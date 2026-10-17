"""Opt-in prepared LIVE engines, paused at scenario t=0 in isolated processes.

This avoids lossy SUMO snapshot restoration: the process retains native and
Python RNGs, pending departures, subscriptions, incidents and controller state.
Claim consumes an entry. Every future step still executes SUMO. This module is
an explicit backend integration API; the existing REST service is unchanged.
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import shutil
from pathlib import Path
import tempfile
import time

from .identity import identity


def _worker(pipe, request):
    os.environ["FLOWSTATE_BACKEND"] = request["backend"]
    from ..scenarios.catalog import create
    from ..sim.engine import Engine, RunConfig
    engine = None
    work = tempfile.mkdtemp(prefix="flowstate-prepared-")
    try:
        scenario = create(request["scenario"], request["params"])
        engine = Engine(scenario, RunConfig(request["scenario"], scenario.params, request["seed"],
                        scenario.params.get("signals", "fixed"), warmup=request["warmup"]),
                        Path(request["networks"]), Path(work))
        start = time.perf_counter()
        engine.start()
        while engine.t < -1e-9:
            engine.step()
        pipe.send({"ok": True, "value": {"t": engine.t, "seconds": time.perf_counter() - start,
                    "pid": os.getpid(), "scenario": scenario.describe(), "frame": engine.frame()}})
        while True:
            op, value = pipe.recv()
            if op == "close":
                break
            try:
                if op == "advance":
                    if type(value) is not int or not 1 <= value <= 256:
                        raise ValueError("steps must be an integer from 1 to 256")
                    for _ in range(value):
                        if engine.finished:
                            break
                        engine.step()
                    result = {"frame": engine.frame(), "kpi": engine.kpi, "finished": engine.finished}
                elif op == "frame":
                    result = engine.frame()
                elif op == "results":
                    result = engine.results()
                elif op == "params":
                    result = engine.scenario.update_live(value)
                else:
                    raise ValueError(f"unknown operation: {op}")
                pipe.send({"ok": True, "value": result})
            except Exception as exc:
                pipe.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            pipe.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        except (EOFError, BrokenPipeError):
            pass
    finally:
        if engine is not None:
            engine.close()
        pipe.close()
        shutil.rmtree(work, ignore_errors=True)


class PreparedRun:
    """Owns one frozen worker; call close() or use a context manager.

    Calls must be serialized by the service's owning thread. Preparation can
    happen before a click; construction waits for readiness and reports time.
    """

    def __init__(self, scenario: str, params: dict, seed: int, networks: Path, *, warmup: float = 180,
                 backend: str = "libsumo", timeout: float = 120):
        if type(seed) is not int or not 0 <= seed < 2**31:
            raise ValueError("seed must be a nonnegative 31-bit integer")
        if not math.isfinite(warmup) or not 0 <= warmup <= 1800 or warmup % 0.25:
            raise ValueError("warmup must be 0..1800 seconds in quarter-second steps")
        if backend not in ("libsumo", "traci"):
            raise ValueError("unknown backend")
        self.request = dict(scenario=scenario, params=dict(params), seed=seed, networks=str(networks.resolve()),
                            warmup=warmup, backend=backend)
        self.identity = identity(scenario, params, seed, networks, warmup=warmup, backend=backend)
        context = mp.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, self.request), daemon=True)
        self.closed = False
        self.claimed = False
        self.created = time.monotonic()
        self.process.start()
        child.close()
        try:
            self.ready = self._receive(timeout)
        except BaseException:
            self.close()
            raise

    def _receive(self, timeout):
        if not self.pipe.poll(timeout):
            raise TimeoutError("prepared SUMO worker did not respond")
        response = self.pipe.recv()
        if not response["ok"]:
            raise RuntimeError(response["error"])
        return response["value"]

    def claim(self, *, max_age: float = 600):
        if self.closed or self.claimed:
            raise RuntimeError("prepared run already consumed or closed")
        current = identity(self.request["scenario"], self.request["params"], self.request["seed"],
                           Path(self.request["networks"]), warmup=self.request["warmup"], backend=self.request["backend"])
        if current["key"] != self.identity["key"] or time.monotonic() - self.created > max_age:
            self.close()
            raise RuntimeError("prepared state expired or inputs changed; prepare a fresh run")
        self.claimed = True
        return self

    def call(self, operation: str, value=None, *, timeout: float = 60):
        if self.closed or not self.claimed:
            raise RuntimeError("claim the prepared run before advancing it")
        self.pipe.send((operation, value))
        try:
            return self._receive(timeout)
        except (TimeoutError, EOFError, BrokenPipeError):
            # A timed-out response must never be mistaken for the next command.
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.is_alive():
            try:
                self.pipe.send(("close", None))
            except (EOFError, BrokenPipeError, OSError):
                pass
            self.process.join(timeout=3)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=3)
        self.pipe.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
