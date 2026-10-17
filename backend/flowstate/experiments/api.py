"""HTTP API for experiment history, comparisons, heatmaps and (validated) experiment runs.

Mount it in the existing FastAPI app with one line, ``app.include_router(flowstate.experiments.api.router)``, or run it
stand-alone: ``python -m flowstate.experiments serve --port 8010``. It never returns database credentials, and
experiments started through it run in a separate worker process (SUMO is not run inside the web server).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import store as S
from .export import SCHEMA_VERSION, export_comparison, export_experiment
from .recorder import CONTROLLERS, controller_params
from .store import ExperimentStore, KPI_METRICS, ROOT, SUMMARY_METRICS

router = APIRouter(prefix="/api/experiments", tags=["experiments"])

_store: ExperimentStore | None = None
_store_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
MAX_RUNNING_JOBS = 2


def get_store() -> ExperimentStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = S.open_store()
    if _store is None:
        raise HTTPException(503, "no experiment store available (set FLOWSTATE_DATABASE_URL or leave unset for the local SQLite file)")
    return _store


def set_store(store: ExperimentStore | None) -> None:
    """Use a specific store (tests, or an application that manages its own connection)."""
    global _store
    _store = store


class RunRequest(BaseModel):
    scenario: str
    controller: str = "fixed"
    seed: int = Field(42, ge=0, le=2**31 - 1)
    params: dict[str, Any] = Field(default_factory=lambda: {"duration": 5})
    warmup: float = Field(90.0, ge=0, le=900)
    suite: str | None = None
    name: str | None = None


def validate_run(req: RunRequest) -> dict[str, Any]:
    """Raise ``ValueError`` unless ``req`` is a supported scenario/controller/parameter combination; return the resolved parameters."""
    from ..scenarios.catalog import create

    params = controller_params(req.scenario, req.controller, req.params)
    sc = create(req.scenario, params)
    if not sc.duration:
        raise ValueError("set a finite run length ('duration' in minutes)")
    if sc.duration > 3600:
        raise ValueError("experiments are limited to 60 simulated minutes")
    return sc.params


def start_job(req: RunRequest) -> dict[str, Any]:
    if sum(1 for j in _jobs.values() if j["status"] == "running") >= MAX_RUNNING_JOBS:
        raise HTTPException(429, f"{MAX_RUNNING_JOBS} experiments are already running")
    try:
        params = validate_run(req)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    jid = uuid.uuid4().hex[:12]
    work = ROOT / "data" / "work" / "jobs"
    work.mkdir(parents=True, exist_ok=True)
    stores: list[dict[str, str]] = [{"sqlite": str(os.environ.get("FLOWSTATE_EXPERIMENT_DB") or S.DEFAULT_SQLITE)}]
    if S.database_url():
        stores = [{"postgres": "env"}, *stores]  # the database first, the local file as the fallback copy
    spec = {"scenario": req.scenario, "seed": req.seed, "controller": req.controller, "params": req.params, "warmup": req.warmup, "suite": req.suite,
            "name": req.name, "stores": stores}
    sp, op = work / f"{jid}.spec.json", work / f"{jid}.out.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen([sys.executable, "-m", "flowstate.experiments.worker", str(sp), str(op)], env=env, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    job = {"id": jid, "status": "running", "started": time.time(), "request": req.model_dump(), "resolvedParams": params, "out": str(op), "experimentId": None, "error": None}
    _jobs[jid] = job

    def watch() -> None:
        output, _ = proc.communicate()
        if op.exists():
            doc = json.loads(op.read_text(encoding="utf-8"))
            job["experimentId"] = (doc.get("experiment") or {}).get("id")
            job["run"] = doc.get("run")
        job["status"] = "completed" if proc.returncode == 0 else "failed"
        if proc.returncode != 0:
            job["error"] = (output or "")[-600:]
        job["finished"] = time.time()
        sp.unlink(missing_ok=True)

    threading.Thread(target=watch, daemon=True).start()
    return {k: job[k] for k in ("id", "status", "resolvedParams")}


@router.get("/status")
def status() -> dict[str, Any]:
    st = get_store()
    return {"schemaVersion": SCHEMA_VERSION, "backend": st.backend, "timescaledb": bool(st.timescale), "configured": "postgres" if S.database_url() else "local",
            "controllers": sorted(CONTROLLERS), "kpiMetrics": KPI_METRICS, "summaryMetrics": SUMMARY_METRICS,
            "experiments": len(st.list_experiments(limit=100000))}


@router.get("")
def list_experiments(scenario: str | None = None, controller: str | None = None, suite: str | None = None, status: str | None = None,
                     seed: int | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    return {"experiments": get_store().list_experiments(scenario=scenario, controller=controller, suite=suite, status=status, seed=seed, limit=limit, offset=offset)}


@router.get("/compare")
def compare(baseline: str, candidate: str, step: float = Query(5.0, ge=1, le=300)) -> dict[str, Any]:
    try:
        return export_comparison(get_store(), baseline, candidate, kpi_step_s=step)
    except KeyError:
        raise HTTPException(404, "unknown experiment id")


@router.get("/controllers")
def controllers(scenario: str, baseline: str = "fixed", params_hash: str | None = None, suite: str | None = None) -> dict[str, Any]:
    """Controllers on identical situations: per-controller mean/std over seeds, paired per-seed deltas against ``baseline``."""
    return get_store().compare_controllers(scenario, params_hash=params_hash, baseline=baseline, suite=suite)


@router.post("/run", status_code=202)
def run(req: RunRequest) -> dict[str, Any]:
    return start_job(req)


@router.get("/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    j = _jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "unknown job")
    return {k: v for k, v in j.items() if k != "out"}


@router.get("/{experiment_id}")
def get_experiment(experiment_id: str, detail: str = Query("full", pattern="^(full|summary)$"), step: float | None = Query(None, ge=1, le=300),
                   heat_bucket: float = Query(30.0, ge=5, le=300)) -> dict[str, Any]:
    st = get_store()
    try:
        if detail == "summary":
            exp = st.get_experiment(experiment_id)
            if exp is None:
                raise KeyError(experiment_id)
            return exp
        return export_experiment(st, experiment_id, kpi_step_s=step, heat_bucket_s=heat_bucket)
    except KeyError:
        raise HTTPException(404, "unknown experiment id")


@router.get("/{experiment_id}/kpi")
def kpi(experiment_id: str, metrics: str | None = None, step: float | None = Query(None, ge=1, le=300)) -> dict[str, Any]:
    try:
        rows = get_store().kpi_series(experiment_id, metrics.split(",") if metrics else None, step_s=step)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"series": rows}


@router.get("/{experiment_id}/heatmap")
def heatmap(experiment_id: str, metric: str = "halting", bucket: float = Query(30.0, ge=5, le=300), top: int | None = Query(60, ge=1, le=500)) -> dict[str, Any]:
    try:
        return {"cells": get_store().edge_heatmap(experiment_id, metric, bucket, top=top)}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/{experiment_id}/signals")
def signals(experiment_id: str, tls: str | None = None, kind: str | None = None, limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
    st = get_store()
    return {"stats": st.signal_decision_stats(experiment_id), "events": st.signal_events(experiment_id, tls, kind, limit)}


@router.get("/{experiment_id}/safety")
def safety(experiment_id: str, kind: str | None = None) -> dict[str, Any]:
    st = get_store()
    return {"counts": st.safety_counts(experiment_id), "events": st.safety_events(experiment_id, kind)}


def create_app() -> FastAPI:
    app = FastAPI(title="FlowState experiments")
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])
    app.include_router(router)
    try:
        from .assistant_api import router as assistant_router

        app.include_router(assistant_router)
    except ImportError:  # the optional assistant is not installed
        pass
    return app


def mount(app: FastAPI) -> None:
    """Add the experiment routes to an existing FastAPI app."""
    app.include_router(router)
