"""Record paired replays on the real ``district`` network for several of main's scenarios, plus a small paired evaluation for each.

    python -m flowstate.replay.record --scenarios ambulance,rush_hour,accident,construction --seeds 3100-3107 --out ../frontend/public/flowstate-demo

Arms (same seed, same demand, same events):
* ``left``  = ``fixed`` signals (main's pre-timed plans) with no extra rules;
* ``right`` = the same plans with FlowState's rules: queue-responsive green durations (``heuristic.py``) + ambulance green priority (``priority.py``).
Both are hand-written rules measured by SUMO. No learned model runs on this network (the Intelligence Lab's queue forecaster is grid-specific and is not used).
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

MAIN_TYPES = {"sedan": 0, "suv": 1, "truck": 2, "bus": 3, "bike": 4, "ambulance": 5, "police": 6}
STATUS_MOVING, STATUS_RED, STATUS_QUEUE, STATUS_GREEN = 0, 1, 2, 3
UNIT_KINDS = ("ambulance", "responder")
FRAME_DT = 0.5
DURATION_MIN = 5
SCENARIOS: dict[str, dict[str, Any]] = {
    "ambulance": {"title": "Ambulance dispatch", "params": {"calls": 2}, "tagline": "Calls from the hospital, measured call-to-scene"},
    "rush_hour": {"title": "Rush hour", "params": {}, "tagline": "Peak demand across the network"},
    "accident": {"title": "Collision", "params": {}, "tagline": "A crash blocks lanes; responders are dispatched"},
    "construction": {"title": "Work zone", "params": {}, "tagline": "A closure with a signed detour"},
}


def run(seed: int, arm: str, scenario_id: str = "ambulance", frames: bool = True) -> dict[str, Any]:
    from ..api.service import NETWORKS_DIR, WORK_DIR
    from ..scenarios.catalog import create
    from ..sim.engine import Engine, RunConfig
    from ..sim.vtypes import base_type
    from .heuristic import QueueResponsive
    from .priority import EmergencyPriority

    spec = SCENARIOS.get(scenario_id, {})
    sc = create(scenario_id, {"duration": DURATION_MIN, **spec.get("params", {})})
    eng = Engine(sc, RunConfig(scenario_id, sc.params, seed, "fixed", label=f"replay-{scenario_id}-{arm}-{seed}"), NETWORKS_DIR, WORK_DIR / f"replay-{scenario_id}-{arm}-{seed}")
    eng.start()
    rules: list[Any] = []
    if arm == "flowstate":
        rules = [QueueResponsive(), EmergencyPriority()]
        eng.signals.policies.extend(rules)
    T = eng.T
    tls_ids = sorted(eng.signals.tls)
    ids: dict[str, int] = {}
    out_frames: list[dict[str, Any]] = []
    units: dict[str, dict[str, Any]] = {}
    queue_sum = 0.0
    samples = 0
    last = -1.0
    try:
        while not eng.finished:
            eng.step()
            if eng.t < 0 or eng.t - last < FRAME_DT - 1e-6:
                continue
            last = eng.t
            whole = abs(eng.t - round(eng.t)) < 1e-6
            if whole:
                samples += 1
                queue_sum += sum(eng.junction_queues().values())
            ev: list[list[int]] = []
            for vid, tr in eng.tracked.items():
                if tr.kind not in UNIT_KINDS or vid not in eng.vehicles or tr.status != "en route":
                    continue
                u = units.setdefault(vid, {"vid": vid, "label": tr.label.split(" →")[0], "kind": tr.kind, "call": tr.data.get("call"), "departS": round(eng.t, 1),
                                           "stoppedRedS": 0.0, "stoppedQueueS": 0.0, "first": int(eng.t), "last": int(eng.t)})
                u["last"] = int(eng.t)
                s = eng.vehicles.get(vid)
                speed = s[3] if s else 0.0
                try:
                    nxt = T.vehicle.getNextTLS(vid)
                except Exception:  # noqa: BLE001
                    nxt = []
                dist, state = (nxt[0][2], nxt[0][3]) if nxt else (9999.0, "")
                status = STATUS_MOVING
                if speed < 0.5:
                    if dist < 14 and state in "rRyY":
                        status = STATUS_RED
                        if whole:
                            u["stoppedRedS"] += 1.0
                    else:
                        status = STATUS_QUEUE
                        if whole:
                            u["stoppedQueueS"] += 1.0
                elif state in "Gg" and dist < 120:
                    status = STATUS_GREEN
                ev.append([ids.setdefault(vid, len(ids) + 1), status, int(min(9999.0, dist) * 10)])
            if frames:
                vt = eng.vtype
                v = [[ids.setdefault(vid, len(ids) + 1), int(round(s[0] * 10)), int(round(s[1] * 10)), int(round(s[2])),
                      MAIN_TYPES.get(base_type(vt.get(vid, "sedan")), 0), int(round(s[3] * 10))] for vid, s in eng.vehicles.items()]
                fr: dict[str, Any] = {"t": round(eng.t, 1), "v": v, "s": [eng.tls_state.get(t, "") for t in tls_ids]}
                if ev:
                    fr["e"] = ev
                out_frames.append(fr)
        res = eng.results()
        focus = _focus(eng, res)
    finally:
        eng.close()
    for vid, u in units.items():
        tr = eng.tracked.get(vid)
        rt = tr.data.get("responseTime") if tr else None
        u.update({"travelS": rt, "finished": rt is not None, "freeFlowS": tr.data.get("freeFlow") if tr else None, "id": ids.get(vid)})
    cars = res["metrics"]["byMode"].get("car", {})
    fin = [u for u in units.values() if u["finished"]]
    metrics = {
        "ev_total": len(units), "ev_unfinished": len(units) - len(fin),
        "ev_travel_mean_s": float(np.mean([u["travelS"] for u in fin])) if fin else None,
        "ev_stopped_red_s": float(np.mean([u["stoppedRedS"] for u in units.values()])) if units else None,
        "ev_stopped_queue_s": float(np.mean([u["stoppedQueueS"] for u in units.values()])) if units else None,
        "queue_total_mean_veh": queue_sum / max(1, samples), "throughput_vph": res["metrics"]["completed"] * 3600.0 / (60.0 * DURATION_MIN),
        "teleports": res["teleports"], "collisions": res["collisions"], "car": cars, "junctionMaxQueue": res["metrics"].get("junctionMaxQueue", {}),
        "rules": [r.summary() for r in rules] or None, "scenario": res.get("scenario"),
    }
    return {"seed": seed, "arm": arm, "scenario": scenario_id, "network": eng.network_id, "controller": "fixed_time" if arm == "fixed_time" else "flowstate_rules", "tlsIds": tls_ids,
            "frames": out_frames, "units": list(units.values()), "metrics": metrics, "focus": focus}


def _focus(eng: Any, res: dict[str, Any]) -> dict[str, Any]:
    """Where the camera should look when no unit is chosen: the incident if there is one, else the busiest junction."""
    try:
        inc = next(iter(eng.incidents.values()), None)
        if inc is not None:
            x, y, _ = eng.edge_point(inc.edge, getattr(inc, "pos", 20.0))
            return {"kind": "point", "x": round(x, 1), "y": round(y, 1), "label": getattr(inc, "kind", "incident")}
    except Exception:  # noqa: BLE001
        pass
    jq = res["metrics"].get("junctionMaxQueue") or {}
    if jq:
        jid = max(jq, key=jq.get)
        pos = eng.net.node_pos.get(jid)
        if pos:
            return {"kind": "junction", "x": round(pos[0], 1), "y": round(pos[1], 1), "label": eng.junction_name(jid), "id": jid}
    return {"kind": "network"}


def _job(args: tuple[str, int, bool]) -> dict[str, Any]:
    scenario_id, seed, frames = args
    return {"seed": seed, "left": run(seed, "fixed_time", scenario_id, frames), "right": run(seed, "flowstate", scenario_id, frames)}


def car_metric(m: dict[str, Any], key: str) -> float | None:
    v = m.get("car", {}).get(key)
    return float(v) if v is not None else None


AGG = [("ev_travel_mean_s", True), ("ev_stopped_red_s", True), ("ev_stopped_queue_s", True), ("queue_total_mean_veh", True), ("throughput_vph", False), ("teleports", True)]
HIGHER_CAR = ("speed", "trips")


def paired(pairs: list[dict[str, Any]], car_keys: list[str], rng_seed: int = 0) -> dict[str, Any]:
    rng = np.random.default_rng(rng_seed)
    out: dict[str, Any] = {}
    keys = list(AGG) + [(f"car_{k}", k not in HIGHER_CAR) for k in car_keys]
    for key, lower in keys:
        a, b = [], []
        for p in pairs:
            lm, rm = p["left"]["metrics"], p["right"]["metrics"]
            x = car_metric(lm, key[4:]) if key.startswith("car_") else lm.get(key)
            y = car_metric(rm, key[4:]) if key.startswith("car_") else rm.get(key)
            if x is None or y is None:
                continue
            a.append(x)
            b.append(y)
        if not a:
            continue
        a_, b_ = np.array(a, dtype=float), np.array(b, dtype=float)
        d = b_ - a_
        boots = rng.choice(d, size=(4000, len(d)), replace=True).mean(axis=1)
        sign = -1 if lower else 1
        out[key] = {"left_mean": round(float(a_.mean()), 3), "right_mean": round(float(b_.mean()), 3), "mean_diff": round(float(d.mean()), 3),
                    "pct": round(100 * float(d.mean()) / float(a_.mean()), 2) if a_.mean() else None,
                    "ci95": [round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)], "pairs": len(d),
                    "better": int((d * sign > 1e-9).sum()), "worse": int((d * sign < -1e-9).sum())}
    return out


BEST_CASE_STOP_S = 3.0
MIN_QUEUE_S = 10.0
KPIS = {
    "ambulance": [("ev_travel_mean_s", "Call-to-scene time", "s"), ("ev_stopped_queue_s", "Units stuck in queue", "s"), ("car_waiting", "Other vehicles’ wait", "s"), ("queue_total_mean_veh", "Vehicles queued", "")],
    "default": [("car_waiting", "Mean wait per vehicle", "s"), ("car_timeLoss", "Time lost per vehicle", "s"), ("queue_total_mean_veh", "Vehicles queued", ""), ("throughput_vph", "Throughput", " veh/h")],
}


def asset(scenario_id: str, pair: dict[str, Any], agg: dict[str, Any], seeds: list[int]) -> dict[str, Any]:
    L, R = pair["left"], pair["right"]
    lu = {u["vid"]: u for u in L["units"]}
    ru = {u["vid"]: u for u in R["units"]}
    units = []
    for vid in sorted(set(lu) & set(ru), key=lambda v: lu[v]["departS"]):
        l, r = lu[vid], ru[vid]
        side = lambda u: {k: u[k] for k in ("travelS", "stoppedRedS", "stoppedQueueS", "finished", "departS", "first", "last", "id")}
        units.append({"id": l["id"], "label": l["label"], "kind": l["kind"], "left": side(l), "right": side(r),
                      "savedS": round(l["travelS"] - r["travelS"], 1) if l["finished"] and r["finished"] else None})
    usable = [u for u in units if u["savedS"] is not None]
    best = [u for u in usable if u["right"]["stoppedRedS"] + u["right"]["stoppedQueueS"] <= BEST_CASE_STOP_S and u["savedS"] > 0 and u["left"]["stoppedQueueS"] >= MIN_QUEUE_S]
    example = max(best or usable, key=lambda u: u["savedS"])["id"] if usable else None

    def totals(run_: dict[str, Any]) -> dict[str, Any]:
        m = run_["metrics"]
        return {k: m.get(k) for k in ("ev_total", "ev_unfinished", "ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s", "queue_total_mean_veh", "throughput_vph", "teleports", "collisions")} | {"car": m.get("car"), "rules": m.get("rules")}

    spec = _spec(scenario_id)
    return {"schema": "flowstate.results-demo/3", "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "network": L.get("network", "district"), "codes": "main", "tlsIds": L["tlsIds"], "dt": FRAME_DT,
            "pair": f"district__{scenario_id}__{pair['seed']}", "scenario": scenario_id, "scenarioTitle": spec["title"], "tagline": spec["tagline"], "seed": pair["seed"],
            "bestCaseStopS": BEST_CASE_STOP_S, "minQueueS": MIN_QUEUE_S, "focus": L["focus"], "kpis": [{"key": k, "label": lbl, "unit": u} for k, lbl, u in KPIS.get(scenario_id, KPIS["default"])],
            "left": {"controller": "fixed_time", "frames": L["frames"]}, "right": {"controller": "flowstate_rules", "frames": R["frames"]},
            "units": units, "example": example, "totals": {"left": totals(L), "right": totals(R)},
            "aggregate": {"episodes": len(seeds), "seeds": seeds, "metrics": agg},
            "rightIncludes": "the same fixed plans with FlowState's rules: queue-responsive green durations and ambulance green priority (hand-written; no learned model is used on this network)"}


def _spec(scenario_id: str) -> dict[str, Any]:
    """Title and tagline: ours for the curated four, otherwise straight from main's scenario catalog."""
    if scenario_id in SCENARIOS:
        return SCENARIOS[scenario_id]
    from ..scenarios.catalog import CATALOG

    cat = CATALOG[scenario_id][0]
    return {"title": cat.title, "tagline": cat.tagline, "params": {}}


def compare_one(scenario_id: str, seed: int) -> dict[str, Any]:
    """Run both arms of one seed in parallel processes (libsumo allows one simulation per process) and build a page asset. Used by ``POST /api/replay``."""
    with ProcessPoolExecutor(2, mp_context=mp.get_context("spawn")) as pool:
        fl = pool.submit(run, seed, "fixed_time", scenario_id, True)
        fr = pool.submit(run, seed, "flowstate", scenario_id, True)
        pair = {"seed": seed, "left": fl.result(), "right": fr.result()}
    car_keys = [k for k, v in pair["left"]["metrics"]["car"].items() if isinstance(v, (int, float))]
    return asset(scenario_id, pair, paired([pair], car_keys), [seed])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--seeds", default="3100-3107")
    ap.add_argument("--show", type=int, default=None, help="seed recorded with frames for display (default: first seed)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--eval-out", default=None)
    a = ap.parse_args(argv)
    lo, hi = (a.seeds.split("-") + [None])[:2]
    seeds = list(range(int(lo), int(hi) + 1)) if hi else [int(lo)]
    show = a.show if a.show is not None else seeds[0]
    scenarios = a.scenarios.split(",")
    t0 = time.time()
    jobs = [(sc, s, s == show) for sc in scenarios for s in seeds]
    with ProcessPoolExecutor(a.workers, mp_context=mp.get_context("spawn")) as pool:
        results = list(pool.map(_job, jobs))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from ..api.service import NETWORKS_DIR
    from ..network.geometry import network_geometry

    (out / "district-network.json").write_text(json.dumps(network_geometry(str(NETWORKS_DIR / "district.net.xml"), "district"), separators=(",", ":")))
    index = {"network": "district", "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "seeds": seeds, "scenarios": []}
    ev_all: dict[str, Any] = {"builtAt": index["builtAt"], "seeds": seeds, "arms": {"left": "fixed_time", "right": "flowstate_rules"}, "scenarios": {}}
    for sc in scenarios:
        pairs = [r for (sid, _s, _f), r in zip(jobs, results) if sid == sc]
        car_keys = [k for k, v in pairs[0]["left"]["metrics"]["car"].items() if isinstance(v, (int, float))]
        agg = paired(pairs, car_keys)
        shown = next(p for p in pairs if p["seed"] == show)
        doc = asset(sc, shown, agg, seeds)
        (out / sc).mkdir(exist_ok=True)
        (out / sc / "pair.json.gz").write_bytes(gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), 9))
        head = doc["kpis"][0]
        hk = head["key"]
        hs = agg.get(hk) or {}
        index["scenarios"].append({"id": sc, "title": doc["scenarioTitle"], "tagline": doc["tagline"], "units": len(doc["units"]),
                                   "headline": {"key": hk, "label": head["label"], "unit": head["unit"], "left": hs.get("left_mean"), "right": hs.get("right_mean"), "pct": hs.get("pct"), "ci95": hs.get("ci95"), "better": hs.get("better"), "worse": hs.get("worse")}})
        ev_all["scenarios"][sc] = {"aggregate": agg, "perSeed": [{"seed": p["seed"], "left": {k: v for k, v in p["left"]["metrics"].items() if k != "scenario"},
                                                                 "right": {k: v for k, v in p["right"]["metrics"].items() if k != "scenario"},
                                                                 "units": {"left": p["left"]["units"], "right": p["right"]["units"]}} for p in pairs]}
        print(json.dumps({"scenario": sc, "example": doc["example"], "units": len(doc["units"]), "frames": len(doc["left"]["frames"]), "bytes": (out / sc / "pair.json.gz").stat().st_size}))
    (out / "index.json").write_text(json.dumps(index, indent=1))
    ev_path = Path(a.eval_out) if a.eval_out else out / "district-eval.json"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    ev_path.write_text(json.dumps(ev_all, indent=1))
    print(json.dumps({"scenarios": scenarios, "seeds": len(seeds), "minutes": round((time.time() - t0) / 60, 1)}))


if __name__ == "__main__":
    main()
