"""Build the scenario manifest the Intelligence Lab consumes (``artifacts/demo/scenario_manifest.json``).

The manifest reuses the lab API's own vocabulary so a client needs no translation:

* ``id`` / ``title`` / ``description`` are exactly what ``GET /api/lab/status`` lists for the scenario;
* ``lab.pairRequest`` is a valid body for ``POST /api/lab/replays`` and ``lab.liveStart`` a valid ``/ws/live`` start message;
* controller ids are the lab's (``fixed_time``, ``queue_heuristic``, ``ml_forecast``).

Illustrative (one seed, for watching) and benchmark (ten seeds, for conclusions) numbers are kept in separate keys on purpose.
The illustrative seed is chosen by a demand-only rule (the benchmark seed whose vehicle count is the median), never by how a
controller performed on it.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

import numpy as np

from .analysis import RULES
from .benchmark import BENCH_SEEDS, DESIGN_SEEDS, git_sha, out_dir
from .scenarios import ALL_ENTRIES, ALL_EXITS, SPECS

SCHEMA = "flowstate.demo-scenarios/1"
TIER = {"demo_rush_hour": "heavy", "demo_spillback": "severe", "demo_incident": "gridlock"}
KIND = {"demo_rush_hour": "rush_hour", "demo_spillback": "asymmetric_spillback", "demo_incident": "incident"}
HEADLINE = ("mean_wait_s", "p95_travel_s", "throughput_vph", "queue_peak_veh", "spillback_pct", "spilled_links_peak", "near_standstill_seconds",
            "t_cleared_s", "teleports", "collisions", "completed_fraction", "never_inserted")


def pick_demo_seed(rows: list[dict[str, Any]], scenario: str) -> int:
    """Median demand: the benchmark seed whose scheduled vehicle count is nearest the median (ties: lowest seed). Outcome-blind."""
    v = {r["seed"]: r["vehicles"] for r in rows if r["scenario"] == scenario and r["controller"] == "fixed_time"}
    med = float(np.median(list(v.values())))
    return min(v, key=lambda s: (abs(v[s] - med), s))


def clarity(rows: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    """Visual-clarity checklist, computed over every seed under the non-ML controllers only (so no ML result can favour a scenario)."""
    sel = [r for r in rows if r["scenario"] == scenario and r["controller"] in ("fixed_time", "queue_heuristic")]
    a = [r["analysis"] for r in sel]
    lead = [x["queue_peak_t_s"] - x["t_queue_buildup_s"] for x in a if x.get("t_queue_buildup_s") is not None]
    first = [x["first_full_approach"]["approach"] for x in a if x.get("first_full_approach")]
    top = max(set(first), key=first.count) if first else None
    checks = {
        "visible_buildup": bool(lead) and float(np.median(lead)) >= 120.0,
        "localised_origin": bool(first) and first.count(top) / len(sel) >= 0.6,
        "recovers_in_run": float(np.mean([not r["t_cleared_censored"] for r in sel])) >= 0.9 and float(np.mean([x["completed_fraction"] for x in a])) >= 0.98,
        "clean_run": float(np.mean([x["teleports"] for x in a])) <= 1.0 and sum(x["collisions"] for x in a) == 0,
        "heavy_enough": float(np.median([x["queue_peak_pct_storage"] for x in a])) >= 15.0 and float(np.mean([x["spillback_pct"] for x in a])) >= 3.0,
    }
    return {"checks": checks, "score": sum(checks.values()), "median_buildup_to_peak_s": float(np.median(lead)) if lead else None,
            "most_common_first_full_approach": top, "share_with_that_origin": round(first.count(top) / len(sel), 2) if first else None}


def choose_default(rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    scores = {sc: clarity(rows, sc) for sc in SPECS}
    best = max(s["score"] for s in scores.values())
    tied = [sc for sc, s in scores.items() if s["score"] == best]
    # ties go to the middle severity tier, not to whichever scenario flatters a controller
    order = list(SPECS)
    pick = min(tied, key=lambda sc: abs(order.index(sc) - (len(order) - 1) / 2))
    return pick, scores


def marker_set(row: dict[str, Any], limit_interventions: int = 6) -> list[dict[str, Any]]:
    """Keep every state marker, and only the busiest ML-intervention clusters (the full log stays in the benchmark file)."""
    mk = row["markers"]
    keep = [m for m in mk if m["kind"] != "controller_intervention"]
    iv = sorted((m for m in mk if m["kind"] == "controller_intervention"), key=lambda m: -m["count"])[:limit_interventions]
    return sorted(keep + iv, key=lambda m: m["tSec"])


def build(bench: dict[str, Any], perf: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = bench["rows"]
    default, scores = choose_default(rows)
    scenarios = []
    for sc, spec in SPECS.items():
        seed = pick_demo_seed(rows, sc)
        by = {c: next(r for r in rows if r["scenario"] == sc and r["seed"] == seed and r["controller"] == c) for c in bench["controllers"]}
        b = bench["scenarios"][sc]
        wait = sorted(r["mean_wait_s"] for r in rows if r["scenario"] == sc and r["controller"] == "queue_heuristic")
        scenarios.append({
            "id": sc, "title": spec.title, "description": spec.description, "kind": KIND[sc], "tier": TIER[sc],
            "lab": {
                "scenario": sc, "seed": seed, "intensity": spec.intensity, "horizonS": spec.horizon_s, "drainS": spec.drain_s,
                "intensityRange": [0.5, 1.4], "defaultComparison": {"left": "queue_heuristic", "right": "ml_forecast"},
                "pairRequest": {"left": "queue_heuristic", "right": "ml_forecast", "scenario": sc, "seed": seed, "intensity": spec.intensity},
                "liveStart": {"cmd": "start", "scenario": sc, "controller": "queue_heuristic", "seed": seed, "intensity": spec.intensity, "speed": 4},
                "replayId": f"queue_heuristic__ml_forecast__{sc}__{seed}__{spec.intensity:g}",
            },
            "seeds": {"illustrative": seed, "benchmark": list(bench["seeds"]), "design": list(DESIGN_SEEDS),
                      "illustrativeRule": "benchmark seed with median scheduled vehicle count (demand only; outcome-blind)"},
            "demand": {"flows": [{"entry": f.entry, "vphAtPeakMultiplier1": f.vph, "toExits": {d: round(w, 3) for d, w in f.dests} or "uniform",
                                  "profile": [list(p) for p in f.profile], "note": f.note} for f in spec.flows],
                       "incidents": [asdict(i) for i in spec.incidents], "entries": list(ALL_ENTRIES), "exits": list(ALL_EXITS),
                       "vehiclesScheduled": by["fixed_time"]["vehicles"], "vehiclesPerSeed": b["vehicles"],
                       "demandSha256": by["fixed_time"]["demandSha"], "matchedAcrossControllers": len({r["demandSha"] for r in by.values()}) == 1,
                       "peakDemandVph": round(sum(f.vph for f in spec.flows) * max(m for f in spec.flows for _, m in f.profile), 0)},
            "expectedBehavior": list(spec.expected),
            "phases": [{"id": i, "fromS": a, "toS": z, "what": w} for i, a, z, w in spec.phases],
            "benchmark": {"kind": "multi-seed, paired, frozen protocol (conclusions)", "seeds": list(bench["seeds"]),
                          "byController": {c: {k: b["by_controller"][c][k] for k in HEADLINE} for c in bench["controllers"]},
                          "mlVsHeuristic": {k: b["ml_vs_heuristic"][k] for k in HEADLINE},
                          "heuristicVsFixed": {k: b["heuristic_vs_fixed"][k] for k in HEADLINE},
                          "forecast": b["forecast"], "clarity": scores[sc],
                          "heuristicMeanWaitRangeS": [wait[0], wait[-1]]},
            "illustrative": {"kind": "single seed, for watching only; not a general result", "seed": seed,
                             "byController": {c: {k: by[c][k] for k in HEADLINE} for c in by},
                             "waitPercentileAmongSeedsHeuristic": round(100 * sum(w <= by["queue_heuristic"]["mean_wait_s"] for w in wait) / len(wait), 0),
                             "incident": by["queue_heuristic"]["incident"], "demand": by["queue_heuristic"]["demand"]},
            "markers": {"rules": RULES, "seed": seed, "illustrative": {c: marker_set(by[c]) for c in ("queue_heuristic", "ml_forecast")}},
        })
    return {
        "schema": SCHEMA, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "git": git_sha(),
        "network": {"id": "rl_grid", "layout": "2x2 signalised grid, junctions A0 B0 A1 B1, 200 m two-lane links, 8 entries / 8 exits",
                    "compatibleWith": "flowstate.rl.sim.SignalSim / flowstate.lab (Intelligence Lab, port 8015)",
                    "notCompatibleWith": "Toronto district scenarios (different network)"},
        "api": {"scenarioList": "GET /api/lab/status -> scenarios[]", "manifest": "GET /api/lab/demo-scenarios",
                "record": "POST /api/lab/replays {left,right,scenario,seed,intensity}", "live": "WS /ws/live {cmd:'start', scenario, controller, seed, intensity, speed}",
                "controllers": ["fixed_time", "queue_heuristic", "ml_forecast"]},
        "defaultScenario": default,
        "defaultRationale": ("Highest visual-clarity checklist score computed on non-ML controllers over all benchmark seeds "
                             "(visible buildup, localised origin, recovery inside the run, clean run, heavy enough); ties go to the middle tier. "
                             "ML results play no part in the choice."),
        "recommendedSequence": ["demo_rush_hour", "demo_spillback", "demo_incident"],
        "markerKinds": ["queue_buildup", "spillback", "predicted_spillback", "controller_intervention", "recovery", "incident"],
        "benchmarkRef": {"file": "artifacts/demo/benchmark.json", "episodes": bench["episodes"], "seeds": list(bench["seeds"]),
                         "git": bench["git"], "generated_at": bench["generated_at"]},
        "performance": perf,
        "scenarios": scenarios,
    }


def write(bench: dict[str, Any], perf: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = build(bench, perf)
    (out_dir() / "scenario_manifest.json").write_text(json.dumps(doc, indent=1))
    return doc


def load() -> dict[str, Any] | None:
    f = out_dir() / "scenario_manifest.json"
    return json.loads(f.read_text()) if f.exists() else None
