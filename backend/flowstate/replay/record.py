"""Record a paired replay on the real ``district`` network and a small paired evaluation.

    python -m flowstate.replay.record --seeds 3100-3109 --out ../frontend/public/flowstate-demo --show 3100

Arms (same seed, same demand, same ambulance calls):
* ``left``  = ``fixed`` signals (main's pre-timed plans), no priority;
* ``right`` = ``actuated`` signals (main's gap-based adaptive plans) + the ambulance green-priority rule in ``priority.py``.
Nothing here is a learned model. The queue forecaster from the Intelligence Lab is *not* used on this network.
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


def run(seed: int, arm: str, duration_min: int = 5, calls: int = 2, frames: bool = True) -> dict[str, Any]:
    from ..api.service import NETWORKS_DIR, WORK_DIR
    from ..scenarios.catalog import create
    from ..sim.engine import Engine, RunConfig
    from ..sim.vtypes import base_type
    from .priority import EmergencyPriority

    mode = "fixed" if arm == "fixed_time" else "actuated"
    sc = create("ambulance", {"duration": duration_min, "calls": calls})
    eng = Engine(sc, RunConfig("ambulance", sc.params, seed, mode, label=f"replay-{arm}-{seed}"), NETWORKS_DIR, WORK_DIR / f"replay-{arm}-{seed}")
    eng.start()
    prio = None
    if arm == "flowstate":
        prio = EmergencyPriority()
        eng.signals.policies.append(prio)
    T = eng.T
    tls_ids = sorted(eng.signals.tls)
    ids: dict[str, int] = {}
    out_frames: list[dict[str, Any]] = []
    amb: dict[str, dict[str, Any]] = {}
    queue_sum = 0.0
    samples = 0
    last = -1.0
    try:
        while not eng.finished:
            eng.step()
            if eng.t < 0 or eng.t - last < 1.0 - 1e-6:
                continue
            last = eng.t
            samples += 1
            queue_sum += sum(eng.junction_queues().values())
            ev: list[list[int]] = []
            for vid in eng.emergency_active():
                tr = eng.tracked[vid]
                if tr.kind != "ambulance" or tr.status == "on scene" or tr.status == "returning":
                    continue
                u = amb.setdefault(vid, {"id": vid, "call": tr.data.get("call"), "departS": round(eng.t, 1), "stoppedRedS": 0.0, "stoppedQueueS": 0.0,
                                         "first": int(eng.t), "last": int(eng.t)})
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
                        u["stoppedRedS"] += 1.0
                    else:
                        status = STATUS_QUEUE
                        u["stoppedQueueS"] += 1.0
                elif state in "Gg" and dist < 120:
                    status = STATUS_GREEN
                ev.append([ids.setdefault(vid, len(ids) + 1), status, int(min(9999.0, dist) * 10)])
            if frames:
                vt = eng.vtype
                v = [[ids.setdefault(vid, len(ids) + 1), int(round(s[0] * 10)), int(round(s[1] * 10)), int(round(s[2])),
                      MAIN_TYPES.get(base_type(vt.get(vid, "sedan")), 0), int(round(s[3] * 10))] for vid, s in eng.vehicles.items()]
                fr: dict[str, Any] = {"t": int(eng.t), "v": v, "s": [eng.tls_state.get(t, "") for t in tls_ids]}
                if ev:
                    fr["e"] = ev
                out_frames.append(fr)
        res = eng.results()
    finally:
        eng.close()
    for vid, u in amb.items():
        tr = eng.tracked.get(vid)
        rt = tr.data.get("responseTime") if tr else None
        u.update({"travelS": rt, "finished": rt is not None, "freeFlowS": tr.data.get("freeFlow") if tr else None, "intId": ids.get(vid)})
    cars = res["metrics"]["byMode"].get("car", {})
    metrics = {
        "ev_total": len(amb), "ev_unfinished": sum(1 for u in amb.values() if not u["finished"]),
        "ev_travel_mean_s": float(np.mean([u["travelS"] for u in amb.values() if u["finished"]])) if any(u["finished"] for u in amb.values()) else None,
        "ev_stopped_red_s": float(np.mean([u["stoppedRedS"] for u in amb.values()])) if amb else None,
        "ev_stopped_queue_s": float(np.mean([u["stoppedQueueS"] for u in amb.values()])) if amb else None,
        "queue_total_mean_veh": queue_sum / max(1, samples), "throughput_vph": res["metrics"]["completed"] * 3600.0 / max(1.0, 60.0 * duration_min),
        "teleports": res["teleports"], "collisions": res["collisions"], "car": cars, "last": res.get("last", {}),
        "signals": res["signals"], "priority": prio.summary() if prio else None,
    }
    return {"seed": seed, "arm": arm, "controller": "fixed_time" if arm == "fixed_time" else "adaptive_ev", "tlsIds": tls_ids, "frames": out_frames,
            "ambulances": list(amb.values()), "metrics": metrics}


def _job(args: tuple[int, bool]) -> dict[str, Any]:
    seed, frames = args
    return {"seed": seed, "left": run(seed, "fixed_time", frames=frames), "right": run(seed, "flowstate", frames=frames)}


def car_metric(m: dict[str, Any], key: str) -> float | None:
    for k in (key, key.replace("mean", "avg")):
        if k in m.get("car", {}) and m["car"][k] is not None:
            return float(m["car"][k])
    return None


AGG = [("ev_travel_mean_s", True), ("ev_stopped_red_s", True), ("ev_stopped_queue_s", True), ("queue_total_mean_veh", True), ("throughput_vph", False)]


def paired(pairs: list[dict[str, Any]], car_keys: list[str], rng_seed: int = 0) -> dict[str, Any]:
    rng = np.random.default_rng(rng_seed)
    out: dict[str, Any] = {}
    keys = list(AGG) + [(f"car_{k}", k not in ("speed", "trips")) for k in car_keys]
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
        a_, b_ = np.array(a), np.array(b)
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


def asset(pair: dict[str, Any], agg: dict[str, Any], seeds: list[int]) -> dict[str, Any]:
    L, R = pair["left"], pair["right"]
    lu = {u["call"]: u for u in L["ambulances"]}
    ru = {u["call"]: u for u in R["ambulances"]}
    units = []
    for call in sorted(set(lu) & set(ru)):
        l, r = lu[call], ru[call]
        side = lambda u: {"travelS": u["travelS"], "stoppedRedS": u["stoppedRedS"], "stoppedQueueS": u["stoppedQueueS"], "finished": u["finished"],
                          "departS": u["departS"], "first": u["first"], "last": u["last"], "id": u["intId"]}
        units.append({"id": l["intId"], "rightId": r["intId"], "call": call, "left": side(l), "right": side(r),
                      "savedS": round(l["travelS"] - r["travelS"], 1) if l["finished"] and r["finished"] else None})
    usable = [u for u in units if u["savedS"] is not None]
    best = [u for u in usable if u["right"]["stoppedRedS"] + u["right"]["stoppedQueueS"] <= BEST_CASE_STOP_S and u["savedS"] > 0
            and u["left"]["stoppedQueueS"] >= MIN_QUEUE_S]
    example = max(best or usable, key=lambda u: u["savedS"])["id"] if usable else None

    def totals(run_: dict[str, Any]) -> dict[str, Any]:
        m = run_["metrics"]
        return {k: m.get(k) for k in ("ev_total", "ev_unfinished", "ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s", "queue_total_mean_veh",
                                      "throughput_vph", "teleports", "collisions")} | {"car": m.get("car"), "priority": m.get("priority")}

    return {"schema": "flowstate.results-demo/2", "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "network": "district", "pair": f"district__ambulance__{pair['seed']}",
            "scenario": "ambulance", "scenarioTitle": "Ambulance dispatch", "seed": pair["seed"], "codes": "main", "tlsIds": L["tlsIds"],
            "bestCaseStopS": BEST_CASE_STOP_S, "minQueueS": MIN_QUEUE_S,
            "left": {"controller": "fixed_time", "frames": L["frames"]}, "right": {"controller": "adaptive_ev", "frames": R["frames"]},
            "ambulances": units, "example": example, "totals": {"left": totals(L), "right": totals(R)},
            "aggregate": {"episodes": len(seeds), "seeds": seeds, "metrics": agg},
            "rightIncludes": "main's adaptive (gap-actuated) signal plans + a hand-written ambulance green-priority rule; no learned model is used on this network"}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="3100-3109")
    ap.add_argument("--show", type=int, default=None, help="seed to record with frames for display (default: first seed)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--eval-out", default=None)
    a = ap.parse_args(argv)
    lo, hi = (a.seeds.split("-") + [None])[:2]
    seeds = list(range(int(lo), int(hi) + 1)) if hi else [int(lo)]
    show = a.show if a.show is not None else seeds[0]
    t0 = time.time()
    jobs = [(s, s == show) for s in seeds]
    with ProcessPoolExecutor(a.workers, mp_context=mp.get_context("spawn")) as pool:
        pairs = list(pool.map(_job, jobs))
    car_keys = [k for k in pairs[0]["left"]["metrics"]["car"] if isinstance(pairs[0]["left"]["metrics"]["car"][k], (int, float))]
    agg = paired(pairs, car_keys)
    shown = next(p for p in pairs if p["seed"] == show)
    doc = asset(shown, agg, seeds)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pair.json.gz").write_bytes(gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), 9))
    from ..api.service import NETWORKS_DIR
    from ..network.geometry import network_geometry

    (out / "district-network.json").write_text(json.dumps(network_geometry(str(NETWORKS_DIR / "district.net.xml"), "district"), separators=(",", ":")))
    ev = {"builtAt": doc["builtAt"], "seeds": seeds, "arms": {"left": "fixed_time", "right": "adaptive_ev"}, "aggregate": agg,
          "perSeed": [{"seed": p["seed"], "left": {k: v for k, v in p["left"]["metrics"].items() if k != "last"},
                       "right": {k: v for k, v in p["right"]["metrics"].items() if k != "last"},
                       "ambulances": {"left": p["left"]["ambulances"], "right": p["right"]["ambulances"]}} for p in pairs]}
    ev_path = Path(a.eval_out) if a.eval_out else out / "district-eval.json"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    ev_path.write_text(json.dumps(ev, indent=1))
    print(json.dumps({"seeds": len(seeds), "shown": show, "example": doc["example"], "ambulances": len(doc["ambulances"]), "frames": len(doc["left"]["frames"]),
                      "bytes": (out / "pair.json.gz").stat().st_size, "minutes": round((time.time() - t0) / 60, 1)}))


if __name__ == "__main__":
    main()
