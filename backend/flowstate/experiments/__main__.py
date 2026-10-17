"""Command line for the experiment store.

    python -m flowstate.experiments status
    python -m flowstate.experiments schema [--dialect postgres|sqlite] [--timescale]
    python -m flowstate.experiments run --scenario rush_hour --controller actuated --seed 42 --params '{"duration":5}'
    python -m flowstate.experiments benchmark --suite rush-am --scenario rush_hour --controllers fixed,actuated --seeds 1,2,3
    python -m flowstate.experiments list [--scenario rush_hour]
    python -m flowstate.experiments compare --scenario rush_hour --baseline fixed
    python -m flowstate.experiments serve --port 8010
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import tables
from .store import database_url, open_store


def _json(x) -> None:
    print(json.dumps(x, indent=1, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flowstate.experiments")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("schema")
    s.add_argument("--dialect", default="postgres", choices=("postgres", "sqlite"))
    s.add_argument("--timescale", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--scenario", required=True)
    r.add_argument("--controller", default="fixed")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--params", default='{"duration": 5}')
    r.add_argument("--warmup", type=float, default=90.0)
    r.add_argument("--out", help="write the export JSON here")
    b = sub.add_parser("benchmark")
    b.add_argument("--suite", required=True)
    b.add_argument("--scenario", required=True)
    b.add_argument("--controllers", default="fixed,actuated")
    b.add_argument("--baseline", default="fixed")
    b.add_argument("--seeds", default="1,2,3")
    b.add_argument("--params", default='{"duration": 5}')
    b.add_argument("--warmup", type=float, default=90.0)
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--out")
    ls = sub.add_parser("list")
    ls.add_argument("--scenario")
    ls.add_argument("--controller")
    ls.add_argument("--limit", type=int, default=20)
    c = sub.add_parser("compare")
    c.add_argument("--scenario", required=True)
    c.add_argument("--baseline", default="fixed")
    c.add_argument("--suite")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8010)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if a.cmd == "schema":
        print(tables.schema_sql(a.dialect, hypertables=a.timescale))
        return 0
    if a.cmd == "status":
        st = open_store()
        _json({"configured": "postgres" if database_url() else "none (local SQLite)", "backend": st.backend if st else None, "timescaledb": bool(st and st.timescale),
               "experiments": len(st.list_experiments(limit=10000)) if st else None})
        return 0 if st else 1
    if a.cmd == "run":
        from .recorder import ExperimentSpec, run_experiment

        st = open_store()
        res = run_experiment(ExperimentSpec(a.scenario, a.seed, a.controller, json.loads(a.params), warmup=a.warmup), st)
        _json({"id": res.id, "status": res.status, "persistedTo": res.persisted, "persistError": res.persist_error, "wallSeconds": round(res.wall_s, 1),
               "summary": {k: v for k, v in res.summary.items() if k != "extra"}})
        if a.out:
            Path(a.out).write_text(json.dumps(res.export(), default=str), encoding="utf-8")
        return 0 if res.status == "completed" else 1
    if a.cmd == "benchmark":
        from .benchmark import run_benchmark

        m = run_benchmark(a.suite, a.scenario, a.controllers.split(","), [int(x) for x in a.seeds.split(",")], json.loads(a.params), baseline=a.baseline,
                          warmup=a.warmup, workers=a.workers, out_dir=Path(a.out) if a.out else None, progress=lambda f: print(f"{f:4.0%}", file=sys.stderr, flush=True))
        _json({k: m[k] for k in ("suite", "scenario", "controllers", "seeds", "wallSeconds", "storedIn", "reproduce")})
        return 0
    st = open_store()
    if st is None:
        print("no experiment store available", file=sys.stderr)
        return 1
    if a.cmd == "list":
        for r in st.list_experiments(scenario=a.scenario, controller=a.controller, limit=a.limit):
            print(f"{r['id'][:8]}  {r['scenario']:12s} {r['controller']:16s} seed={r['seed']:<5} {r['status']:9s} thr={r.get('throughput_per_h')} travel={r.get('mean_travel_s')} co2={r.get('co2_total_g')}")
        return 0
    if a.cmd == "compare":
        _json(st.compare_controllers(a.scenario, baseline=a.baseline, suite=a.suite))
        return 0
    if a.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(), host=a.host, port=a.port, log_level="info")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
