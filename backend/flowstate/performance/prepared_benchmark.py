"""Measure ahead-of-click preparation versus claiming a frozen LIVE engine."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from .prepared import PreparedRun
from .benchmark import ROOT, WORKLOADS


def signature(frame):
    return hashlib.sha256(json.dumps(frame, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    reports = []
    for i in range(args.repeats):
        start = time.perf_counter()
        with PreparedRun("rush_hour", {**WORKLOADS["rush_hour"], "duration": 5}, 42, ROOT / "data/networks") as run:
            prepare = time.perf_counter() - start
            start = time.perf_counter()
            run.claim()
            claim = time.perf_counter() - start
            frozen = run.call("frame")
            start = time.perf_counter()
            later = run.call("advance", 40)
            continuation = time.perf_counter() - start
            result = run.call("results")
            report = {"repeat": i, "key": run.identity["key"], "prepareWallSeconds": prepare,
                      "nativeInitAndWarmupSeconds": run.ready["seconds"], "claimWallSeconds": claim,
                      "continuation10SimSecondsWall": continuation, "readyVehicles": len(frozen["v"]),
                      "readyTrajectorySHA256": signature(frozen), "laterTrajectorySHA256": signature(later["frame"]),
                      "clockFrozenUntilAdvance": frozen == run.ready["frame"], "results": result,
                      "manifest": run.identity["manifest"]}
            reports.append(report)
            (args.output.parent / "transport-frame.json").write_text(json.dumps(later["frame"]), encoding="utf-8")
            args.output.write_text(json.dumps(reports, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items() if k not in ("manifest", "results")}), flush=True)


if __name__ == "__main__":
    main()
