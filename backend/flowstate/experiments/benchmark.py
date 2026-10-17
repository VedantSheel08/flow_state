"""Reproducible benchmark datasets from real SUMO runs.

A *suite* is: one scenario and parameter set x a list of controllers x a list of seeds. Every (controller, seed) pair is a
separate SUMO process, stored in the suite's own SQLite file (so the dataset is a self-contained artefact), mirrored to the
configured PostgreSQL/Tiger database when there is one, and exported as JSON for the dashboard:

    data/benchmarks/<suite>/
        manifest.json         what was run, with which code/SUMO versions; re-running it reproduces the numbers
        experiments.sqlite    the raw measurements (same schema as the database)
        experiments/<id>.json one full document per run (kpi series, heatmaps, signal decisions, safety, emissions)
        comparisons.json      controllers against the baseline: per-controller mean/std and paired per-seed deltas
        pairs/<seed>.<controller>.json  before-versus-after documents (aligned series, per-edge congestion difference)
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .export import SCHEMA_VERSION, export_comparison
from .recorder import _git_rev, controller_params, situation_hash
from .store import ROOT, SqliteStore, open_store

log = logging.getLogger(__name__)
BENCH_DIR = ROOT / "data" / "benchmarks"


def run_benchmark(suite: str, scenario: str, controllers: list[str], seeds: list[int], params: dict[str, Any] | None = None, *, baseline: str = "fixed",
                  warmup: float = 90.0, workers: int = 4, out_dir: Path | None = None, mirror_to_database: bool = True, progress=None) -> dict[str, Any]:
    params = {"duration": 5, **(params or {})}
    if baseline not in controllers:
        controllers = [baseline, *controllers]
    for c in controllers:  # fail before running anything if a controller cannot be expressed for this scenario
        controller_params(scenario, c, params)
    out = (Path(out_dir) if out_dir else BENCH_DIR / suite).resolve()  # absolute: the workers run in another working directory
    (out / "experiments").mkdir(parents=True, exist_ok=True)
    (out / "pairs").mkdir(exist_ok=True)
    local = SqliteStore(out / "experiments.sqlite")
    local.init_schema()
    stores: list[dict[str, str]] = [{"sqlite": str(out / "experiments.sqlite")}]
    remote = open_store() if mirror_to_database else None
    if remote is not None and remote.backend == "postgres":
        stores.append({"postgres": "env"})
    jobs = [(c, s) for s in seeds for c in controllers]
    t0 = time.time()
    results: dict[tuple[str, int], dict[str, Any]] = {}
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend") + os.pathsep + os.environ.get("PYTHONPATH", "")}

    def one(job: tuple[str, int]) -> tuple[tuple[str, int], dict[str, Any]]:
        c, s = job
        spec = {"scenario": scenario, "seed": s, "controller": c, "params": params, "warmup": warmup, "suite": suite, "tags": [suite], "stores": stores,
                "name": f"{suite}/{c}/seed{s}"}
        sp, op = out / f".spec_{c.replace('+', '_')}_{s}.json", out / f".out_{c.replace('+', '_')}_{s}.json"
        sp.write_text(json.dumps(spec), encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "flowstate.experiments.worker", str(sp), str(op)], env=env, capture_output=True, text=True, cwd=str(ROOT))
        if not op.exists():
            raise RuntimeError(f"worker for {c}/seed{s} produced nothing: {(p.stderr or p.stdout)[-400:]}")
        doc = json.loads(op.read_text(encoding="utf-8"))
        sp.unlink(missing_ok=True)
        op.unlink(missing_ok=True)
        if p.returncode != 0:
            raise RuntimeError(f"experiment {c}/seed{s} failed: {doc.get('error')}")
        return job, doc

    with cf.ThreadPoolExecutor(max(1, workers)) as ex:
        for i, (job, doc) in enumerate(ex.map(one, jobs)):
            results[job] = doc
            eid = doc["experiment"]["id"]
            (out / "experiments" / f"{eid}.json").write_text(json.dumps(doc, default=str), encoding="utf-8")
            if progress:
                progress((i + 1) / len(jobs))

    ids = {job: doc["experiment"]["id"] for job, doc in results.items()}
    ph = situation_hash(scenario, results[jobs[0]]["experiment"]["network"], results[jobs[0]]["experiment"]["params"], warmup)
    comparisons = local.compare_controllers(scenario, params_hash=ph, baseline=baseline, suite=suite)
    pairs = []
    for (c, s), eid in sorted(ids.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if c == baseline:
            continue
        doc = export_comparison(local, ids[(baseline, s)], eid)
        name = f"{s}.{c.replace('+', '_')}.json"
        (out / "pairs" / name).write_text(json.dumps(doc, default=str), encoding="utf-8")
        pairs.append({"seed": s, "controller": c, "baseline": ids[(baseline, s)], "candidate": eid, "file": f"pairs/{name}", "paired": doc["paired"]})
    (out / "comparisons.json").write_text(json.dumps({"schemaVersion": SCHEMA_VERSION, **comparisons, "pairs": pairs}, default=str), encoding="utf-8")
    manifest = {
        "schemaVersion": SCHEMA_VERSION, "suite": suite, "scenario": scenario, "params": params, "warmup": warmup, "controllers": controllers, "baseline": baseline,
        "seeds": seeds, "paramsHash": ph, "experiments": [{"id": ids[j], "controller": j[0], "seed": j[1], "file": f"experiments/{ids[j]}.json"} for j in jobs],
        "engine": next(iter(results.values()))["experiment"]["engine"], "revision": _git_rev(), "wallSeconds": round(time.time() - t0, 1),
        "storedIn": [list(s)[0] for s in stores], "reproduce": f"python -m flowstate.experiments benchmark --suite {suite} --scenario {scenario} --controllers {','.join(controllers)} "
                                                                 f"--seeds {','.join(map(str, seeds))} --warmup {warmup} --params '{json.dumps(params)}'",
        "note": "Re-running with the same seeds on the same SUMO and code revision reproduces the SUMO outputs; wall times and experiment ids differ.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    local.close()
    return manifest


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
