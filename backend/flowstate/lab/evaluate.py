"""Tuning and paired held-out evaluation of the forecast-aware controller.

* ``tune``: a small grid of (beta, bound, spill) on *training-side* seeds 1100-1105 (never used for the final numbers).
* ``evaluate_paired``: fixed-time, the plain heuristic and the forecast-assisted heuristic on fresh seeds 3100-3111 x all five scenarios
  (the rush-hour ramp is never trained on). Identical demand per (scenario, seed), checked by hash. Reports paired differences
  with a bootstrap 95 % CI and win/loss counts, whatever they show.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from typing import Any

import numpy as np

from ..rl.demand import SCENARIOS, TRAIN_SCENARIOS
from .forecast import intensity_for, lab_dir

TUNE_SEEDS = list(range(1100, 1106))
TEST_SEEDS = list(range(3100, 3112))
METRICS = ["mean_wait_s", "mean_travel_s", "p95_travel_s", "throughput_vph", "queue_total_mean_veh", "queue_max_veh", "spillback_pct",
           "co2_kg", "teleports", "collisions", "vehicles_completed", "emergency_mean_stopped_s", "mean_wait_civilian_s", "mean_travel_civilian_s",
           "ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s", "ev_unfinished"]
HIGHER_BETTER = {"throughput_vph", "vehicles_completed"}


def _job(args: tuple[str, str, int]) -> dict[str, Any]:
    from .record import run_episode

    cid, sc, seed = args
    r = run_episode(cid, sc, seed, intensity_for(seed))
    return {"controller": cid, "scenario": sc, "seed": seed, "intensity": intensity_for(seed), "demandSha": r["demandSha"], "ml": r.get("ml"),
            "wall_s": r["wall_s"], **{m: r["metrics"][m] for m in METRICS}, "timing": r["timing"]}


def _run(jobs: list[tuple[str, str, int]], workers: int) -> list[dict[str, Any]]:
    with mp.get_context("spawn").Pool(workers) as pool:
        return pool.map(_job, jobs, chunksize=1)


def paired(rows: list[dict[str, Any]], left: str, right: str, rng_seed: int = 0) -> dict[str, Any]:
    by = {(r["scenario"], r["seed"], r["controller"]): r for r in rows}
    keys = sorted({(r["scenario"], r["seed"]) for r in rows if (r["scenario"], r["seed"], left) in by and (r["scenario"], r["seed"], right) in by})
    for k in keys:
        assert by[(*k, left)]["demandSha"] == by[(*k, right)]["demandSha"], f"demand differs for {k}"
    rng = np.random.default_rng(rng_seed)
    out: dict[str, Any] = {}
    for m in METRICS:
        a = np.array([by[(*k, left)][m] for k in keys], dtype=float)
        b = np.array([by[(*k, right)][m] for k in keys], dtype=float)
        d = b - a
        boots = rng.choice(d, size=(4000, len(d)), replace=True).mean(axis=1) if len(d) else np.zeros(1)
        sign = 1 if m in HIGHER_BETTER else -1
        out[m] = {"left_mean": round(float(a.mean()), 3), "right_mean": round(float(b.mean()), 3), "mean_diff": round(float(d.mean()), 3),
                  "pct": round(100 * float(d.mean()) / float(a.mean()), 2) if a.mean() else None,
                  "ci95": [round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)], "pairs": len(d),
                  "better": int((d * sign > 1e-9).sum()), "worse": int((d * sign < -1e-9).sum())}
    return out


GRIDS = {
    1: [(b, 6.0, s) for b in (0.25, 0.5, 1.0) for s in (0.85, 0.95, 9.0)],  # spill 9.0 = spillback term off
    2: [(b, 6.0, s) for b in (0.0, 0.1, 0.25) for s in (0.5, 0.6, 0.7)],  # beta 0 = spillback anticipation only
}


def tune(workers: int = 4, rounds: tuple[int, ...] = (1, 2)) -> dict[str, Any]:
    """Every round is kept in the file; rounds already run are not re-simulated (same seeds, deterministic)."""
    from ..rl.controllers import tuned_heuristic

    f = lab_dir() / "ml_control_tuning.json"
    old = json.loads(f.read_text()) if f.exists() else {}
    table, tele = dict(old.get("mean_wait_s", {})), dict(old.get("teleports", {}))
    cids = ["queue_heuristic"] + [f"ml_forecast@{b},{bd},{s}" for r in rounds for b, bd, s in GRIDS[r]]
    todo = [c for c in cids if c not in table]
    t0 = time.time()
    rows = _run([(c, sc, s) for sc in TRAIN_SCENARIOS for s in TUNE_SEEDS for c in todo], workers) if todo else []
    table |= {c: round(float(np.mean([r["mean_wait_s"] for r in rows if r["controller"] == c])), 3) for c in todo}
    tele |= {c: int(sum(r["teleports"] for r in rows if r["controller"] == c)) for c in todo}
    best = min((c for c in table if c != "queue_heuristic"), key=lambda c: table[c])
    b, bd, s = (float(x) for x in best.split("@")[1].split(","))
    doc = {"seeds": TUNE_SEEDS, "scenarios": list(TRAIN_SCENARIOS), "base": tuned_heuristic(), "mean_wait_s": table, "teleports": tele,
           "best": {"beta": b, "bound": bd, "spill": s}, "best_id": best, "heuristic_mean_wait_s": table["queue_heuristic"],
           "best_beats_heuristic_on_tuning_seeds": table[best] < table["queue_heuristic"], "rounds": sorted(set(old.get("rounds", [])) | set(rounds)),
           "wall_s": round(old.get("wall_s", 0) + time.time() - t0, 1)}
    lab_dir().mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(doc, indent=1))
    return doc


def evaluate_paired(workers: int = 4, rerun: tuple[str, ...] | None = None) -> dict[str, Any]:
    """``rerun``: only simulate these arms and reuse the stored rows of the others (every arm is deterministic for its (scenario, seed, demand))."""
    cids = ["fixed_time", "queue_heuristic", "queue_heuristic_ev", "ml_forecast", "ml_forecast_ev"]  # *_ev = controller turns the lights ahead of an ambulance green
    t0 = time.time()
    fresh = [c for c in cids if rerun is None or c in rerun]
    rows = _run([(c, sc, s) for sc in SCENARIOS for s in TEST_SEEDS for c in fresh], workers)
    if rerun is not None:
        stored = json.loads((lab_dir() / "comparison_eval.json").read_text())["rows"]
        rows += [r for r in stored if r["controller"] not in fresh]
    ml_rows = [r for r in rows if r["controller"] == "ml_forecast" and r["ml"]]  # influence stats of the pure-ML arm
    per_scenario = {sc: paired([r for r in rows if r["scenario"] == sc], "queue_heuristic", "ml_forecast")["mean_wait_s"] for sc in SCENARIOS}
    summary = {"episodes": len(rows) // len(cids), "seeds": TEST_SEEDS, "scenarios": list(SCENARIOS),
               "ml_vs_heuristic": paired(rows, "queue_heuristic", "ml_forecast"),
               "heuristic_vs_fixed": paired(rows, "fixed_time", "queue_heuristic"),
               "ml_vs_fixed": paired(rows, "fixed_time", "ml_forecast"),
               # the demo pair (right side also clears lights for ambulances) and the attribution of its effect: forecast vs emergency-green rule
               "ml_ev_vs_heuristic": paired(rows, "queue_heuristic", "ml_forecast_ev"),
               "ev_rule_effect_on_heuristic": paired(rows, "queue_heuristic", "queue_heuristic_ev"),
               "ev_rule_effect_on_ml": paired(rows, "ml_forecast", "ml_forecast_ev"),
               "ml_vs_heuristic_both_with_ev": paired(rows, "queue_heuristic_ev", "ml_forecast_ev"),
               "arms": {"fixed_time": "pre-timed, ignores ambulances", "queue_heuristic": "heuristic only, no ambulance priority", "queue_heuristic_ev": "heuristic only + green corridor along the ambulance route",
                        "ml_forecast": "forecast-assisted, no ambulance priority", "ml_forecast_ev": "forecast-assisted + green corridor along the ambulance route"},
               "ml_vs_heuristic_wait_by_scenario": per_scenario,
               "ml_influence": {"decisions": sum(r["ml"]["decisions"] for r in ml_rows), "changed": sum(r["ml"]["changed"] for r in ml_rows),
                                "switchedEarlier": sum(r["ml"]["switchedEarlier"] for r in ml_rows), "heldLonger": sum(r["ml"]["heldLonger"] for r in ml_rows)},
               "ml_params": json.loads((lab_dir() / "ml_control_tuning.json").read_text())["best"] if (lab_dir() / "ml_control_tuning.json").exists() else None}
    timing = {c: {k: round(float(np.mean([r["timing"][k]["mean_ms"] for r in rows if r["controller"] == c and "timing" in r] or [0.0])), 3) for k in ("sumo_step", "observation", "inference")}
              for c in cids}
    doc = {"summary": summary, "timing_ms": timing, "wall_s": round(time.time() - t0, 1), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "rows": [{k: v for k, v in r.items() if k != "timing"} for r in rows]}
    (lab_dir() / "comparison_eval.json").write_text(json.dumps(doc, indent=1))
    return doc
