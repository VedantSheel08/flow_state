"""CPU, RAM and simulation-step latency of the demo scenarios.

Episodes run one at a time, each in a fresh process (so peak RSS and CPU time belong to that episode), with frame recording on,
which is what the live and replay paths do. The latency that matters for playback is the cost of one simulated second
(SUMO step + measurement/frame capture) against the 1 s of wall time real-time playback allows, and the cost of one control
decision (observation + forecast + controller) against the 5 s decision interval.
"""

from __future__ import annotations

import json
import os
import platform
import time
from typing import Any

import numpy as np

from .benchmark import git_sha, out_dir
from .runner import run_many
from .scenarios import SPECS


def run_perf(seeds: dict[str, int], controllers: tuple[str, ...] = ("queue_heuristic", "ml_forecast")) -> dict[str, Any]:
    import psutil

    rows = []
    for sc in SPECS:
        for c in controllers:
            (r,) = run_many([(c, sc, seeds[sc], None, True, None, None, False)], workers=1)
            t = r["timing"]
            sim_s = r["metrics"]["sim_end_s"]
            per_s = np.array(r["perSecondMs"])
            rows.append({"scenario": sc, "controller": c, "seed": seeds[sc], "sim_seconds": sim_s, "wall_s": r["resources"]["wall_s"],
                         "cpu_s": r["resources"]["cpu_s"], "cpu_cores_avg": round(r["resources"]["cpu_s"] / max(1e-9, r["resources"]["wall_s"]), 2),
                         "peak_rss_mb": r["resources"]["peak_rss_mb"], "speed_x_realtime": round(sim_s / r["resources"]["wall_s"], 1),
                         "sumo_step": t["sumo_step"], "measure_and_frame": t["post_step"], "per_simulated_second_ms": {
                             "mean": round(float(per_s.mean()), 3), "p95": round(float(np.percentile(per_s, 95)), 3),
                             "p99": round(float(np.percentile(per_s, 99)), 3), "max": round(float(per_s.max()), 3)},
                         "observation": t["observation"], "inference": t["inference"], "decision": t["decide"],
                         "frames": len(r["frames"]), "vehicles_peak": max(len(f["v"]) for f in r["frames"]), "teleports": r["metrics"]["teleports"],
                         "collisions": r["metrics"]["collisions"]})
    ok = {
        "per_second_p99_under_250ms": all(r["per_simulated_second_ms"]["p99"] < 250 for r in rows),
        "decision_p99_under_5s": all(r["decision"]["p99_ms"] < 5000 for r in rows),
    }
    doc = {"machine": {"platform": platform.platform(), "logical_cores": os.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 1e9, 1)},
           "method": "one episode per fresh process, run alone, frames recorded; seed = illustrative demo seed", "git": git_sha(),
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "budget": {"per_simulated_second_ms": 1000, "decision_interval_ms": 5000, "step_budget_ms_for_0.25s_live_tick": 250},
           "episodes": rows, "checks": ok,
           "summary": {"max_peak_rss_mb": max(r["peak_rss_mb"] for r in rows), "max_cpu_cores_avg": max(r["cpu_cores_avg"] for r in rows),
                       "worst_p99_per_second_ms": max(r["per_simulated_second_ms"]["p99"] for r in rows),
                       "min_speed_x_realtime": min(r["speed_x_realtime"] for r in rows)}}
    (out_dir() / "performance.json").write_text(json.dumps(doc, indent=1))
    return doc
