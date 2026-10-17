"""Live simulation service.

* One live run at a time, executed on a worker thread that owns the SUMO
  connection. All SUMO access (including inspector queries) happens on that
  thread via a small command queue.
* The worker paces SUMO against the wall clock at the requested speed and
  publishes frames (≤ 20 Hz), 1 Hz statistics and events to WebSocket clients.
* Every run is recorded in ``data/runs`` (scenario, params, seed, results) so it
  can be reproduced exactly.
* Comparison jobs run two headless variants of the same seed in separate
  processes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from ..scenarios.catalog import create
from ..sim.engine import Engine, RunConfig
from ..performance.congestion import engine_options, resolve
from ..performance.identity import identity

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
NETWORKS_DIR = DATA_DIR / "networks"
WORK_DIR = DATA_DIR / "work"
RUNS_DIR = Path(os.environ.get("FLOWSTATE_RUNS_DIR", str(DATA_DIR / "runs")))
SPEEDS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
MAX_PUBLISH_HZ = 20.0


class Broadcaster:
    """Thread-safe fan-out of JSON messages to WebSocket clients."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[str] | None = None
        self.clients: set[Any] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=256)

    def publish(self, message: dict[str, Any]) -> None:
        if self.loop is None or self.queue is None or not self.clients:
            return
        text = json.dumps(message, separators=(",", ":"))

        def _put() -> None:
            assert self.queue is not None
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self.queue.put_nowait(text)

        try:
            self.loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass

    async def pump(self) -> None:
        assert self.queue is not None
        while True:
            text = await self.queue.get()
            dead = []
            for ws in list(self.clients):
                try:
                    await ws.send_text(text)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)


class LiveRun(threading.Thread):
    def __init__(self, run_id: str, engine: Engine, info: dict[str, Any], bus: Broadcaster, speed: float, on_finish: Callable[["LiveRun"], None]):
        super().__init__(daemon=True, name=f"run-{run_id}")
        self.run_id = run_id
        self.engine = engine
        self.info = info
        self.bus = bus
        self.speed = speed
        self.state = "starting"
        self.error: str | None = None
        self.paused = False
        self._halt = threading.Event()
        self._cmds: list[tuple[Callable[[], Any], Future]] = []
        self._lock = threading.Lock()
        self._anchor: tuple[float, float] | None = None
        self.achieved = 0.0
        self._rate_window: list[tuple[float, float]] = []
        self.results: dict[str, Any] | None = None
        self.on_finish = on_finish
        self.events: list[dict[str, Any]] = []
        engine.events._listener = self._on_event

    # -- cross-thread calls ------------------------------------------------
    def call(self, fn: Callable[[], Any], timeout: float = 5.0) -> Any:
        if not self.is_alive():
            raise RuntimeError("simulation is not running")
        fut: Future = Future()
        with self._lock:
            self._cmds.append((fn, fut))
        return fut.result(timeout=timeout)

    def _drain(self) -> None:
        with self._lock:
            cmds, self._cmds = self._cmds, []
        for fn, fut in cmds:
            try:
                fut.set_result(fn())
            except Exception as e:  # noqa: BLE001
                fut.set_exception(e)

    def _on_event(self, ev) -> None:
        d = ev.to_dict()
        self.events.append(d)
        self.bus.publish({"type": "event", "run": self.run_id, "event": d})

    # -- control -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        eng = self.engine
        return {
            "type": "status", "run": self.run_id, "state": self.state, "paused": self.paused, "speed": self.speed,
            "achieved": round(self.achieved, 2), "t": round(eng.t, 2), "warmup": eng.warmup, "duration": eng.scenario.duration,
            "error": self.error, "stepMs": round(eng.wall_ms, 2),
        }

    def set_paused(self, paused: bool) -> None:
        if self.state == "finished":
            return
        self.paused = paused
        self._anchor = None
        self.bus.publish(self.status())

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.25, min(64.0, float(speed)))
        self._anchor = None
        self.bus.publish(self.status())

    def stop(self) -> None:
        self._halt.set()

    # -- loop ------------------------------------------------------------------
    def run(self) -> None:  # noqa: C901
        eng = self.engine
        try:
            eng.start()
            self.state = "warming"
            self.bus.publish(self.status())
            last_status = 0.0
            while not self._halt.is_set() and eng.t < 0:
                self._drain()
                eng.step()
                now = time.perf_counter()
                if now - last_status > 0.2:
                    last_status = now
                    self.bus.publish(self.status())
            self.state = "running"
            self.bus.publish(self.status())
            self._publish_frame()
            last_pub = 0.0
            last_stats_t = -1e9
            while not self._halt.is_set():
                self._drain()
                if self.paused or eng.finished:
                    time.sleep(0.02)
                    continue
                now = time.perf_counter()
                if self._anchor is None:
                    self._anchor = (now, eng.t)
                target = self._anchor[1] + (now - self._anchor[0]) * self.speed
                if eng.t + eng.dt > target + 1e-9:
                    time.sleep(min(0.004, (eng.t + eng.dt - target) / self.speed))
                    continue
                eng.step()
                if target - eng.t > max(2.0, self.speed * 0.5):
                    self._anchor = (time.perf_counter(), eng.t)  # cannot keep up: re-anchor
                self._rate_window.append((time.perf_counter(), eng.t))
                if len(self._rate_window) > 2 and self._rate_window[-1][0] - self._rate_window[0][0] > 2.0:
                    (w0, s0), (w1, s1) = self._rate_window[0], self._rate_window[-1]
                    self.achieved = (s1 - s0) / max(1e-6, w1 - w0)
                    self._rate_window = self._rate_window[-2:]
                now = time.perf_counter()
                if now - last_pub >= 1.0 / MAX_PUBLISH_HZ:
                    last_pub = now
                    self._publish_frame()
                if eng.t - last_stats_t >= 1.0 - 1e-6:
                    last_stats_t = eng.t
                    self._publish_stats()
                if eng.finished:
                    self._publish_frame()
                    self._publish_stats()
                    self.results = eng.results()
                    self.state = "finished"
                    self.bus.publish(self.status())
                    self.bus.publish({"type": "results", "run": self.run_id, "results": self.results})
                    self.on_finish(self)
        except Exception as e:  # noqa: BLE001
            log.exception("live run failed")
            self.error = f"{type(e).__name__}: {e}"
            self.state = "error"
            self.bus.publish(self.status())
        finally:
            if self.results is None and self.state not in ("error",) and eng.started and eng.T is not None:
                try:
                    self.results = eng.results()
                except Exception:  # noqa: BLE001
                    pass
            eng.close()

    def _publish_frame(self) -> None:
        f = self.engine.frame()
        f["run"] = self.run_id
        self.bus.publish(f)

    def _publish_stats(self) -> None:
        eng = self.engine
        self.bus.publish({"type": "stats", "run": self.run_id, "t": round(eng.t, 1), "kpi": eng.kpi, "edges": eng.edge_frame(), **eng.state(), "status": self.status()})


class SimulationService:
    def __init__(self) -> None:
        self.bus = Broadcaster()
        self.live: LiveRun | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # -- runs --------------------------------------------------------------
    def start_run(self, scenario_id: str, params: dict[str, Any], seed: int | None, speed: float = 1.0, congestion: str | None = None) -> dict[str, Any]:
        seed = int(seed) if seed is not None else random.randint(1, 99999)
        params, warmup, preset = resolve(params, congestion, scenario_id)
        scenario = create(scenario_id, params)  # raises ValueError on invalid params
        config = RunConfig(scenario_id, scenario.params, seed, signal_mode=scenario.params.get("signals", "fixed"), warmup=warmup, label="live", **engine_options(congestion))
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        engine = Engine(scenario, config, NETWORKS_DIR, WORK_DIR / "live")  # instantiates (draws) the scenario
        info = {"id": run_id, **scenario.describe(), "signalMode": config.signal_mode, "spec": scenario.spec.model_dump(), "created": time.time(),
                "warmup": warmup, "congestion": congestion, "congestionPreset": preset}
        info["reproducibility"] = identity(scenario_id, scenario.params, seed, NETWORKS_DIR,
                                           warmup=warmup, backend=os.environ.get("FLOWSTATE_BACKEND", "libsumo"))
        with self._lock:
            self._stop_live()
            run = LiveRun(run_id, engine, info, self.bus, speed, self._save_run)
            self.live = run
            run.start()
        self.bus.publish({"type": "run", "run": info})
        self._write(info, None)
        return info

    def _stop_live(self) -> None:
        if self.live is not None:
            old = self.live
            old.stop()
            old.join(timeout=10)
            if old.results is not None:
                self._save_run(old)
            self.live = None

    def _save_run(self, run: LiveRun) -> None:
        self._write(run.info, run.results)

    def _write(self, info: dict[str, Any], results: dict[str, Any] | None) -> None:
        rec = {k: info[k] for k in ("id", "scenario", "title", "seed", "params", "network", "briefing", "signalMode", "created") if k in info}
        rec["results"] = results
        rec.update({k: info[k] for k in ("warmup", "congestion", "congestionPreset", "reproducibility") if k in info})
        (RUNS_DIR / f"{info['id']}.json").write_text(json.dumps(rec, default=str), encoding="utf-8")

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        out = []
        for p in sorted(RUNS_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            res = rec.get("results") or {}
            last = res.get("last") or {}
            rec["summary"] = {"t": res.get("t"), "completed": (res.get("metrics") or {}).get("completed"), "timeLoss": (last.get("overall") or {}).get("timeLoss")}
            rec.pop("results", None)
            out.append(rec)
        return out

    def control(self, action: str, speed: float | None = None) -> dict[str, Any]:
        run = self.live
        if action == "restart":
            if run is None:
                raise RuntimeError("no run to restart")
            info = run.info
            return self.start_run(info["scenario"], info["params"], info["seed"], run.speed, info.get("congestion"))
        if run is None:
            raise RuntimeError("no active run")
        if action == "pause":
            run.set_paused(True)
        elif action == "resume":
            run.set_paused(False)
        elif action == "speed":
            if speed is None:
                raise ValueError("speed required")
            run.set_speed(speed)
        elif action == "relieve":  # the downstream restriction clears and the district is metered down: the jam discharges
            return {**run.status(), "relief": run.call(lambda: run.engine.relieve(), timeout=10.0)}
        elif action == "stop":
            with self._lock:
                self._stop_live()
            self.bus.publish({"type": "status", "run": None, "state": "idle"})
            return {"state": "idle"}
        else:
            raise ValueError(f"unknown action '{action}'")
        return run.status()

    def snapshot(self) -> dict[str, Any]:
        run = self.live
        if run is None:
            return {"run": None, "status": {"type": "status", "run": None, "state": "idle"}}
        return {"run": run.info, "status": run.status(), "events": run.events[-200:], "results": run.results}

    def metrics_history(self) -> list[dict[str, Any]]:
        run = self.live
        return [] if run is None else [{k: v for k, v in h.items() if k != "queues"} for h in run.engine.metrics.history]

    def set_live_params(self, params: dict[str, Any]) -> dict[str, Any]:
        run = self.live
        if run is None:
            raise RuntimeError("no active run")
        return run.call(lambda: run.engine.scenario.update_live(params))

    def vehicle(self, vid: str) -> dict[str, Any] | None:
        run = self.live
        if run is None or run.state not in ("running", "warming"):
            return None
        return run.call(lambda: run.engine.vehicle_detail(vid))

    def junction(self, jid: str) -> dict[str, Any] | None:
        run = self.live
        if run is None:
            return None
        if run.state not in ("running", "warming"):
            return None
        return run.call(lambda: run.engine.junction_detail(jid))

    # -- comparisons -----------------------------------------------------------
    def start_compare(self, scenario_id: str, params: dict[str, Any], seed: int, key: str, values: list[Any]) -> dict[str, Any]:
        if len(values) != 2:
            raise ValueError("exactly two variant values are required")
        params, _, _ = resolve(params, None, scenario_id)
        base = create(scenario_id, params)
        if key not in base.params:
            raise ValueError(f"'{key}' is not a parameter of {scenario_id}")
        for v in values:
            create(scenario_id, {**params, key: v})  # validate both variants up front
        job_id = uuid.uuid4().hex[:8]
        job = {"id": job_id, "scenario": scenario_id, "seed": seed, "params": base.params, "key": key, "values": values,
               "state": "running", "variants": [None, None], "progress": [0.0, 0.0], "started": time.time()}
        self.jobs[job_id] = job
        threading.Thread(target=self._run_compare, args=(job,), daemon=True).start()
        return job

    def _run_compare(self, job: dict[str, Any]) -> None:
        procs = []
        for i, v in enumerate(job["values"]):
            payload = {"scenario": job["scenario"], "params": {**job["params"], job["key"]: v}, "seed": job["seed"], "label": f"cmp{job['id']}{i}"}
            p = subprocess.Popen([sys.executable, "-m", "flowstate.cli", "run", "--json", json.dumps(payload)], cwd=str(ROOT / "backend"),
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
            procs.append(p)

        def reader(i: int, p: subprocess.Popen) -> None:
            assert p.stdout is not None
            for line in p.stdout:
                if line.startswith("PROGRESS "):
                    job["progress"][i] = float(line.split()[1])
                    self.bus.publish({"type": "job", "job": {k: job[k] for k in ("id", "state", "progress")}})
                elif line.startswith("RESULT "):
                    job["variants"][i] = json.loads(line[7:])
            p.wait()

        threads = [threading.Thread(target=reader, args=(i, p), daemon=True) for i, p in enumerate(procs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        job["state"] = "done" if all(job["variants"]) else "error"
        job["finished"] = time.time()
        self.bus.publish({"type": "job", "job": job})

    def shutdown(self) -> None:
        with self._lock:
            self._stop_live()
