"""Frozen multi-seed benchmark of the three demo scenarios under the lab's three controllers.

Protocol (written before the benchmark was run, and not changed after seeing its results):

* Scenario parameters were calibrated on the *design seeds* 9001-9003 only. Those seeds are never used for a reported conclusion.
* The benchmark seeds 4001-4010 are disjoint from every seed the forecaster or the ML controller was trained, tuned or evaluated on
  (1000-1011, 1100-1105, 2000-2003, 3000-3007, 3100-3111) and from the design seeds.
* Every (scenario, seed) is run by all three controllers on a byte-identical route file (SHA-256 compared; a mismatch aborts).
* Paired differences use a bootstrap 95 % CI over seeds. Wins and losses are counted. Nothing is dropped or re-run.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import analyse, forecast_skill
from .runner import run_many
from .scenarios import SPECS

BENCH_SEEDS = tuple(range(4001, 4011))
DESIGN_SEEDS = (9001, 9002, 9003)
CONTROLLERS = ("fixed_time", "queue_heuristic", "ml_forecast")
METRICS = ("mean_wait_s", "p95_wait_s", "mean_travel_s", "p95_travel_s", "throughput_vph", "completed_fraction", "queue_peak_veh",
           "queue_mean_in_window_veh", "spillback_pct", "spillback_link_seconds", "spilled_links_peak", "mean_speed_ms_window",
           "near_standstill_seconds", "t_cleared_s", "teleports", "collisions", "never_inserted", "on_road_at_end")
HIGHER_BETTER = {"throughput_vph", "completed_fraction", "mean_speed_ms_window"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def out_dir() -> Path:
    return repo_root() / "artifacts" / "demo"


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_root(), capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def flatten(run: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    row = {"scenario": run["scenario"], "seed": run["seed"], "controller": run["controller"], "demandSha": run["demandSha"],
           "vehicles": run["demand"]["scheduled"], "intensity": run["intensity"]}
    for m in METRICS:
        v = a.get(m)
        if m == "t_cleared_s" and v is None:
            v = run["metrics"]["sim_end_s"]  # never cleared: censored at the end of the run, counted as the worst value
        row[m] = v
    row["t_cleared_censored"] = a.get("t_cleared_s") is None
    row["analysis"] = {k: v for k, v in a.items() if k != "markers"}
    row["markers"] = a["markers"]
    row["forecastSkill"] = forecast_skill(run)
    row["ml"] = run.get("ml")
    row["incident"] = run["incident"]
    row["demand"] = run["demand"]
    return row


def paired(rows: list[dict[str, Any]], left: str, right: str, seed: int = 0) -> dict[str, Any]:
    by = {(r["scenario"], r["seed"], r["controller"]): r for r in rows}
    keys = sorted({(r["scenario"], r["seed"]) for r in rows if (r["scenario"], r["seed"], left) in by and (r["scenario"], r["seed"], right) in by})
    for k in keys:
        assert by[(*k, left)]["demandSha"] == by[(*k, right)]["demandSha"], f"demand differs for {k}"
    rng = np.random.default_rng(seed)
    out: dict[str, Any] = {}
    for m in METRICS:
        a = np.array([by[(*k, left)][m] for k in keys], dtype=float)
        b = np.array([by[(*k, right)][m] for k in keys], dtype=float)
        d = b - a
        boots = rng.choice(d, size=(4000, len(d)), replace=True).mean(axis=1)
        sign = 1 if m in HIGHER_BETTER else -1
        lo, hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
        out[m] = {"left_mean": round(float(a.mean()), 3), "right_mean": round(float(b.mean()), 3), "mean_diff": round(float(d.mean()), 3),
                  "pct": round(100 * float(d.mean()) / float(a.mean()), 2) if a.mean() else None, "ci95": [round(lo, 3), round(hi, 3)],
                  "clear": bool(lo > 0 or hi < 0), "better": int((d * sign > 1e-9).sum()), "worse": int((d * sign < -1e-9).sum()), "pairs": len(d)}
    return out


def describe(rows: list[dict[str, Any]], scenario: str, controller: str) -> dict[str, Any]:
    sel = [r for r in rows if r["scenario"] == scenario and r["controller"] == controller]
    out = {}
    for m in METRICS:
        v = np.array([r[m] for r in sel], dtype=float)
        out[m] = {"mean": round(float(v.mean()), 2), "sd": round(float(v.std(ddof=1)), 2) if len(v) > 1 else 0.0, "min": round(float(v.min()), 2),
                  "max": round(float(v.max()), 2), "median": round(float(np.median(v)), 2)}
    return out


def tiers(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Severity ladder under the plain (non-ML) heuristic: does a harder scenario really produce worse delay and spillback?"""
    out = {}
    for sc in SPECS:
        out[sc] = {m: describe(rows, sc, "queue_heuristic")[m]["mean"] for m in ("mean_wait_s", "p95_travel_s", "queue_peak_veh", "spillback_pct", "spilled_links_peak",
                                                                                "near_standstill_seconds", "teleports", "collisions")}
    return out


def forecast_summary(rows: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    """Shadow-forecast skill measured inside the non-ML runs (the forecast is displayed there, never used for control)."""
    sk = [r["forecastSkill"] for r in rows if r["scenario"] == scenario and r["controller"] == "queue_heuristic" and r["forecastSkill"]]
    tp, fp, fn = sum(s["tp"] for s in sk), sum(s["fp"] for s in sk), sum(s["fn"] for s in sk)
    leads = [s["median_lead_s"] for s in sk if s["median_lead_s"] is not None]
    return {"truePositive": tp, "falsePositive": fp, "falseNegative": fn, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "median_lead_s": round(float(np.median(leads)), 1) if leads else None,
            "runs": len(sk)}


def run_benchmark(seeds: tuple[int, ...] = BENCH_SEEDS, workers: int = 4, path: Path | None = None) -> dict[str, Any]:
    t0 = time.time()
    jobs = [(c, sc, sd, None, False) for sc in SPECS for sd in seeds for c in CONTROLLERS]
    runs = run_many(jobs, workers)
    rows = [flatten(r, analyse(r)) for r in runs]
    # fairness gate: identical demand for every controller of a (scenario, seed)
    for sc in SPECS:
        for sd in seeds:
            shas = {r["demandSha"] for r in rows if r["scenario"] == sc and r["seed"] == sd}
            counts = {r["vehicles"] for r in rows if r["scenario"] == sc and r["seed"] == sd}
            assert len(shas) == 1 and len(counts) == 1, f"demand differs across controllers for {sc} seed {sd}"
    doc: dict[str, Any] = {
        "protocol": __doc__.strip().split("\n\n", 1)[1], "git": git_sha(), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seeds": list(seeds), "designSeeds": list(DESIGN_SEEDS), "controllers": list(CONTROLLERS), "episodes": len(rows),
        "wall_s": round(time.time() - t0, 1), "workers": workers,
        "scenarios": {sc: {"vehicles": {"mean": round(float(np.mean([r["vehicles"] for r in rows if r["scenario"] == sc and r["controller"] == "fixed_time"])), 1),
                                        "min": min(r["vehicles"] for r in rows if r["scenario"] == sc and r["controller"] == "fixed_time"),
                                        "max": max(r["vehicles"] for r in rows if r["scenario"] == sc and r["controller"] == "fixed_time")},
                          "by_controller": {c: describe(rows, sc, c) for c in CONTROLLERS},
                          "ml_vs_heuristic": paired([r for r in rows if r["scenario"] == sc], "queue_heuristic", "ml_forecast"),
                          "heuristic_vs_fixed": paired([r for r in rows if r["scenario"] == sc], "fixed_time", "queue_heuristic"),
                          "ml_vs_fixed": paired([r for r in rows if r["scenario"] == sc], "fixed_time", "ml_forecast"),
                          "forecast": forecast_summary(rows, sc)} for sc in SPECS},
        "pooled": {"ml_vs_heuristic": paired(rows, "queue_heuristic", "ml_forecast"), "heuristic_vs_fixed": paired(rows, "fixed_time", "queue_heuristic"),
                   "ml_vs_fixed": paired(rows, "fixed_time", "ml_forecast")},
        "severityLadder": tiers(rows),
        "rows": rows,
    }
    p = path or out_dir() / "benchmark.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1))
    return doc
