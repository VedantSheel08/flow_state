"""Compact asset for the main app's Results page: one recorded pair, fixed-time signals (left) vs FlowState (right).

Everything in it is measured by SUMO in the recorded run (positions, signal states, per-ambulance stops) or by the paired 60-episode held-out evaluation
(``artifacts/lab/comparison_eval.json``). Nothing is recomputed or invented here; the builder only repackages, trims fields and picks which ambulance to show first
(by a stated rule, with every ambulance listed so the page can show the others).

``python -m flowstate.lab.cli demo-asset --pair <replay id> --out <dir>`` writes ``pair.json.gz`` and ``grid-network.json`` (the map description for main's renderer).
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
from pathlib import Path
from typing import Any

from .evaluate import paired
from .forecast import lab_dir
from .record import replays_dir

BEST_CASE_STOP_S = 3.0  # the default example is an ambulance that FlowState held for at most this long
AGG_KEYS = ("ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s", "mean_wait_civilian_s", "mean_travel_s", "queue_total_mean_veh", "throughput_vph")
SCENARIO_TITLES = {"balanced": "Balanced", "ns_heavy": "North-south heavy", "ew_heavy": "East-west heavy", "pulse": "Alternating pulses", "rush_ramp": "Rush-hour ramp"}


def _units(run: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out = {}
    for u in run["metrics"].get("ev_units", []):
        out[int(u["id"][1:])] = {"travelS": u["travel_s"], "stoppedRedS": u["stopped_red_s"], "stoppedQueueS": u["stopped_queue_s"], "finished": u["finished"], "departS": u["depart_s"]}
    return out


def _span(frames: list[dict[str, Any]], vid: int) -> tuple[int, int] | None:
    seen = [f["t"] for f in frames if any(e[0] == vid for e in f.get("e", []))]
    return (min(seen), max(seen)) if seen else None


def build(pair_id: str) -> dict[str, Any]:
    doc = json.loads(gzip.decompress((replays_dir() / f"{pair_id}.json.gz").read_bytes()))
    left, right = doc["left"], doc["right"]
    lu, ru = _units(left), _units(right)
    units = []
    for vid in sorted(set(lu) & set(ru), key=lambda v: lu[v]["departS"]):
        ls, rs = _span(left["frames"], vid), _span(right["frames"], vid)
        units.append({"id": vid, "left": lu[vid] | {"first": ls[0] if ls else None, "last": ls[1] if ls else None},
                      "right": ru[vid] | {"first": rs[0] if rs else None, "last": rs[1] if rs else None},
                      "savedS": round(lu[vid]["travelS"] - ru[vid]["travelS"], 1)})
    usable = [u for u in units if u["left"]["finished"] and u["right"]["finished"] and u["left"]["first"] is not None and u["right"]["first"] is not None]
    best = [u for u in usable if u["right"]["stoppedRedS"] + u["right"]["stoppedQueueS"] <= BEST_CASE_STOP_S and u["savedS"] > 0]
    example = max(best or usable, key=lambda u: u["savedS"])["id"] if usable else None

    def side(run: dict[str, Any]) -> dict[str, Any]:
        return {"controller": run["controller"], "frames": [{"t": f["t"], "v": [r[:6] for r in f["v"]], "s": f["s"], **({"e": f["e"]} if f.get("e") else {})} for f in run["frames"]]}

    def totals(run: dict[str, Any]) -> dict[str, Any]:
        m = run["metrics"]
        return {k: m[k] for k in ("ev_total", "ev_unfinished", "ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s", "mean_wait_civilian_s", "mean_travel_s", "throughput_vph",
                                  "queue_total_mean_veh", "teleports", "collisions")}

    ev = json.loads((lab_dir() / "comparison_eval.json").read_text())
    agg = paired(ev["rows"], "fixed_time", "ml_forecast_ev")
    return {"schema": "flowstate.results-demo/1", "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "pair": pair_id, "scenario": doc["scenario"], "scenarioTitle": SCENARIO_TITLES.get(doc["scenario"], doc["scenario"]),
            "seed": doc["seed"], "intensity": doc["intensity"], "demandSha": doc["demandSha"], "bestCaseStopS": BEST_CASE_STOP_S,
            "left": side(left), "right": side(right), "ambulances": units, "example": example, "totals": {"left": totals(left), "right": totals(right)},
            "aggregate": {"episodes": ev["summary"]["episodes"], "seeds": ev["summary"]["seeds"], "metrics": {k: agg[k] for k in AGG_KEYS}},
            "rightIncludes": "adaptive signals + queue forecast + green corridor along the ambulance route (rule-based; only the forecaster is a trained model)"}


def write(pair_id: str, out: Path) -> dict[str, Any]:
    from .mapgeom import build as build_geom

    out.mkdir(parents=True, exist_ok=True)
    asset = build(pair_id)
    (out / "pair.json.gz").write_bytes(gzip.compress(json.dumps(asset, separators=(",", ":")).encode(), 9))
    grid = lab_dir() / "grid-network.json"
    if not grid.exists():
        build_geom(grid)
    shutil.copy2(grid, out / "grid-network.json")
    return {"pair": pair_id, "example": asset["example"], "ambulances": len(asset["ambulances"]), "frames": len(asset["left"]["frames"]),
            "bytes": (out / "pair.json.gz").stat().st_size, "sides": [asset["left"]["controller"], asset["right"]["controller"]]}
