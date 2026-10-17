"""Measure *achieved* congestion in a real SUMO run (never configured demand multipliers).

    python tests/congestion_probe.py --scenario rush_hour --seed 42 --warmup 180 \
        --params '{"duration": 5, "demand": 2800, "peak": 2.0}' [--nets DIR] [--out report.json]

A run has a warm-up (outside the observation) and a 300 s observation. Everything is read from SUMO:

* storage capacity  – lane-metres of each corridor / 7.5 m per vehicle (vehicle length + standstill gap)
* occupancy         – vehicles on the corridor's edges / storage capacity, sampled every 5 s
* standing queues   – SUMO halting vehicles; a block is *spilled back* when its queue covers >= 80 % of its length
* insertions        – SUMO departure notifications (not the generator's add calls); pending = SUMO's backlog
* throughput        – SUMO arrival notifications
* stop-and-go       – tripinfo waitingCount (number of separate stops per vehicle)
* spacing           – bumper-to-bumper gaps of consecutive vehicles on the same lane (negative = overlap)
* teleports, and collisions from SUMO's own collision output with junction checking switched on
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

JAM_SPACING = 7.5  # m per vehicle per lane at standstill (4.5 m car + ~2.5 m minGap + margin)


@contextmanager
def audited_sumo(work: Path):
    """Independent safety/accounting outputs, applied to the engine's own SUMO command line."""
    from flowstate.sim import engine as module

    targets = [module.traci] + ([module.libsumo] if module.libsumo else [])
    originals = [(t, t.start) for t in targets]

    def wrap(original):
        def start(cmd, *a, **kw):
            cmd = list(cmd)
            if "--collision.check-junctions" in cmd:
                cmd[cmd.index("--collision.check-junctions") + 1] = "true"
            cmd += ["--collision-output", str(work / "collisions.xml"), "--tripinfo-output", str(work / "trips.xml"),
                    "--tripinfo-output.write-unfinished", "true"]
            return original(cmd, *a, **kw)
        return start

    try:
        for t, o in originals:
            t.start = wrap(o)
        yield
    finally:
        for t, o in originals:
            t.start = o


def corridors(net) -> dict[str, list[str]]:
    """Street name -> its street/entry edges (main + bay parts)."""
    out: dict[str, list[str]] = defaultdict(list)
    for e in net.edges.values():
        if e.kind in ("street", "entry") and e.street:  # interior blocks and the inbound fringe stubs (exit stubs never queue)
            out[e.street].append(e.id)
    return out


def storage(net, edges: list[str]) -> float:
    return sum(net.edges[e].length * len(net.car_lanes(e)) for e in edges) / JAM_SPACING


def measure(scenario: str = "rush_hour", params: dict | None = None, seed: int = 42, warmup: float = 180.0, nets: Path | None = None,
            sample: float = 5.0, label: str = "probe", signals: str | None = None) -> dict:
    from flowstate.api.service import NETWORKS_DIR, WORK_DIR
    from flowstate.scenarios.catalog import create
    from flowstate.sim.engine import Engine, RunConfig

    params = {"duration": 5, **(params or {})}
    sc = create(scenario, params)
    work = WORK_DIR / f"probe_{label}"
    work.mkdir(parents=True, exist_ok=True)
    for f in ("collisions.xml", "trips.xml"):
        (work / f).unlink(missing_ok=True)
    eng = Engine(sc, RunConfig(scenario, sc.params, seed, signals or sc.params.get("signals", "fixed"), warmup=warmup, label=label),
                 nets or NETWORKS_DIR, work)
    net = eng.net
    corr = corridors(net)
    cap = {k: storage(net, v) for k, v in corr.items()}
    cap_total = storage(net, [e for e in net.edges if net.edges[e].kind in ("street", "entry")])
    blocks: dict[tuple, list[str]] = defaultdict(list)
    for e in net.edges.values():
        if e.kind in ("street", "entry") and e.street:
            blocks[(e.street, e.dir, e.seg)].append(e.id)
    block_len = {k: max(net.edges[e].length for e in v) for k, v in blocks.items()}
    block_lanes = {k: max(1, max(len(net.car_lanes(e)) for e in v) - (1 if net.edges[v[0]].cls == "arterial" else 0)) for k, v in blocks.items()}

    series: list[dict] = []
    gaps: list[float] = []
    spill_blocks: dict[str, int] = defaultdict(int)
    block_occ: dict[tuple, list[float]] = defaultdict(list)
    chains: list[int] = []
    departed = arrived = 0
    tele_rec: list[dict] = []
    counters0 = None
    with audited_sumo(work):
        eng.start()
        T = eng.T
        last = -1e9
        try:
            while not eng.finished:
                eng.step()
                if eng.t >= 0:
                    if counters0 is None:
                        counters0 = dict(tele=eng.teleports, offered=getattr(eng.demand, 'offered', 0.0), deferred=getattr(eng.demand, 'deferred', 0.0), submitted=eng.demand.counter,
                                         metered=getattr(eng.demand, "metered", 0))
                    for vid in T.simulation.getStartingTeleportIDList():
                        try:
                            tele_rec.append({"t": round(eng.t, 1), "vehicle": vid, "type": T.vehicle.getTypeID(vid), "lane": T.vehicle.getLaneID(vid),
                                             "waited": round(T.vehicle.getWaitingTime(vid)), "route": list(T.vehicle.getRoute(vid))[:3]})
                        except Exception:  # noqa: BLE001
                            tele_rec.append({"t": round(eng.t, 1), "vehicle": vid})
                    departed += T.simulation.getDepartedNumber()
                    arrived += T.simulation.getArrivedNumber()
                if eng.t >= 0 and eng.t - last >= sample - 1e-6:
                    last = eng.t
                    row: dict = {"t": round(eng.t, 1), "active": T.vehicle.getIDCount(), "pending": len(T.simulation.getPendingVehicles())}
                    per: dict[str, tuple[int, int]] = {}
                    for name, edges in corr.items():
                        n = h = 0
                        for e in edges:
                            n += T.edge.getLastStepVehicleNumber(e)
                            h += T.edge.getLastStepHaltingNumber(e)
                        per[name] = (n, h)
                    row["occ"] = {k: round(v[0] / cap[k], 3) for k, v in per.items() if cap[k]}
                    row["halt"] = {k: round(v[1] / cap[k], 3) for k, v in per.items() if cap[k]}
                    row["occ"]["district"] = round(sum(v[0] for v in per.values()) / cap_total, 3)
                    row["halt"]["district"] = round(sum(v[1] for v in per.values()) / cap_total, 3)
                    spilled = {}
                    for key, edges in blocks.items():
                        block_occ[key].append(sum(T.edge.getLastStepVehicleNumber(e) for e in edges) * JAM_SPACING / (block_len[key] * block_lanes[key]))
                        halting = sum(T.edge.getLastStepHaltingNumber(e) for e in edges)
                        if halting * JAM_SPACING / block_lanes[key] >= 0.8 * block_len[key]:
                            spilled[key] = True
                            spill_blocks[f"{key[0]}:{key[1]}"] += 1
                    row["spilled"] = len(spilled)
                    # longest run of consecutive spilled blocks on one street direction
                    run = 0
                    for (street, d, seg) in {k for k in spilled}:
                        n = 1
                        while (street, d, seg + n) in spilled:
                            n += 1
                        run = max(run, n)
                    row["spillChain"] = run
                    chains.append(run)
                    speeds = [r[3] for r in eng.vehicles.values()]
                    row["meanSpeed"] = round(st.mean(speeds) * 3.6, 1) if speeds else 0
                    row["stopped"] = sum(1 for s in speeds if s < 0.1)
                    series.append(row)
                    for e in net.edges:
                        if net.edges[e].kind not in ("street", "entry", "exit"):
                            continue
                        for lane in net.car_lanes(e):
                            ids = T.lane.getLastStepVehicleIDs(lane)
                            if len(ids) < 2:
                                continue
                            pos = sorted((T.vehicle.getLanePosition(v), T.vehicle.getLength(v), T.vehicle.getLateralLanePosition(v), T.vehicle.getWidth(v)) for v in ids)
                            for (p0, l0, y0, w0), (p1, l1, y1, w1) in zip(pos, pos[1:]):
                                if abs(y1 - y0) < (w0 + w1) / 2:  # side-by-side sublane neighbours are not leader/follower pairs
                                    gaps.append(p1 - l1 - p0)
            results = eng.results()
        finally:
            dem = eng.demand
            eng.close()
    # SUMO's own records
    coll = [dict(c.attrib) for c in ET.parse(work / "collisions.xml").getroot()] if (work / "collisions.xml").exists() else []
    coll_obs = [c for c in coll if float(c["time"]) - warmup >= 0]
    trips = [e for e in ET.parse(work / "trips.xml").getroot() if e.tag == "tripinfo"] if (work / "trips.xml").exists() else []
    obs_trips = [t for t in trips if float(t.get("depart", -1)) >= warmup and t.get("id", "").startswith("v")]
    wc = [int(t.get("waitingCount", 0)) for t in obs_trips if float(t.get("duration", 0)) > 20]
    insertion_delay = [float(t.get("departDelay", 0)) for t in obs_trips]
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    key_corr = ["district"] + [k for k in ("main", "king", "queen", "church", "front", "elm", "park", "college") if k in cap]

    def stat(field, name):
        v = [r[field][name] for r in series if name in r[field]]
        return {"mean": round(st.mean(v), 3), "p95": round(q(v, 0.95), 3), "max": round(max(v), 3)} if v else None

    return {
        "scenario": scenario, "params": sc.params, "seed": seed, "warmup": warmup, "observation": sc.duration,
        "storageCapacityVehicles": {k: round(cap[k]) for k in key_corr if k != "district"} | {"district": round(cap_total)},
        "corridorOccupancy": {k: stat("occ", k) for k in key_corr},
        "corridorHaltedShare": {k: stat("halt", k) for k in key_corr},
        "activeVehicles": {"mean": round(st.mean(r["active"] for r in series), 1), "peak": max(r["active"] for r in series), "min": min(r["active"] for r in series)},
        "blockOccupancy": {f"{k[0]}:{k[1]}{k[2]}": round(st.mean(v), 2) for k, v in sorted(block_occ.items(), key=lambda kv: -st.mean(kv[1]))[:14]},
        "stationarity": {"activeFirst60s": round(st.mean(r["active"] for r in series if r["t"] <= 60), 0), "activeLast60s": round(st.mean(r["active"] for r in series if r["t"] >= sc.duration - 60), 0),
                         "districtOccFirst60s": round(st.mean(r["occ"]["district"] for r in series if r["t"] <= 60), 3), "districtOccLast60s": round(st.mean(r["occ"]["district"] for r in series if r["t"] >= sc.duration - 60), 3)},
        "stoppedVehicles": {"mean": round(st.mean(r["stopped"] for r in series), 1), "peak": max(r["stopped"] for r in series)},
        "meanSpeedKmh": {"mean": round(st.mean(r["meanSpeed"] for r in series), 1), "min": min(r["meanSpeed"] for r in series)},
        "spilledBlocks": {"mean": round(st.mean(r["spilled"] for r in series), 1), "peak": max(r["spilled"] for r in series), "byStreet": dict(sorted(spill_blocks.items(), key=lambda kv: -kv[1])[:6])},
        "spillChainBlocks": {"max": max(chains), "samplesWith2plus": sum(c >= 2 for c in chains), "samples": len(chains)},
        "insertions": {"departedPerHour": round(departed * 3600 / sc.duration), "pendingMean": round(st.mean(r["pending"] for r in series), 1),
                       "pendingMax": max(r["pending"] for r in series), "meanInsertionDelayS": round(st.mean(insertion_delay), 1) if insertion_delay else None,
                       "maxInsertionDelayS": max(insertion_delay, default=None)},
        "throughputPerHour": round(arrived * 3600 / sc.duration),
        "offeredPerHour": round((getattr(dem, 'offered', 0.0) - counters0["offered"]) * 3600 / sc.duration),
        "submittedPerHour": round((dem.counter - counters0["submitted"]) * 3600 / sc.duration),
        "throttleDeferredPerHour": round((getattr(dem, 'deferred', 0.0) - counters0["deferred"]) * 3600 / sc.duration),
        "stopsPerVehicle": {"mean": round(st.mean(wc), 2) if wc else None, "p90": q(wc, 0.9), "n": len(wc)},
        "standingGapM": {"min": round(min(gaps), 2) if gaps else None, "p1": round(q(gaps, 0.01), 2) if gaps else None, "p5": round(q(gaps, 0.05), 2) if gaps else None,
                         "overlapsBelowMinus0_3": sum(g < -0.3 for g in gaps), "samples": len(gaps)},
        "teleports": eng.teleports - counters0["tele"], "replanned": getattr(dem, "replanned", 0),
        "teleportRecords": tele_rec,
        "collisionRecords": [{"t": round(float(c["time"]) - warmup, 1), "type": c.get("type"), "lane": c.get("lane"), "pos": c.get("pos"), "colliderType": c.get("colliderType"),
                              "colliderSpeed": c.get("colliderSpeed"), "victimType": c.get("victimType"), "victimSpeed": c.get("victimSpeed")} for c in coll_obs],
        "collisions": {"observationRecords": len(coll_obs), "byType": {t: sum(c.get("type") == t for c in coll_obs) for t in {c.get("type") for c in coll_obs}},
                       "uniquePairs": len({tuple(sorted((c["collider"], c["victim"]))) for c in coll_obs}), "engineCounter": results["collisions"]},
        "timeLossCarS": (results["metrics"]["byMode"].get("car") or {}).get("timeLoss"),
        "series": series,
    }


def brief(r: dict) -> str:
    occ = {k: (v["mean"], v["max"]) for k, v in r["corridorOccupancy"].items() if v}
    return (f"active mean {r['activeVehicles']['mean']} peak {r['activeVehicles']['peak']} | occ(mean,max) " +
            " ".join(f"{k.split()[0]} {m:.2f}/{x:.2f}" for k, (m, x) in occ.items()) +
            f" | halted-active {r['stoppedVehicles']['mean']} | spilled blocks mean {r['spilledBlocks']['mean']} max chain {r['spillChainBlocks']['max']} | "
            f"depart/h {r['insertions']['departedPerHour']} offered/h {r['offeredPerHour']} thru/h {r['throughputPerHour']} pending max {r['insertions']['pendingMax']} | "
            f"stops/veh {r['stopsPerVehicle']['mean']} gap min {r['standingGapM']['min']} | tele {r['teleports']} coll {r['collisions']['observationRecords']} {r['collisions']['byType']} | speed {r['meanSpeedKmh']['mean']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="rush_hour")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--warmup", type=float, default=180.0)
    ap.add_argument("--params", default="{}")
    ap.add_argument("--nets", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--demand-attrs", default="{}", help="JSON overrides of DemandGenerator class attributes (for sweeps)")
    a = ap.parse_args(argv)
    from flowstate.sim.demand import DemandGenerator
    for k, v in json.loads(a.demand_attrs).items():
        assert hasattr(DemandGenerator, k) or k in DemandGenerator.DENSE, k
        if k in DemandGenerator.DENSE:  # dense rush-hour settings override the class attribute per instance
            DemandGenerator.DENSE[k] = v
        else:
            setattr(DemandGenerator, k, v)
    label = a.label or f"{a.scenario}{a.seed}"
    r = measure(a.scenario, json.loads(a.params), a.seed, a.warmup, Path(a.nets) if a.nets else None, label=label)
    print("PROBE " + brief(r))
    if a.out:
        Path(a.out).write_text(json.dumps(r), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
