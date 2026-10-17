"""FastAPI application: REST for control and queries, one WebSocket for streams."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
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

# Access policy (all optional; unset keeps the open local-development behaviour).
#   FLOWSTATE_ALLOWED_ORIGINS  comma-separated browser origins allowed for CORS and /ws
#   FLOWSTATE_ACCESS_TOKEN     if set, state-changing requests need "Authorization: Bearer <token>"
#   FLOWSTATE_MAX_WS_CLIENTS   cap on simultaneous WebSocket viewers (default 25)
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("FLOWSTATE_ALLOWED_ORIGINS", "").split(",") if o.strip()]
ACCESS_TOKEN = os.environ.get("FLOWSTATE_ACCESS_TOKEN", "")
MAX_WS_CLIENTS = int(os.environ.get("FLOWSTATE_MAX_WS_CLIENTS", "25"))

app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS or ["*"], allow_methods=["*"], allow_headers=["*"])


# Abuse limits, independent of the Origin header (which scripted clients can spoof).
#   FLOWSTATE_RATE_LIMIT        state-changing requests per client IP per minute (default 30)
#   FLOWSTATE_START_LIMIT       run/compare starts per client IP per minute (default 6)
#   FLOWSTATE_MAX_WS_PER_IP     simultaneous WebSocket viewers per client IP (default 4)
#   FLOWSTATE_MAX_DURATION_MIN  longest simulated run a client may request (default 15)
RATE_LIMIT = int(os.environ.get("FLOWSTATE_RATE_LIMIT", "30"))
START_LIMIT = int(os.environ.get("FLOWSTATE_START_LIMIT", "6"))
MAX_WS_PER_IP = int(os.environ.get("FLOWSTATE_MAX_WS_PER_IP", "4"))
MAX_DURATION_MIN = float(os.environ.get("FLOWSTATE_MAX_DURATION_MIN", "15"))
_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_ws_per_ip: dict[str, int] = defaultdict(int)


def _client_ip(request: Request | WebSocket) -> str:
    peer = request.client.host if request.client else "?"
    fwd = request.headers.get("x-forwarded-for")
    # Only trust the header when it comes from the local reverse proxy, which overwrites it.
    return fwd.split(",")[0].strip() if fwd and peer in ("127.0.0.1", "::1") else peer


def _over_limit(ip: str, bucket: str, limit: int) -> bool:
    now = time.monotonic()
    q = _hits[(ip, bucket)]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if ACCESS_TOKEN and request.headers.get("authorization") != f"Bearer {ACCESS_TOKEN}":
            return JSONResponse({"detail": "missing or invalid access token"}, status_code=401)
        ip = _client_ip(request)
        starts = request.url.path in ("/api/runs", "/api/compare")
        if _over_limit(ip, "write", RATE_LIMIT) or (starts and _over_limit(ip, "start", START_LIMIT)):
            return JSONResponse({"detail": "too many requests, slow down"}, status_code=429, headers={"Retry-After": "30"})
    return await call_next(request)


def _check_duration(params: dict[str, Any]) -> None:
    try:
        minutes = float(params.get("duration", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "duration must be a number") from None
    if minutes > MAX_DURATION_MIN:
        raise HTTPException(400, f"duration is limited to {MAX_DURATION_MIN:g} minutes on this server")


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
    _check_duration(req.params)
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
    _check_duration(req.params)
    if any(j.get("state") == "running" for j in service.jobs.values()):
        raise HTTPException(409, "a comparison is already running")
    return _guard(service.start_compare, req.scenario, req.params, req.seed, req.key, req.values)


@app.get("/api/compare/{job_id}")
def compare_job(job_id: str) -> dict[str, Any]:
    job = service.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if (ALLOWED_ORIGINS and origin not in ALLOWED_ORIGINS) or len(service.bus.clients) >= MAX_WS_CLIENTS:
        await websocket.close(code=1008)
        return
    ip = _client_ip(websocket)
    if _ws_per_ip[ip] >= MAX_WS_PER_IP:
        await websocket.close(code=1008)
        return
    _ws_per_ip[ip] += 1
    try:
        await _serve_ws(websocket)
    finally:
        _ws_per_ip[ip] -= 1


async def _serve_ws(websocket: WebSocket) -> None:
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
