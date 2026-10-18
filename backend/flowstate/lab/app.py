"""FlowState Intelligence Lab API (standalone, default http://127.0.0.1:8015).

* ``/ws/live``           live SUMO session (one worker process), measured frames + 5 s observations/forecasts
* ``/api/lab/replays``   recorded paired runs for synchronized side-by-side playback; POST records a new pair in the background
* ``/api/lab/forecast``  held-out forecasting evaluation; ``/api/lab/comparison`` the paired multi-seed control evaluation
* ``/api/lab/ask``       Gemini interpretation through the existing guarded client; numbers are grounded; offline fallback

SUMO runs only in worker processes, Gemini only in a thread pool: neither can block the event loop or each other.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..rl.demand import SCENARIOS
from . import intel_api, studio_api
from . import record as R
from .control import CONTROLLERS
from .forecast import Forecaster, lab_dir

app = FastAPI(title="FlowState Intelligence Lab")
app.include_router(studio_api.router)  # read-only: results, ambulance stories, what the AI sees
app.include_router(intel_api.router)  # versioned intelligence contract (additive: the /api/lab/* endpoints are unchanged)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5176", "http://127.0.0.1:5176", "http://localhost:5177", "http://127.0.0.1:5177", "http://localhost:5178", "http://127.0.0.1:5178"], allow_methods=["*"], allow_headers=["*"])
CTX = mp.get_context("spawn")
_geometry: dict[str, Any] | None = None
_jobs: dict[str, dict[str, Any]] = {}
_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(2, mp_context=CTX)  # bounded: at most two background SUMO runs
        return _pool


def _read(name: str) -> dict[str, Any] | None:
    f = lab_dir() / name
    return json.loads(f.read_text()) if f.exists() else None


# ------------------------------------------------------------------------------------------------------------ static data
@app.get("/api/lab/status")
def status() -> dict[str, Any]:
    from . import health
    from .ask import guard_status

    h = health.check()
    return {"controllers": CONTROLLERS, "forecaster": h["usable"], "mlAvailable": h["usable"], "modelHealth": {"status": h["status"], "failed": h["failed"]},
            "scenarios": [{"id": s.id, "title": s.title, "description": s.description} for s in SCENARIOS.values()],
            "live": live.state(), "gemini": guard_status()}


def _geom_job() -> dict[str, Any]:
    from .sim import LabSim, geometry

    sim = LabSim("geom")
    return geometry(sim)


@app.get("/api/lab/geometry")
async def geometry() -> dict[str, Any]:
    global _geometry
    if _geometry is None:
        _geometry = await asyncio.wrap_future(pool().submit(_geom_job))
    return _geometry


@app.get("/api/lab/forecast")
def forecast_eval() -> dict[str, Any]:
    d = _read("forecast_eval.json")
    if d is None:
        raise HTTPException(404, "forecaster not trained yet")
    return d


@app.get("/api/lab/comparison")
def comparison_eval() -> dict[str, Any]:
    d = _read("comparison_eval.json")
    if d is None:
        raise HTTPException(404, "paired evaluation not run yet")
    return {k: v for k, v in d.items() if k != "rows"}


# ------------------------------------------------------------------------------------------------------------ replays
@app.get("/api/lab/replays")
def replays() -> dict[str, Any]:
    return {"replays": list(R.index().values())}


@app.get("/api/lab/replays/{rid}")
def replay(rid: str) -> Response:
    if "/" in rid or "\\" in rid or ".." in rid:
        raise HTTPException(400, "bad id")
    f = R.replays_dir() / f"{rid}.json.gz"
    if not f.exists():
        raise HTTPException(404, "no such replay")
    return Response(f.read_bytes(), media_type="application/json", headers={"Content-Encoding": "gzip", "Cache-Control": "max-age=3600"})


class PairRequest(BaseModel):
    left: Literal["fixed_time", "queue_heuristic", "ml_forecast", "queue_heuristic_ev", "ml_forecast_ev"] = "queue_heuristic"
    right: Literal["fixed_time", "queue_heuristic", "ml_forecast", "queue_heuristic_ev", "ml_forecast_ev"] = "ml_forecast_ev"
    scenario: str = "rush_ramp"
    seed: int = Field(3100, ge=0, le=99999)
    intensity: float = Field(1.0, ge=0.5, le=1.4)


@app.post("/api/lab/replays")
def new_pair(req: PairRequest) -> dict[str, Any]:
    if req.scenario not in SCENARIOS:
        raise HTTPException(400, "unknown scenario")
    if req.left == req.right:
        raise HTTPException(400, "pick two different controllers")
    if sum(1 for j in _jobs.values() if j["state"] == "running") >= 2:
        raise HTTPException(429, "two recordings are already running")
    jid = uuid.uuid4().hex[:8]
    _jobs[jid] = {"id": jid, "state": "running", "request": req.model_dump(), "started": time.time()}

    def run() -> None:
        try:
            _jobs[jid] |= {"state": "done", "result": R.record_pair(req.left, req.right, req.scenario, req.seed, req.intensity, pool())}
        except Exception as exc:  # noqa: BLE001
            _jobs[jid] |= {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
        _jobs[jid]["wall_s"] = round(time.time() - _jobs[jid]["started"], 1)

    threading.Thread(target=run, daemon=True).start()
    return _jobs[jid]


@app.get("/api/lab/jobs/{jid}")
def job(jid: str) -> dict[str, Any]:
    if jid not in _jobs:
        raise HTTPException(404, "no such job")
    return _jobs[jid]


# ------------------------------------------------------------------------------------------------------------ Gemini
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    replay: str | None = Field(None, max_length=200)
    live: bool = False


@app.post("/api/lab/ask")
async def ask(req: AskRequest) -> dict[str, Any]:
    from .ask import answer

    snap = live.snapshot() if req.live else None
    return await asyncio.to_thread(answer, req.question, req.replay, snap)


# ------------------------------------------------------------------------------------------------------------ live session
class Live:
    def __init__(self) -> None:
        self.proc: Any = None
        self.cmd_q: Any = None
        self.out_q: Any = None
        self.clients: set[WebSocket] = set()
        self.cfg: dict[str, Any] = {}
        self.status = "idle"
        self.paused = False
        self.last_frame: dict[str, Any] | None = None
        self.last_obs: dict[str, Any] | None = None
        self.last_end: dict[str, Any] | None = None
        self.started_msg: dict[str, Any] | None = None
        self.ws_ms: list[float] = []
        self.frames_out = 0
        self.pump: asyncio.Task | None = None
        self.sid = 0  # session id: every message carries it so a browser can ignore a replaced session's tail

    def state(self) -> dict[str, Any]:
        ws = sorted(self.ws_ms[-500:])
        return {"status": self.status, "paused": self.paused, "config": self.cfg, "clients": len(self.clients), "framesSent": self.frames_out,
                "wsSendMs": {"mean": round(sum(ws) / len(ws), 3) if ws else 0.0, "p95": round(ws[int(0.95 * (len(ws) - 1))], 3) if ws else 0.0}}

    def snapshot(self) -> dict[str, Any] | None:
        if not self.last_frame:
            return None
        k = self.last_frame["k"]
        return {"scenario": self.cfg.get("scenario"), "controller": self.cfg.get("controller"), "t": self.last_frame["t"],
                "kpis": dict(zip(R.KPI_FIELDS, k)), "obs": self.last_obs}

    def intel(self) -> dict[str, Any] | None:
        """Latest v1 report of the live session plus the compact snapshot Gemini may be given."""
        o = self.last_obs
        if not o or "intel" not in o:
            return None
        return {**o["intel"], "session": {"status": self.status, "paused": self.paused, "sid": self.sid, "config": self.cfg}, "_snapshot": self.snapshot()}

    async def start(self, cfg: dict[str, Any]) -> None:
        await self.stop()
        self.cmd_q, self.out_q = CTX.Queue(), CTX.Queue(maxsize=256)
        from .live import worker

        self.sid += 1
        self.cfg, self.status, self.paused, self.last_end = cfg, "starting", False, None
        self.proc = CTX.Process(target=worker, args=(self.cmd_q, self.out_q, cfg), daemon=True)
        self.proc.start()
        self.pump = asyncio.create_task(self._pump(self.out_q, self.sid))

    async def command(self, msg: dict[str, Any]) -> None:
        if self.cmd_q is None or self.status not in ("running", "starting"):
            return
        self.cmd_q.put(msg)
        if msg["cmd"] == "camera":
            self.cfg["camera"] = msg["junction"]
        if msg["cmd"] in ("pause", "resume"):
            self.paused = msg["cmd"] == "pause"
            await self.broadcast(json.dumps({"type": "state", **self.state()}))
        elif msg["cmd"] == "speed":
            self.cfg["speed"] = msg["value"]

    async def stop(self) -> None:
        if self.proc is not None and self.proc.is_alive():
            self.cmd_q.put({"cmd": "stop"})
            await asyncio.to_thread(self.proc.join, 3.0)
            if self.proc.is_alive():
                self.proc.terminate()
        self.proc = None

    async def _pump(self, q: Any, sid: int) -> None:
        import queue as Q

        def get() -> list[dict[str, Any]]:
            try:
                first = q.get(timeout=0.25)
            except Q.Empty:
                return []
            batch = [first]
            while len(batch) < 64:
                try:
                    batch.append(q.get_nowait())
                except Q.Empty:
                    break
            return batch

        while True:
            batch = await asyncio.to_thread(get)
            if not batch:
                if self.proc is None or not self.proc.is_alive() or sid != self.sid:
                    if self.status in ("running", "starting") and sid == self.sid:
                        self.status = "idle"
                    return
                continue
            for m in batch:
                m["sid"] = sid
            current = sid == self.sid
            frames = [m for m in batch if m["type"] == "frame"]
            for m in batch:
                if m["type"] == "frame":
                    continue
                if not current:
                    pass
                elif m["type"] == "started":
                    self.status, self.started_msg = "running", m
                elif m["type"] == "obs":
                    self.last_obs = m
                elif m["type"] in ("done", "stopped", "error"):
                    self.status, self.last_end, self.paused = ("error" if m["type"] == "error" else "finished"), m, False
                await self.broadcast(json.dumps(m))
            if frames and current:  # if the browser fell behind, only the newest frame matters for display
                self.last_frame = frames[-1]
                await self.broadcast(json.dumps(frames[-1]), frame=True)

    async def broadcast(self, text: str, frame: bool = False) -> None:
        dead = []
        for ws in list(self.clients):
            t0 = time.perf_counter()
            try:
                await asyncio.wait_for(ws.send_text(text), 1.0)
            except Exception:  # noqa: BLE001
                dead.append(ws)
                continue
            if frame:
                self.ws_ms.append((time.perf_counter() - t0) * 1000.0)
                self.frames_out += 1
        for ws in dead:
            self.clients.discard(ws)
        if len(self.ws_ms) > 5000:
            del self.ws_ms[:-1000]


live = Live()
intel_api.live_provider = live.intel


@app.websocket("/ws/live")
async def live_ws(ws: WebSocket) -> None:
    await ws.accept()
    live.clients.add(ws)
    await ws.send_text(json.dumps({"type": "state", **live.state()}))
    if live.started_msg and live.status == "running":
        await ws.send_text(json.dumps(live.started_msg))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            cmd = msg.get("cmd")
            if cmd == "start":
                sc = msg.get("scenario", "rush_ramp")
                ctl = msg.get("controller", "queue_heuristic")
                if sc not in SCENARIOS or ctl not in CONTROLLERS:
                    await ws.send_text(json.dumps({"type": "error", "error": "unknown scenario or controller"}))
                    continue
                if ctl == "ml_forecast" and Forecaster.load_default() is None:
                    await ws.send_text(json.dumps({"type": "error", "error": "forecaster not trained yet"}))
                    continue
                await live.start({"scenario": sc, "controller": ctl, "seed": int(msg.get("seed", 3100)) % 100000,
                                  "intensity": max(0.5, min(1.4, float(msg.get("intensity", 1.0)))), "speed": max(0.25, min(32.0, float(msg.get("speed", 4)))),
                                  "perception": bool(msg.get("perception", False)), "camera": str(msg.get("camera", "A0"))})
            elif cmd == "camera":
                await live.command({"cmd": "camera", "junction": str(msg.get("junction", "A0"))})
            elif cmd in ("pause", "resume"):
                await live.command({"cmd": cmd})
            elif cmd == "speed":
                await live.command({"cmd": "speed", "value": max(0.25, min(32.0, float(msg.get("value", 4))))})
            elif cmd == "stop":
                await live.stop()
    except WebSocketDisconnect:
        pass
    finally:
        live.clients.discard(ws)


@app.get("/api/lab/perf")
def perf() -> dict[str, Any]:
    end = live.last_end or {}
    obs = live.last_obs or {}
    return {"live": live.state(), "workerTiming": obs.get("timing") or end.get("timing"), "lastRun": {k: end.get(k) for k in ("type", "dropped", "t")},
            "forecastInference": (_read("forecast_eval.json") or {}).get("inference_ms_per_decision")}


@app.on_event("shutdown")
async def _shutdown() -> None:
    await live.stop()
    if _pool is not None:
        _pool.shutdown(cancel_futures=True)
