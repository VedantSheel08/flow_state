"""Headless, reproducible runs of one condition and seed, with the contract's measurements and classification.

    python -m flowstate.evaluation run --condition baseline_severe --seed 42 --out reports/eval
    python -m flowstate.evaluation matrix --conditions severe gridlock --seeds 7 42 99 --jobs 3 --out reports/eval

One SUMO simulation per process (libsumo allows one), so ``matrix`` starts one process per run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import conditions as C
from .classify import CRITERIA_VERSION, OBSERVATION_S, classify
from .collector import Collector
from .zone import build_zone, interior_arterials, interior_streets

REPO = Path(__file__).resolve().parents[3]


def _peak_rss_mb() -> int | None:
    """Peak resident memory of this process (standard library only: /proc on Linux, the Win32 API on Windows)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return None
            return round(c.PeakWorkingSetSize / 2**20)
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return round(int(line.split()[1]) / 1024)
    except Exception:  # noqa: BLE001
        return None
    return None


def _resources(step_ms: list[float], cpu0: float, wall: float) -> dict[str, Any]:
    """Step-time distribution, CPU and peak memory of this one simulation process (engine + collector, no web stack)."""
    srt = sorted(step_ms)
    q = lambda p: round(srt[min(len(srt) - 1, int(p * len(srt)))], 2) if srt else None  # noqa: E731
    used = time.process_time() - cpu0
    return {"steps": len(srt), "stepMs": {"mean": round(sum(srt) / len(srt), 2) if srt else None, "p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "max": round(srt[-1], 2) if srt else None},
            "cpuSeconds": round(used, 1), "cpuCoresAverage": round(used / wall, 2) if wall else None, "peakRssMB": _peak_rss_mb()}


def recovery_summary(col, cond) -> dict[str, Any] | None:
    """Plateau versus recovery for a condition that relieves its restriction (outlets.relieve_at): minute-by-minute arrivals, halted share
    and accumulation after the relief, from the same per-second series the classifier uses."""
    at = cond.outlets.get("relieve_at")
    if at is None:
        return None
    rows = col.rows
    def window(a: float, b: float) -> list[dict[str, Any]]:
        return [r for r in rows if a <= r["t"] < b]
    def block(a: float, b: float) -> dict[str, Any]:
        w = window(a, b)
        if not w:
            return {}
        arr = (w[-1].get("arrivedCum") or 0) - (w[0].get("arrivedCum") or 0)
        return {"from": a, "to": b, "arrivalsPerMin": round(arr * 60.0 / max(1.0, b - a), 1), "haltedShare": round(sum(r["halted"] for r in w) / len(w), 3),
                "active": round(sum(r.get("active", 0) for r in w) / len(w)), "density": round(sum(r["density"] for r in w) / len(w), 1)}
    end = rows[-1]["t"] if rows else at
    return {"reliefAt": at, "plateau": block(at - 120, at), "after": [block(at + i, min(end, at + i + 120)) for i in range(0, int(max(0, end - at)), 120)]}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def declared_zone(net, junctions: list[str]):
    """Blocks that start or end at a declared junction (approaches and the blocks between declared junctions)."""
    arterial = interior_streets(net)
    keys = {k for k, b in arterial.blocks.items() if b.to_node in junctions or b.from_node in junctions}
    edges = [e for k in keys for e in arterial.blocks[k].edges]
    zone = build_zone(net, "declared", ("street",), None, f"blocks adjacent to {','.join(junctions)}")
    zone.edges = edges
    zone.lanes = [l for e in edges for l in net.car_lanes(e)]
    zone.lane_km = sum(net.lanes[l].length for l in zone.lanes) / 1000.0
    zone.blocks = {k: b for k, b in zone.blocks.items() if k in keys}
    return zone, keys


def signal_cycle_s(net) -> float:
    return max((sum(d for d, _s in prog) for prog in net.tls_programs.values()), default=90.0)


def run_condition(cond: C.Condition, seed: int, out_dir: Path, *, reference: bool = False, progress: bool = True, on_step=None) -> dict[str, Any]:
    """Run one condition and seed. ``on_step(engine, collector)`` is called after every step, so a controller (for
    example a signal policy under evaluation) can act and read ``schema.observation`` while the demand, seeds and
    measurements stay identical to the baseline run it is compared with."""
    from ..api.service import NETWORKS_DIR, WORK_DIR
    from ..performance.identity import identity
    from ..scenarios.catalog import create
    from ..sim.engine import Engine, RunConfig

    params = {"duration": cond.observation / 60.0, **cond.params}
    demand_overrides = {**cond.demand_overrides}
    sumo_options = {**cond.sumo_options}
    if reference and cond.reference:
        demand_overrides.update(cond.reference.get("demand_overrides", {}))
        sumo_options.update(cond.reference.get("sumo_options", {}))
        params.update(cond.reference.get("params", {}))
    sc = create(cond.scenario, params)
    tag = f"{cond.id}{'-ref' if reference else ''}-{seed}"
    work = WORK_DIR / "eval" / tag
    work.mkdir(parents=True, exist_ok=True)
    for f in ("collisions.xml", "sumo-errors.log"):
        (work / f).unlink(missing_ok=True)
    cfg = RunConfig(cond.scenario, sc.params, seed, cond.signal_mode, signal_program=cond.signal_program, warmup=cond.warmup, label=tag, sumo_options=sumo_options, demand_overrides=demand_overrides, box_guard=cond.box_guard, vtype_overrides=cond.vtype_overrides, outlets={**cond.outlets, **((cond.reference or {}).get('outlets', {}) if reference else {})})
    eng = Engine(sc, cfg, NETWORKS_DIR, work)
    net = eng.net
    primary = interior_arterials(net)
    graph = interior_streets(net)
    dz, declared = (declared_zone(net, cond.declared_junctions) if cond.declared_junctions else (None, set()))
    col = Collector(eng, primary, graph, declared, dz)
    t0 = time.perf_counter()
    eng.start()
    steps0 = 0
    step_ms: list[float] = []  # wall time of one engine step including the collector, per simulated step
    cpu0 = time.process_time()
    try:
        while not eng.finished:
            ts = time.perf_counter()
            eng.step()
            col.after_step()
            step_ms.append((time.perf_counter() - ts) * 1000.0)
            if on_step is not None:
                on_step(eng, col)
            steps0 += 1
            if progress and steps0 % 2000 == 0:
                print(f"[{tag}] t={eng.t:7.1f} active={len(eng.vehicles)} pending={len(eng.T.simulation.getPendingVehicles())} wall={time.perf_counter() - t0:5.0f}s", flush=True)
        results = eng.results()
        pending_now = len(eng.T.simulation.getPendingVehicles())
        active_now = len(eng.vehicles)
    finally:
        wall = time.perf_counter() - t0
        eng.close()
    manifest = identity(cond.scenario, sc.params, seed, NETWORKS_DIR, warmup=cond.warmup)
    manifest["manifest"].pop("codeFiles", None)  # the key above already covers them; keep reports small
    manifest["manifest"].pop("resourceFiles", None)
    guard_summary = eng.guard.summary() if eng.guard else {"enabled": False}
    report = analyze(cond, seed, eng, col, results, work, primary, graph, dz, pending_now, active_now, wall, reference)
    report["manifest"] = {
        "identityKey": manifest["key"], "git": {"commit": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain")), "branch": _git("rev-parse", "--abbrev-ref", "HEAD")},
        "condition": cond.to_dict(), "seed": seed, "reference": reference, "sumoCommand": eng.sumo_command[:2] + ["..."] + [a for a in eng.sumo_command[4:]],
        "sumo": manifest["manifest"]["sumo"], "backend": manifest["manifest"]["backend"], "dependencies": manifest["manifest"]["dependencies"],
        "platform": manifest["manifest"]["platform"], "criteriaVersion": CRITERIA_VERSION,
        "effectiveDemandSettings": {k: getattr(eng.demand, k, None) for k in ("GATE_FRACTION", "MAX_PENDING", "PENDING_PER_GATEWAY", "PATH_GATE", "THROTTLE_SLOPE", "MIN_THROTTLE", "SPILL_FRACTION", "dense")},
    }
    report["boxGuard"] = guard_summary
    report["resources"] = _resources(step_ms, cpu0, wall)
    report["recovery"] = recovery_summary(col, cond)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{tag}.json").write_text(json.dumps(report, separators=(",", ":"), default=str), encoding="utf-8")
    return report


def _collisions(work: Path) -> list[dict[str, Any]]:
    path = work / "collisions.xml"
    if not path.exists():
        return []
    try:
        return [dict(c.attrib) for c in ET.parse(path).getroot()]
    except ET.ParseError:
        return []


def _run_lengths(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def analyze(cond, seed, eng, col: Collector, results: dict[str, Any], work: Path, primary, graph, dz, pending_now: int, active_now: int, wall: float, reference: bool) -> dict[str, Any]:
    import statistics

    net = eng.net
    obs = [r for r in col.rows if 0 <= r["t"] < OBSERVATION_S]
    cycle = signal_cycle_s(net)
    warm_tail = [r for r in col.rows if -2 * cycle <= r["t"] < 0]
    idx0 = next((i for i, r in enumerate(col.rows) if r["t"] >= 0), 0)
    n_obs = len(obs)

    def block_series(key):
        st = col.block_stats[key]
        return st.extent[idx0:idx0 + n_obs], st.vehicles[idx0:idx0 + n_obs]

    # queues on arterial approaches
    queue_blocks = []
    block_summary: dict[str, Any] = {}
    for key, b in graph.blocks.items():
        ext, veh = block_series(key)
        if not ext:
            continue
        block_summary[f"{key[0]}:{key[1]}{key[2]}"] = {
            "lengthM": round(b.length), "maxQueueM": max(ext), "meanQueueM": round(statistics.fmean(ext), 1), "maxQueueVehicles": max(veh),
            "secondsOver75pct": sum(1 for e in ext if e >= 0.75 * b.length), "secondsSpillbackCandidate": sum(1 for e in ext if e >= b.length - 10),
        }
        if key in primary.blocks and max(ext) >= 50 and max(veh) >= 8:
            queue_blocks.append(key)

    # corridor spillback with receiving-lane blockage evidence
    nodes_of = {k: (b.from_node, b.to_node) for k, b in graph.blocks.items()}
    corridor_pairs = [(a, b) for a in graph.blocks for b in graph.blocks if a != b and a[0] == b[0] and a[1] == b[1] and nodes_of[a][1] == nodes_of[b][0]]
    spill_seconds = 0
    gb = col.gb_arcs[idx0:idx0 + n_obs]
    ext_of = {k: block_series(k)[0] for k in graph.blocks}
    for i in range(n_obs):
        gset = set(gb[i]) if i < len(gb) else set()
        for a, b in corridor_pairs:
            ea, eb = ext_of[a], ext_of[b]
            if i < len(ea) and i < len(eb) and ea[i] >= 0.75 * graph.blocks[a].length and eb[i] >= 0.75 * graph.blocks[b].length and (a, b) in gset:
                spill_seconds += 1
                break

    # stop-and-go (inside the observation only)
    s0, g0 = col.snap0 if col.snap0 else ({}, {})
    stopgo = sum(1 for v in set(col.stops) | set(col.starts) if col.stops.get(v, 0) - s0.get(v, 0) >= 2 and col.starts.get(v, 0) - g0.get(v, 0) >= 2)

    # blocked cycles
    cyc_rows = col.cycle_seconds[idx0:idx0 + n_obs]
    flags = [bool(r["qualifying"]) for r in cyc_rows]
    longest = _run_lengths(flags)
    last_q = next((r["qualifying"] for r in reversed(cyc_rows) if r["qualifying"]), None)
    gb_junction: dict[str, int] = {}
    for arcs in gb:
        for a, b in arcs:
            n = graph.blocks[a].to_node
            gb_junction[n] = gb_junction.get(n, 0) + 1
    cycle_j = last_q["junctions"] if last_q else []
    evidence = bool(cycle_j) and all(gb_junction.get(j, 0) >= 10 for j in cycle_j)

    # declared-zone flow
    out_obs = sum(col.declared_outflow[idx0:idx0 + n_obs]) if col.declared_outflow else None
    in_obs = sum(col.declared_inflow[idx0:idx0 + n_obs]) if col.declared_inflow else None

    dz_rows = [dict(r["dz"], t=r["t"]) for r in obs if "dz" in r]
    first = [r["density"] for r in obs if r["t"] < 60]
    last = [r["density"] for r in obs if r["t"] >= OBSERVATION_S - 60]
    safety = results["safety"]
    coll = _collisions(work)
    person = [c for c in coll if "person" in (c.get("type", "") + c.get("victimType", "") + c.get("colliderType", "")).lower() or "walker" in (c.get("victimType", "") + c.get("colliderType", "")).lower()]

    by_group: dict[str, Any] = {}
    for g, km in col.group_km.items():
        n = [r.get(g, [0, 0]) for r in col.group_rows[idx0:idx0 + n_obs]]
        if n:
            by_group[g] = {"laneKm": round(km, 3), "meanVehicles": round(statistics.fmean(x[0] for x in n), 1), "densityVehPerKmLane": round(statistics.fmean(x[0] for x in n) / km, 1) if km else None,
                           "haltedShare": round(sum(x[1] for x in n) / max(1, sum(x[0] for x in n)), 3)}
    ledger = ledger_of(eng, col, pending_now, active_now, work)
    ttt = (cond.sumo_options.get("--time-to-teleport") if not reference else None) or "240"
    m = {
        "rows": obs, "dzRows": dz_rows, "queueApproaches": len(queue_blocks), "stopGoVehicles": stopgo, "corridorSpillSeconds": spill_seconds,
        "firstMinuteDensity": round(statistics.fmean(first), 1) if first else None, "lastMinuteDensity": round(statistics.fmean(last), 1) if last else None,
        "cycleSeconds": longest, "cycleJunctions": cycle_j, "cycleStreets": last_q["streets"] if last_q else [], "greenBlockedEvidence": evidence,
        "greenBlockedJunctions": {j: gb_junction.get(j, 0) for j in cycle_j}, "outflowRatio": None,
        "rescueDisabled": ttt in ("-1", "0"), "timeToTeleport": ttt,
        "collisionRecords": safety["collisionRecordObservations"], "collisionEpisodes": safety["collisionEpisodes"], "collisionPairs": safety["uniqueCollisionPairs"],
        "invalidRoutes": col.invalid_routes, "ledgerResidual": ledger["residual"], "teleports": eng.teleports,
        "startDensity": {"atZero": round(obs[0]["density"], 1) if obs else 0.0, "minTwoCycles": round(min((r["density"] for r in warm_tail), default=0.0), 1)},
    }
    travel = col.travel_speeds_kmh("primary", 0, OBSERVATION_S)
    travel_dz = col.travel_speeds_kmh("declared", 0, OBSERVATION_S) if dz else []
    m["travelP50"] = _q(travel, 0.5)
    m["travelSamples"] = len(travel)
    m["travelP50Declared"] = _q(travel_dz, 0.5)
    m["travelSamplesDeclared"] = len(travel_dz)
    cls = classify(m, cond.requested)
    dens = [r["density"] for r in obs]
    summary = {
        "arterialLaneKm": round(primary.lane_km, 3), "declaredLaneKm": round(dz.lane_km, 3) if dz else None,
        "density": {"mean": round(statistics.fmean(dens), 1), "min": round(min(dens), 1), "max": round(max(dens), 1)} if dens else None,
        "storage": round(statistics.fmean(r["storage"] for r in obs), 3) if obs else None, "body": round(statistics.fmean(r["body"] for r in obs), 3) if obs else None,
        "speedKmh": {"p10": _med(r["p10"] for r in obs), "p50": _med(r["p50"] for r in obs), "p90": _med(r["p90"] for r in obs)},
        "travelSpeedKmh": {"n": len(travel), "p10": _q(travel, 0.1), "p50": _q(travel, 0.5), "p90": _q(travel, 0.9),
                           "note": "per-vehicle average speed through the interior arterials (trips >= 20 s), see docs/TRAFFIC_REALISM.md"},
        "haltedShare": round(statistics.fmean(r["halted"] for r in obs), 3) if obs else None,
        "activeVehicles": {"mean": round(statistics.fmean(r["active"] for r in obs)) if obs else None, "max": max((r["active"] for r in obs), default=None)},
        "pendingMax": max((r["pending"] for r in obs), default=None),
        "queueApproaches": m["queueApproaches"], "stopGoVehicles": stopgo, "corridorSpillSeconds": spill_seconds, "longestBlockedCycleSeconds": longest,
        "declaredZoneOutflowVehicles": out_obs, "declaredZoneInflowVehicles": in_obs,
        "standstillGapM": {"samples": len(col.gap_samples), "median": _q(col.gap_samples, 0.5), "p5": _q(col.gap_samples, 0.05), "min": min(col.gap_samples, default=None),
                           "shareIn2to5m": round(sum(1 for g in col.gap_samples if 2.0 <= g <= 5.0) / len(col.gap_samples), 3) if col.gap_samples else None,
                           "negative": sum(1 for g in col.gap_samples if g < 0)},
        "throughputPerHour": round((col.native["arrived"] - ledger["_arrivedAt0"]) * 3600 / OBSERVATION_S) if ledger.get("_arrivedAt0") is not None else None,
        "wallSeconds": round(wall, 1), "simSecondsPerWallSecond": round((cond.warmup + OBSERVATION_S) / wall, 2) if wall else None,
    }
    ledger.pop("_arrivedAt0", None)
    return {
        "condition": cond.id, "seed": seed, "reference": reference, "summary": summary, "classification": cls, "ledger": ledger,
        "safety": {"native": safety, "collisionFile": coll[:60], "personInvolved": len(person), "teleports": eng.teleports, "invalidRoutes": col.invalid_routes,
                   "routeSamples": col.route_samples, "gapOutliers": col.gap_outliers},
        "waitForensics": col.wait_forensics, "forensics": col.forensics, "teleportForensics": col.tele_forensics, "byGroup": by_group, "blocks": block_summary, "cycles": {"longestSeconds": longest, "last": last_q}, "greenBlockedByJunction": gb_junction,
        "series": col.summary_series(), "declaredFlow": {"outflow": col.declared_outflow, "inflow": col.declared_inflow} if col.declared else None,
    }


def ledger_of(eng, col: Collector, pending_now: int, active_now: int, work: Path) -> dict[str, Any]:
    """Integer demand and vehicle accounting for the whole run (warm-up included) and the observation window.

    background: requests = submitted + dropped          (the generator holds nothing back: a refused attempt draws again)
    native:     added    = departed + pending + discarded
    ``discarded`` is read from SUMO's own log when present; otherwise it is the difference, and is reported as derived.
    """
    d = eng.demand
    discarded_log = 0
    log = work / "sumo-errors.log"
    if log.exists():
        for line in log.read_text(errors="ignore").splitlines():
            low = line.lower()
            if "discarded" in low or "depart delay" in low or "max-depart-delay" in low or "removed from the insertion" in low:
                discarded_log += 1
    discarded_derived = eng.added - col.native["departed"] - pending_now
    held = len(d.held)
    waits = sorted(d.hold_waits)
    background = {"requests": d.requests, "submitted": d.submitted, "dropped": d.dropped, "cancelled": d.cancelled, "heldAtEnd": held, "cruisingTrips": d.cruised,
                  "residual": d.requests - d.submitted - d.dropped - d.cancelled - held,
                  "holdWaitSeconds": {"n": len(waits), "mean": round(sum(waits) / len(waits), 1) if waits else None, "p90": round(waits[int(0.9 * (len(waits) - 1))], 1) if waits else None, "max": round(waits[-1], 1) if waits else None},
                  "metoredAttempts": d.metered, "expectedDeferred": round(d.deferred, 1), "expectedOffered": round(d.offered, 1)}
    native = {"added": eng.added, "departed": col.native["departed"], "arrived": col.native["arrived"], "pendingNow": pending_now, "activeNow": active_now,
              "discardedDerived": discarded_derived, "discardedLogged": discarded_log, "teleportsStarted": col.native["teleport_started"], "teleportsEnded": col.native["teleport_ended"]}
    residual = abs(background["residual"]) + (abs(discarded_derived - discarded_log) if discarded_log or discarded_derived else 0)
    arrived0 = next((r.get("arrivedCum") for r in col.rows if r["t"] >= 0), None)
    return {"background": background, "native": native, "residual": residual, "_arrivedAt0": arrived0,
            "note": "discardedDerived is added - departed - pending; it must equal what SUMO logged. Non-zero residual = unexplained vehicles."}


def _q(values, p):
    return round(sorted(values)[min(len(values) - 1, int(p * len(values)))], 2) if values else None


def _med(it):
    import statistics

    v = [x for x in it if x is not None]
    return round(statistics.median(v), 1) if v else None


# ---------------------------------------------------------------------- CLI
def attach_reference(locked: dict[str, Any], ref: dict[str, Any]) -> None:
    """Fill the gridlock outflow gate: declared-zone outflow of the locked run vs its matched non-locked reference."""
    lo, ro = locked["summary"]["declaredZoneOutflowVehicles"], ref["summary"]["declaredZoneOutflowVehicles"]
    locked["summary"]["referenceOutflowVehicles"] = ro
    locked["summary"]["outflowRatio"] = None if not ro else round((lo or 0) / ro, 3)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m flowstate.evaluation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--condition", required=True)
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--out", default="reports/eval")
    r.add_argument("--reference", action="store_true", help="run the matched non-locked reference of a gridlock condition")
    r.add_argument("--warmup", type=float, default=None)
    m = sub.add_parser("matrix")
    m.add_argument("--conditions", nargs="+", required=True)
    m.add_argument("--seeds", nargs="+", type=int, default=[7, 42, 99])
    m.add_argument("--jobs", type=int, default=3)
    m.add_argument("--out", default="reports/eval")
    m.add_argument("--warmup", type=float, default=None)
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for c in C.CONDITIONS.values():
            print(f"{c.id:20s} requested={c.requested:9s} {c.description}")
        return 0
    if a.cmd == "run":
        cond = C.get(a.condition)
        if a.warmup is not None:
            cond.warmup = a.warmup
        rep = run_condition(cond, a.seed, Path(a.out), reference=a.reference)
        print_summary(rep)
        return 0
    # matrix: one subprocess per run, ``jobs`` at a time
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    todo = [(c, s, ref) for c in a.conditions for s in a.seeds for ref in ([False, True] if C.get(c).reference else [False])]
    running: list[tuple[subprocess.Popen, str]] = []
    env = {**os.environ, "PYTHONPATH": str(REPO / "backend")}
    while todo or running:
        while todo and len(running) < a.jobs:
            c, s, ref = todo.pop(0)
            cmd = [sys.executable, "-m", "flowstate.evaluation", "run", "--condition", c, "--seed", str(s), "--out", str(out)] + (["--reference"] if ref else []) + (["--warmup", str(a.warmup)] if a.warmup is not None else [])
            log = open(out / f"{c}{'-ref' if ref else ''}-{s}.log", "w")
            running.append((subprocess.Popen(cmd, cwd=REPO / "backend", env=env, stdout=log, stderr=subprocess.STDOUT), f"{c}-{s}{'-ref' if ref else ''}"))
        time.sleep(2)
        for p, name in list(running):
            if p.poll() is not None:
                running.remove((p, name))
                print(f"finished {name} exit={p.returncode}", flush=True)
    return 0


def print_summary(rep: dict[str, Any]) -> None:
    s, c = rep["summary"], rep["classification"]
    print(f"\n== {rep['condition']} seed {rep['seed']}{' (reference)' if rep['reference'] else ''}: requested={c['requestedTier']} achieved={c['achievedTier']} -> {c['qualification']}")
    print(f"   density mean {s['density']['mean']} (min {s['density']['min']} max {s['density']['max']}) veh/km/lane over {s['arterialLaneKm']} lane-km | storage {s['storage']} body {s['body']} | speed km/h {s['speedKmh']} halted {s['haltedShare']}")
    print(f"   active {s['activeVehicles']} pending max {s['pendingMax']} | queue approaches {s['queueApproaches']} stop-go vehicles {s['stopGoVehicles']} corridor spill {s['corridorSpillSeconds']}s cycle {s['longestBlockedCycleSeconds']}s | gap median {s['standstillGapM']['median']}")
    print(f"   safety: collisions {rep['safety']['native']['collisionRecordObservations']} (pairs {rep['safety']['native']['uniqueCollisionPairs']}) teleports {rep['safety']['teleports']} routes invalid {rep['safety']['invalidRoutes']} | ledger residual {rep['ledger']['residual']} | wall {s['wallSeconds']}s")
    print("   failed gates:", ", ".join(c["failedGates"]) or "none")


if __name__ == "__main__":
    sys.exit(main())
