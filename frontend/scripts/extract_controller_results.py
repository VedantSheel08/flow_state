"""Build frontend/src/data/controllerResults.json from the RL branch's stored held-out evaluations.

Usage (repository root):
    python frontend/scripts/extract_controller_results.py <ref>
where <ref> is a git ref that carries artifacts/rl/evaluation_{standard,stress}.json (e.g. origin/task/rl-signal-control).
Numbers are copied, never recomputed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

METRICS = [
    "mean_wait_s", "mean_travel_s", "p95_travel_s", "throughput_vph", "completed_fraction", "queue_mean_veh",
    "queue_max_veh", "spillback_pct", "teleports", "collisions", "emergency_mean_stopped_s",
]

SCENARIOS = [
    {"id": "balanced", "title": "Balanced", "description": "Equal demand on all eight entries.", "heldOut": False},
    {"id": "ns_heavy", "title": "North-south heavy", "description": "North and south entries carry about three times the east-west demand.", "heldOut": False},
    {"id": "ew_heavy", "title": "East-west heavy", "description": "East and west entries carry about three times the north-south demand.", "heldOut": False},
    {"id": "pulse", "title": "Alternating pulses", "description": "The dominant direction flips every 240 s.", "heldOut": False},
    {"id": "rush_ramp", "title": "Rush-hour ramp", "description": "All entries ramp up to 1.5x, hold and ease. Never used for tuning or training.", "heldOut": True},
]


def ctrl_id(raw: str) -> str:
    return raw.split("@", 1)[0]


def slim_stats(block: dict) -> dict:
    out = {}
    for k in METRICS:
        m = block["metrics"].get(k)
        if m is None:
            continue
        row = {"mean": m["mean"], "std": m["std"], "min": m["min"], "max": m["max"]}
        vs = m.get("vs_baseline")
        if vs:
            row["vs"] = {"diff": vs["mean_diff"], "ci95": vs["ci95"], "pct": vs["pct_change"], "p": vs.get("wilcoxon_p"),
                         "better": vs["better_in"], "worse": vs["worse_in"], "paired": vs["paired"]}
        out[k] = row
    return {"n": block["n"], "metrics": out}


def slim_group(group: dict) -> dict:
    return {ctrl_id(c): slim_stats(v) for c, v in group.items()}


def load(ref: str, name: str) -> dict:
    raw = subprocess.check_output(["git", "show", f"{ref}:artifacts/rl/{name}.json"])
    return json.loads(raw)


def main(ref: str) -> None:
    commit = subprocess.check_output(["git", "rev-parse", "--short", ref], text=True).strip()
    demand = {}
    for key, name in (("standard", "evaluation_standard"), ("stress", "evaluation_stress")):
        d = load(ref, name)
        s = d["summary"]
        demand[key] = {
            "intensity": d["meta"]["intensity"],
            "seeds": d["meta"]["seeds"],
            "episodes": s["episodes"],
            "wallSeconds": d["meta"].get("wall_seconds"),
            "overall": slim_group(s["overall"]),
            "perScenario": {sc: slim_group(g) for sc, g in s["per_scenario"].items()},
            "runs": [
                {"c": ctrl_id(e["controller"]), "s": e["scenario"], "seed": e["seed"], "wait": round(e["mean_wait_s"], 2),
                 "travel": round(e["mean_travel_s"], 2), "queueMax": e["queue_max_veh"], "teleports": e["teleports"]}
                for e in d["episodes"]
            ],
        }
    out = {
        "source": {"ref": ref, "commit": commit, "files": ["artifacts/rl/evaluation_standard.json", "artifacts/rl/evaluation_stress.json"]},
        "network": {"id": "rl_grid", "description": "2 x 2 signalised grid, two-lane streets, 200 m blocks, 8 entry/exit stubs"},
        "baseline": "fixed_time",
        "flowstate": "queue_heuristic",
        "controllers": {
            "fixed_time": {"title": "Fixed-time", "detail": "Pre-timed plan, 30 s green per stage; ignores traffic.", "status": "baseline"},
            "queue_heuristic": {"title": "FlowState adaptive", "detail": "Queue-responsive: switches when the waiting stage has clearly more demand, gaps out empty greens, holds when the exit is full and gives way to emergency vehicles.", "status": "verified"},
            "rl_ppo": {"title": "RL policy (PPO)", "detail": "Trained in SUMO. Failed the publication gate, so it is shown for reference only.", "status": "experimental"},
        },
        "safety": {"minGreen": 14, "yellow": 4, "allRed": 2, "maxGreen": 60, "decision": 5},
        "scenarios": SCENARIOS,
        "demand": demand,
    }
    dest = Path(__file__).resolve().parents[1] / "src" / "data" / "controllerResults.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {dest} ({dest.stat().st_size // 1024} KB) from {ref} @ {commit}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "origin/task/rl-signal-control")
