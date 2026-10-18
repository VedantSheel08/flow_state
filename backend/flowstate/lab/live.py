"""Live SUMO session for the lab, in its own process (libsumo allows one simulation per process).

The worker steps SUMO one simulated second at a time, paced to the requested speed, and pushes measured frames to a bounded
queue. If the browser side cannot keep up, frames are dropped (counted), never the simulation slowed. Commands arrive on a
second queue: pause, resume, speed, stop.
"""

from __future__ import annotations

import queue
import time
import traceback
from typing import Any

from ..rl import config as C


def worker(cmd_q: Any, out_q: Any, cfg: dict[str, Any]) -> None:
    from .control import make_controller
    from .sim import LabSim

    sim = None
    try:
        sim = LabSim("live", record=True)
        manifest = sim.reset(cfg["scenario"], int(cfg["seed"]), float(cfg.get("horizon", 900)), float(cfg.get("drain", 600)),
                             float(cfg.get("intensity", 1.0)))
        ctl, info = make_controller(cfg["controller"], sim)
        ctl.reset(sim)
        out_q.put({"type": "started", "manifest": {k: v for k, v in manifest.items() if k != "sha256"} | {"demandSha": manifest["sha256"]},
                   "controller": info, "forecaster": sim.forecaster.meta if sim.forecaster else None})
        speed, paused, dropped = float(cfg.get("speed", 4.0)), False, 0
        wall_ref, sim_ref = time.perf_counter(), sim.t
        while not sim.done:
            try:
                while True:
                    c = cmd_q.get_nowait()
                    if c["cmd"] == "stop":
                        out_q.put({"type": "stopped", "t": sim.t, "metrics": sim.metrics(), "timing": sim.timing()})
                        return
                    if c["cmd"] == "pause":
                        paused = True
                    elif c["cmd"] == "resume":
                        paused, wall_ref, sim_ref = False, time.perf_counter(), sim.t
                    elif c["cmd"] == "speed":
                        speed, wall_ref, sim_ref = max(0.25, min(32.0, float(c["value"]))), time.perf_counter(), sim.t
            except queue.Empty:
                pass
            if paused:
                time.sleep(0.05)
                continue
            if int(sim.t) % int(C.DECISION_S) == 0:
                sim.observe()
                for jid, a in zip(C.CONTROLLED, ctl.decide(sim)):
                    if a:
                        sim.request(jid)
                fc = sim.forecast
                msg: dict[str, Any] = {"type": "obs", "t": sim.t, "q": sim.state.X[:, 0].round(1).tolist()}
                if fc is not None:
                    msg |= {"p30": fc["p30"].round(1).tolist(), "p60": fc["p60"].round(1).tolist()}
                if hasattr(ctl, "last_changed"):
                    msg["mlChanged"] = ctl.last_changed
                    msg["mlStats"] = ctl.stats()
                msg["timing"] = sim.timing()
                _put(out_q, msg)
            sim.advance(C.STEP_S)
            frame = sim.frames.pop()
            sim.frames.clear()
            if not _put(out_q, {"type": "frame", **frame}):
                dropped += 1
            # pace: simulated time should not run ahead of wall time x speed
            ahead = (sim.t - sim_ref) / speed - (time.perf_counter() - wall_ref)
            if ahead > 0:
                time.sleep(ahead)
        out_q.put({"type": "done", "t": sim.t, "metrics": sim.metrics(), "timing": sim.timing(), "dropped": dropped})
    except Exception as exc:  # noqa: BLE001 - reported to the browser instead of dying silently
        out_q.put({"type": "error", "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]})
    finally:
        if sim is not None:
            sim.close()


def _put(q: Any, msg: dict[str, Any]) -> bool:
    try:
        q.put_nowait(msg)
        return True
    except queue.Full:
        return False
