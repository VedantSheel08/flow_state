"""FastAPI application: REST for control and queries, one WebSocket for streams."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import sumo_env
from ..network.geometry import network_geometry
from ..scenarios.catalog import list_specs
from ..sim.engine import backend_name
from ..sim.signals import SIGNAL_MODES
from ..sim.vtypes import type_summary
from ..performance.congestion import list_presets
from .service import NETWORKS_DIR, SPEEDS, SimulationService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
service = SimulationService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    service.bus.bind(asyncio.get_running_loop())
    pump = asyncio.create_task(service.bus.pump())
    yield
    pump.cancel()
    service.shutdown()


app = FastAPI(title="FlowState API", version="2.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class RunRequest(BaseModel):
    scenario: str
    params: dict[str, Any] = {}
    seed: int | None = None
    speed: float = 1.0
    congestion: str | None = None


class ControlRequest(BaseModel):
    action: str
    speed: float | None = None


class CompareRequest(BaseModel):
    scenario: str
    params: dict[str, Any] = {}
    seed: int
    key: str
    values: list[Any]


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e).strip("'\"")) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail="simulation did not respond in time") from e


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "sumo": sumo_env.sumo_version(), "backend": backend_name(), "speeds": SPEEDS, "signalModes": SIGNAL_MODES}


@app.get("/api/scenarios")
def scenarios() -> list[dict[str, Any]]:
    specs = list_specs()
    for spec in specs:
        for param in spec["params"]:
            if param["key"] == "duration":
                param["default"] = 5
    return specs


@app.get("/api/congestion-presets")
def congestion_presets() -> list[dict[str, Any]]:
    return list_presets()


@app.get("/api/vehicle-types")
def vehicle_types() -> list[dict[str, Any]]:
    return type_summary()


@app.get("/api/networks/{network_id}")
def network(network_id: str) -> Response:
    path = NETWORKS_DIR / f"{network_id}.net.xml"
    if not path.exists() or "/" in network_id or ".." in network_id:
        raise HTTPException(404, f"unknown network '{network_id}'")
    data = network_geometry(str(path), network_id)
    return Response(json.dumps(data, separators=(",", ":")), media_type="application/json", headers={"Cache-Control": "max-age=60"})


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict[str, Any]:
    return _guard(service.start_run, req.scenario, req.params, req.seed, req.speed, req.congestion)


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    return service.history()


@app.get("/api/runs/current")
def current() -> dict[str, Any]:
    return service.snapshot()


@app.post("/api/runs/current/control")
def control(req: ControlRequest) -> dict[str, Any]:
    return _guard(service.control, req.action, req.speed)


@app.post("/api/runs/current/params")
def live_params(params: dict[str, Any]) -> dict[str, Any]:
    return _guard(service.set_live_params, params)


@app.get("/api/runs/current/metrics")
def metrics() -> list[dict[str, Any]]:
    return service.metrics_history()


@app.get("/api/runs/current/vehicles/{vid}")
def vehicle(vid: str) -> dict[str, Any]:
    out = _guard(service.vehicle, vid)
    if out is None:
        raise HTTPException(404, "vehicle not in the simulation")
    return out


@app.get("/api/runs/current/junctions/{jid}")
def junction(jid: str) -> dict[str, Any]:
    out = _guard(service.junction, jid)
    if out is None:
        raise HTTPException(404, "junction not available")
    return out


@app.post("/api/compare")
def compare(req: CompareRequest) -> dict[str, Any]:
    return _guard(service.start_compare, req.scenario, req.params, req.seed, req.key, req.values)


@app.get("/api/compare/{job_id}")
def compare_job(job_id: str) -> dict[str, Any]:
    job = service.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    snap = service.snapshot()
    await websocket.send_text(json.dumps({"type": "hello", **snap}, default=str))
    service.bus.clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive pings from the client
    except WebSocketDisconnect:
        pass
    finally:
        service.bus.clients.discard(websocket)


# Single-origin hosting (Render): serve the built frontend from the API process.
# Off unless FLOWSTATE_STATIC_DIR points at a Vite build, so local dev is unchanged.
# Registered last so the catch-all never shadows /api or /ws.
_static = os.environ.get("FLOWSTATE_STATIC_DIR")
STATIC_DIR = Path(_static).resolve() if _static else None
if STATIC_DIR and (STATIC_DIR / "index.html").is_file():
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not found")
        f = (STATIC_DIR / path).resolve()
        if path and f.is_file() and f.is_relative_to(STATIC_DIR):
            return FileResponse(f)
        return FileResponse(STATIC_DIR / "index.html")  # client-side routes
