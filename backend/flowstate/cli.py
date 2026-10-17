"""FlowState command line.

    python -m flowstate.cli build-network
    python -m flowstate.cli serve [--port 8000]
    python -m flowstate.cli scenarios
    python -m flowstate.cli run --scenario accident --seed 42 [--params '{"severity": "severe"}']
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def headless(scenario_id: str, params: dict, seed: int, label: str = "cli", progress=None) -> dict:
    from .api.service import NETWORKS_DIR, WORK_DIR
    from .scenarios.catalog import create
    from .sim.engine import Engine, RunConfig

    sc = create(scenario_id, params)
    eng = Engine(sc, RunConfig(scenario_id, sc.params, seed, sc.params.get("signals", "fixed"), label=label), NETWORKS_DIR, WORK_DIR / label)
    events = []
    eng.events._listener = lambda e: events.append(e.to_dict())
    eng.start()
    dur = sc.duration or 600.0
    last = -1.0
    try:
        while not eng.finished:
            eng.step()
            if progress and eng.t - last >= 10:
                last = eng.t
                progress(max(0.0, eng.t) / dur)
        res = eng.results()
    finally:
        eng.close()
    res["history"] = [{k: v for k, v in h.items() if k not in ("queues",)} for h in eng.metrics.history[::5]]
    res["events"] = events
    res["describe"] = sc.describe()
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flowstate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-network", help="generate the SUMO networks")
    s = sub.add_parser("serve", help="run the API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("scenarios", help="list scenarios")
    r = sub.add_parser("run", help="headless run; prints results JSON")
    r.add_argument("--scenario")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--params", default="{}")
    r.add_argument("--json", help="payload {scenario, params, seed, label} (used by comparison jobs)")
    args = ap.parse_args(argv)

    if args.cmd == "build-network":
        from .api.service import NETWORKS_DIR
        from .network.city import build_all

        for p in build_all(NETWORKS_DIR):
            print(f"built {p}")
        return 0
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("flowstate.api.app:app", host=args.host, port=args.port, log_level="info")
        return 0
    if args.cmd == "scenarios":
        from .scenarios.catalog import list_specs

        for s_ in list_specs():
            print(f"{s_['id']:14s} {s_['title']}")
        return 0
    if args.cmd == "run":
        if args.json:
            payload = json.loads(args.json)
            res = headless(payload["scenario"], payload.get("params") or {}, int(payload.get("seed", 42)), payload.get("label", "cli"),
                           progress=lambda f: print(f"PROGRESS {f:.3f}", flush=True))
            print("RESULT " + json.dumps(res, default=str), flush=True)
        else:
            res = headless(args.scenario, json.loads(args.params), args.seed, progress=lambda f: print(f"{f:5.0%}", file=sys.stderr, flush=True))
            res.pop("history", None)
            print(json.dumps(res, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
