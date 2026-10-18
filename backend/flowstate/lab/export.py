"""Compact, dashboard-ready export of every measured benchmark: ``artifacts/lab/benchmark_export.json`` (~30 KB).

It only repackages stored measurement files (forecast_breakdown, comparison_eval, performance, manifest); nothing is recomputed or invented,
and each section names its source file and the sha256 of the model it describes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import health
from .forecast import lab_dir

KEEP = ("n", "mae", "persistence_mae", "skill_vs_persistence", "bias", "rmse", "interval_coverage", "interval_mean_width", "mean_true_queue")


def _rows(d: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [{key: k, **{f: v[f] for f in KEEP if f in v}} for k, v in d.items() if v.get("n")]


def _load(name: str) -> dict[str, Any] | None:
    f = lab_dir() / name
    return json.loads(f.read_text()) if f.exists() else None


def build(write: bool = True) -> dict[str, Any]:
    bd, cmp_, perf = _load("forecast_breakdown.json"), _load("comparison_eval.json"), _load("performance.json")
    fc = health.Forecaster.load_default()
    out: dict[str, Any] = {"schema": "flowstate.benchmark-export/1", "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "labels": {
        "forecast": "model predictions scored against later SUMO measurements on held-out seeds", "controllers": "paired SUMO runs, measured",
        "performance": "wall-clock measurements on the machine that ran them"}, "model": health.status()}
    if bd and fc and bd["model_sha256"] == fc.sha256:
        out["forecast"] = {"source": "forecast_breakdown.json", "testRows": bd["rows_test"], "testEpisodes": bd["episodes_test"], "intervalLevel": bd["calibration"]["level"], "horizons": {}}
        for h, blk in bd["horizons"].items():
            o = blk["overall"]
            out["forecast"]["horizons"][h] = {
                "overall": {f: o[f] for f in KEEP if f in o} | {"episodeBootstrap": o["episode_bootstrap"]},
                "byScenario": _rows(blk["by_scenario"], "scenario"), "byJunction": _rows(blk["by_junction"], "junction"), "byApproach": _rows(blk["by_approach"], "approach"),
                "byTrueSeverity": _rows(blk["by_true_severity"], "severity"), "byPredictedOccupancy": _rows(blk["by_predicted_occupancy"], "bin"),
                "byDataController": _rows(blk["by_data_controller"], "controller"),
                "warnings": {k: v for k, v in blk["warnings"].items() if k != "definition"}, "warningDefinition": blk["warnings"]["definition"],
                "warningsByScenario": blk["warnings_by_scenario"]}
        out["forecast"]["regimeShiftCheck"] = bd["regime_shift_check"]
    else:
        out["forecast"] = None
        out["forecastNote"] = "forecast_breakdown.json is missing or was computed for a different model artifact: run `python -m flowstate.lab.cli benchmark`"
    if cmp_:
        s = cmp_["summary"]
        keys = ("mean_wait_s", "mean_wait_civilian_s", "mean_travel_s", "throughput_vph", "queue_total_mean_veh", "co2_kg", "teleports", "collisions",
                "ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s")
        out["controllers"] = {"source": "comparison_eval.json", "episodes": s["episodes"], "seeds": s["seeds"], "scenarios": s["scenarios"], "mlInfluence": s["ml_influence"],
                              "arms": s.get("arms"),
                              "pairs": {name: {m: s[name][m] for m in keys if m in s[name]} for name in ("ml_vs_heuristic", "heuristic_vs_fixed", "ml_vs_fixed", "ml_ev_vs_heuristic",
                                                                                                     "ev_rule_effect_on_heuristic", "ev_rule_effect_on_ml", "ml_vs_heuristic_both_with_ev") if name in s},
                              "mlVsHeuristicWaitByScenario": s["ml_vs_heuristic_wait_by_scenario"],
                              "reading": "ci95 spanning zero means no reliable difference; 'better'/'worse' count paired episodes"}
    if perf:
        out["performance"] = {"source": "performance.json", "runs": [{k: r[k] for k in ("scenario", "intensity", "simulated_s", "wall_s", "realtime_factor", "peak_vehicles_in_network", "sumo_step",
                              "observation_collection", "forecast_inference", "report_build", "report_json_serialise", "frame_json_serialise", "decision_interval_total", "payload_bytes", "memory_mb", "microbenchmark_at_heavy_traffic")}
                              | {"systemCpuPercent": r["machine"]["system_cpu_percent_during_run"]} for r in perf["runs"]]}
    if write:
        (lab_dir() / "benchmark_export.json").write_text(json.dumps(out, indent=1))
    return out


def load() -> dict[str, Any]:
    f = lab_dir() / "benchmark_export.json"
    return json.loads(f.read_text()) if f.exists() else build(write=False)
