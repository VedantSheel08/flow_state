"""Demo scenario command line.

    python -m flowstate.demo.cli probe --scenario demo_rush_hour --seeds 9001,9002     # design runs (design seeds only)
    python -m flowstate.demo.cli benchmark                                            # frozen multi-seed benchmark
    python -m flowstate.demo.cli perf                                                 # CPU / RAM / step-latency benchmark
    python -m flowstate.demo.cli manifest                                             # build artifacts/demo/scenario_manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys

from .scenarios import SPECS

CONTROLLERS = ("fixed_time", "queue_heuristic", "ml_forecast")
KEYS = ("mean_wait_s", "p95_travel_s", "throughput_vph", "queue_peak_veh", "queue_peak_pct_storage", "t_congested_s", "t_spillback_s",
        "spillback_pct", "spilled_links_peak", "min_speed_ms_30s_avg", "halted_share_peak", "t_recovery_half_s", "t_cleared_s",
        "completed_fraction", "never_inserted", "teleports", "teleported_vehicles", "collisions")


def probe(scenarios: list[str], seeds: list[int], controllers: tuple[str, ...], workers: int, intensity: float | None, out: str | None) -> None:
    from .analysis import analyse
    from .runner import run_many

    jobs = [(c, sc, sd, intensity, False) for sc in scenarios for sd in seeds for c in controllers]
    runs = run_many(jobs, workers)
    rows = []
    for r in runs:
        a = analyse(r)
        rows.append({"scenario": r["scenario"], "seed": r["seed"], "controller": r["controller"], "vehicles": r["demand"]["scheduled"],
                     "sha": r["demandSha"], "wall_s": r["resources"]["wall_s"], **{k: a.get(k) for k in KEYS}, "first_full": a.get("first_full_approach"),
                     "spill_link": a.get("spillback_first_link"), "incident": r["incident"]})
        print(json.dumps(rows[-1]))
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)


def show_markers(scenario: str, controller: str, seed: int) -> None:
    from .analysis import analyse, forecast_skill
    from .runner import run_demo_episode

    r = run_demo_episode(controller, scenario, seed)
    a = analyse(r)
    print(json.dumps({k: v for k, v in a.items() if k != "markers"}, indent=1))
    print(json.dumps(forecast_skill(r)))
    for m in a["markers"]:
        print(m)


def main() -> None:
    p = argparse.ArgumentParser(prog="flowstate.demo")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe")
    pr.add_argument("--scenario", default=",".join(SPECS))
    pr.add_argument("--seeds", default="9001")
    pr.add_argument("--controllers", default=",".join(CONTROLLERS))
    pr.add_argument("--workers", type=int, default=4)
    pr.add_argument("--intensity", type=float, default=None)
    pr.add_argument("--out", default=None)
    mk = sub.add_parser("markers")
    mk.add_argument("scenario")
    mk.add_argument("controller")
    mk.add_argument("seed", type=int)
    bm = sub.add_parser("benchmark")
    bm.add_argument("--workers", type=int, default=6)
    sub.add_parser("perf")
    sub.add_parser("manifest")
    a = p.parse_args()
    if a.cmd == "perf":
        from .benchmark import out_dir
        from .manifest import load, pick_demo_seed
        from .perf import run_perf

        rows = json.loads((out_dir() / "benchmark.json").read_text())["rows"]
        d = run_perf({sc: pick_demo_seed(rows, sc) for sc in SPECS})
        print(json.dumps({"summary": d["summary"], "checks": d["checks"]}, indent=1))
    elif a.cmd == "manifest":
        from .benchmark import out_dir
        from .manifest import write

        perf = out_dir() / "performance.json"
        doc = write(json.loads((out_dir() / "benchmark.json").read_text()), json.loads(perf.read_text()) if perf.exists() else None)
        print(json.dumps({"default": doc["defaultScenario"], "seeds": {s["id"]: s["lab"]["seed"] for s in doc["scenarios"]}}, indent=1))
    if a.cmd == "markers":
        show_markers(a.scenario, a.controller, a.seed)
    elif a.cmd == "benchmark":
        from .benchmark import run_benchmark

        d = run_benchmark(workers=a.workers)
        print(json.dumps({"episodes": d["episodes"], "wall_s": d["wall_s"], "severity": d["severityLadder"]}, indent=1))
    if a.cmd == "probe":
        probe(a.scenario.split(","), [int(x) for x in a.seeds.split(",")], tuple(a.controllers.split(",")), a.workers, a.intensity, a.out)


if __name__ == "__main__":
    sys.exit(main())
