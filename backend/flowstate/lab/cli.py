"""Intelligence Lab command line.

    python -m flowstate.lab.cli dataset            # real SUMO runs -> data/lab/dataset.npz (+ artifacts/lab/dataset_manifest.json)
    python -m flowstate.lab.cli train              # forecaster + held-out evaluation -> artifacts/lab/forecast_eval.json
    python -m flowstate.lab.cli tune               # forecast-aware control parameters on training seeds
    python -m flowstate.lab.cli evaluate           # paired held-out comparison -> artifacts/lab/comparison_eval.json
    python -m flowstate.lab.cli record             # paired replays for the browser -> data/lab/replays/
    python -m flowstate.lab.cli serve --port 8015
"""

from __future__ import annotations

import argparse
import json


def main() -> None:
    p = argparse.ArgumentParser(prog="flowstate.lab")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dataset").add_argument("--workers", type=int, default=4)
    sub.add_parser("train")
    sub.add_parser("tune").add_argument("--workers", type=int, default=4)
    sub.add_parser("evaluate").add_argument("--workers", type=int, default=4)
    r = sub.add_parser("record")
    r.add_argument("--workers", type=int, default=4)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8015)
    a = p.parse_args()
    if a.cmd == "dataset":
        from .forecast import generate

        m = generate(a.workers)
        print(json.dumps({"rows": m["rows"], "wall_s": m["wall_s"]}))
    elif a.cmd == "train":
        from .forecast import train

        m = train()
        print(json.dumps({k: m[k] for k in ("chosen", "rows", "test", "inference_ms_per_decision")}, indent=1))
    elif a.cmd == "tune":
        from .evaluate import tune

        print(json.dumps(tune(a.workers), indent=1))
    elif a.cmd == "evaluate":
        from .evaluate import evaluate_paired

        print(json.dumps(evaluate_paired(a.workers)["summary"], indent=1))
    elif a.cmd == "record":
        from .record import record_defaults

        print(json.dumps(record_defaults(a.workers), indent=1))
    elif a.cmd == "serve":
        import uvicorn

        uvicorn.run("flowstate.lab.app:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
