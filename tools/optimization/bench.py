"""Paired benchmark harness for optimization v2 (development search and the one-shot final test).

Every (controller, scenario, seed, intensity) runs once in its own SUMO process with the lab's demand (route-file SHA checked across
controllers), and is cached in a JSONL file, so re-running a stage never re-simulates a finished episode. Besides SUMO's metrics,
every episode records action latency (observation, forecast inference, decision) and a SafeSignal audit computed from the signal
history: greens shorter than the minimum or longer than the maximum, and yellows / all-reds shorter than their fixed lengths.

    python tools/optimization/bench.py dev   [--ids "a;b;c"]   # development seeds 4000-4007 x 4 training scenarios
    python tools/optimization/bench.py final [--ids "a;b;c"]   # final seeds 5000-5011 x 5 scenarios + unseen intensities
    python tools/optimization/bench.py report <stage> <left> <right>
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
OUT = ROOT / "artifacts" / "lab" / "optimization_v2"

TRAIN_SC = ("balanced", "ns_heavy", "ew_heavy", "pulse")
ALL_SC = TRAIN_SC + ("rush_ramp",)
# Seed ranges already used elsewhere: forecaster 1000-1011 / 2000-2003 / 3000-3007, heuristic tuning 1000-1002,
# ML-control tuning 1100-1105, previous held-out 3100-3111. Development and final ranges below are new and disjoint.
STAGES = {
    "dev": [(sc, s, None) for sc in TRAIN_SC for s in range(4000, 4008)],
    # more development seeds, used only to separate the finalists of the dev search (still development data)
    "dev_ext": [(sc, s, None) for sc in TRAIN_SC for s in range(4008, 4016)],
    # final: standard intensity rule (1.0 even / 1.18 odd seed) on all five scenarios, plus two demand levels never used anywhere
    "final": [(sc, s, None) for sc in ALL_SC for s in range(5000, 5012)]
             + [(sc, s, x) for sc in ALL_SC for s in range(5100, 5104) for x in (0.8, 1.3)],
    # the previous held-out set, run once after selection as a reference only (never used to choose anything)
    "prev_heldout": [(sc, s, None) for sc in ALL_SC for s in range(3100, 3112)],
}
METRICS = ["mean_wait_s", "mean_travel_s", "p95_travel_s", "throughput_vph", "queue_total_mean_veh", "queue_max_veh", "spillback_pct",
           "teleports", "collisions", "vehicles_completed", "vehicles_unfinished", "emergency_mean_stopped_s", "co2_kg", "signal_switches",
           "forced_changes", "requests_refused"]
HIGHER_BETTER = {"throughput_vph", "vehicles_completed"}


def audit(sim) -> dict[str, int]:
    from flowstate.rl import config as C

    bad = {"green_short": 0, "green_long": 0, "yellow_short": 0, "all_red_short": 0}
    for s in sim.signals.values():
        h = s.history + [(sim.t, "end", -1)]
        prev_t = 0.0
        prev_mode = "green"  # every head starts green at t=0
        for t, mode, _ in h:
            d = t - prev_t
            if prev_mode == "green" and mode != "end":
                bad["green_short"] += d < C.MIN_GREEN_S - 1e-9 and prev_t > 0  # the very first green may be cut only by the controller's own min
                bad["green_long"] += d > C.MAX_GREEN_S + C.STEP_S + 1e-9
            elif prev_mode == "yellow" and mode != "end":
                bad["yellow_short"] += d < C.YELLOW_S - 1e-9
            elif prev_mode == "all_red" and mode != "end":
                bad["all_red_short"] += d < C.ALL_RED_S - 1e-9
            prev_t, prev_mode = t, mode
    return bad


def episode(job: tuple[str, str, int, float | None]) -> dict:
    import numpy as np

    from flowstate.lab.control import make_controller
    from flowstate.lab.forecast import intensity_for
    from flowstate.lab.sim import LabSim
    from flowstate.rl import config as C

    cid, sc, seed, inten = job
    x = intensity_for(seed) if inten is None else inten
    sim = LabSim(f"ob{mp.current_process().pid}")
    try:
        man = sim.reset(sc, seed, 900.0, 600.0, x)
        ctl, info = make_controller(cid, sim, shadow_forecast=False)
        ctl.reset(sim)
        dec_ms = []
        t0 = time.time()
        while not sim.done:
            sim.observe()
            a = time.perf_counter()
            acts = ctl.decide(sim)
            dec_ms.append((time.perf_counter() - a) * 1000)
            for jid, act in zip(C.CONTROLLED, acts):
                if act:
                    sim.request(jid)
            sim.advance(C.DECISION_S)
        m = sim.metrics()
        tm = sim.timing()
        obs, inf = np.array(sim.obs_ms or [0]), np.array(sim.infer_ms or [0])
        tot = obs[: len(dec_ms)] + (inf[: len(dec_ms)] if len(sim.infer_ms) else 0) + np.array(dec_ms)
        return {"controller": cid, "scenario": sc, "seed": seed, "intensity": x, "demandSha": man["sha256"], "info": info,
                **{k: m[k] for k in METRICS}, "ml": ctl.stats() if hasattr(ctl, "stats") else None, "audit": audit(sim),
                "latency_ms": {"observation": round(float(obs.mean()), 3), "inference": round(float(inf.mean()), 3),
                               "decide": round(float(np.mean(dec_ms)), 3), "action_total_mean": round(float(tot.mean()), 3),
                               "action_total_p95": round(float(np.percentile(tot, 95)), 3), "sumo_step": tm["sumo_step"]["mean_ms"]},
                "wall_s": round(time.time() - t0, 2)}
    finally:
        sim.close()


def key(r) -> tuple:
    return (r["controller"], r["scenario"], r["seed"], round(r["intensity"], 4))


def load(stage: str) -> list[dict]:
    f = OUT / f"{stage}_runs.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def run(stage: str, ids: list[str], workers: int) -> None:
    from flowstate.lab.forecast import intensity_for

    OUT.mkdir(parents=True, exist_ok=True)
    have = {key(r) for r in load(stage)}
    jobs = [(c, sc, s, x) for c in ids for sc, s, x in STAGES[stage]
            if (c, sc, s, round(intensity_for(s) if x is None else x, 4)) not in have]
    print(f"{stage}: {len(jobs)} episodes to run ({len(have)} cached)", flush=True)
    t0 = time.time()
    with mp.get_context("spawn").Pool(workers) as pool, (OUT / f"{stage}_runs.jsonl").open("a") as f:
        for r in pool.imap_unordered(episode, jobs):
            f.write(json.dumps(r) + "\n")
            f.flush()
    print(f"done in {time.time() - t0:.0f} s", flush=True)


def paired(rows: list[dict], left: str, right: str, metrics=METRICS, seed: int = 0) -> dict:
    import numpy as np

    by = {key(r): r for r in rows}
    ks = sorted({k[1:] for k in by if k[0] == left} & {k[1:] for k in by if k[0] == right})
    for k in ks:
        assert by[(left, *k)]["demandSha"] == by[(right, *k)]["demandSha"], f"demand differs for {k}"
    rng = np.random.default_rng(seed)
    out = {"pairs": len(ks)}
    for m in metrics:
        a = np.array([by[(left, *k)][m] for k in ks], float)
        b = np.array([by[(right, *k)][m] for k in ks], float)
        d = b - a
        boots = rng.choice(d, size=(4000, len(d)), replace=True).mean(axis=1)
        sign = 1 if m in HIGHER_BETTER else -1
        out[m] = {"left": round(float(a.mean()), 3), "right": round(float(b.mean()), 3), "diff": round(float(d.mean()), 3),
                  "pct": round(100 * float(d.mean()) / float(a.mean()), 2) if a.mean() else None,
                  "ci95": [round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)],
                  "better": int((d * sign > 1e-9).sum()), "worse": int((d * sign < -1e-9).sum())}
    return out


def summary(stage: str, ids: list[str] | None = None, ref: str = "queue_heuristic") -> None:
    import numpy as np

    rows = load(stage)
    ids = ids or sorted({r["controller"] for r in rows})
    print(f"{'controller':34s} {'n':>3s} {'wait':>7s} {'dWait':>7s} {'CI95':>17s} {'W/L':>6s} {'travel':>7s} {'thru':>6s} {'queue':>6s} {'tele':>5s} {'coll':>4s} {'unfin':>6s} {'act ms':>7s} audit")
    for c in ids:
        rs = [r for r in rows if r["controller"] == c]
        if not rs:
            continue
        p = paired(rows, ref, c)
        w = p["mean_wait_s"]
        aud = sum(sum(r["audit"].values()) for r in rs)
        print(f"{c:34s} {len(rs):3d} {np.mean([r['mean_wait_s'] for r in rs]):7.2f} {w['diff']:+7.2f} [{w['ci95'][0]:+6.2f},{w['ci95'][1]:+6.2f}] "
              f"{w['better']:2d}/{w['worse']:<2d} {np.mean([r['mean_travel_s'] for r in rs]):7.1f} {np.mean([r['throughput_vph'] for r in rs]):6.0f} "
              f"{np.mean([r['queue_total_mean_veh'] for r in rs]):6.1f} {sum(r['teleports'] for r in rs):5d} {sum(r['collisions'] for r in rs):4d} "
              f"{np.mean([r['vehicles_unfinished'] for r in rs]):6.1f} {np.mean([r['latency_ms']['action_total_mean'] for r in rs]):7.2f} {aud}")


if __name__ == "__main__":
    mp.set_executable(sys.executable)  # venv on Windows: spawned workers must use the venv interpreter
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("stage")
    ap.add_argument("--ids", default="")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    ids = [x for x in a.ids.split(";") if x]
    if a.cmd == "run":
        run(a.stage, ids, a.workers)
        summary(a.stage, ids)
    elif a.cmd == "summary":
        summary(a.stage, ids or None)
