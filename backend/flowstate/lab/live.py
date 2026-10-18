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
    from . import health
    from .contract import build_report, model_block, observed_extra
    from .control import make_controller
    from .perception import Perception, poses

    EV_TEXT = ["moving", "stopped at a red light", "stuck behind queued cars", "moving, green light ahead"]  # measured from SUMO state, see LabSim._track_emergency
    from .sim import LabSim

    sim = None
    try:
        sim = LabSim("live", record=True)
        manifest = sim.reset(cfg["scenario"], int(cfg["seed"]), float(cfg.get("horizon", 900)), float(cfg.get("drain", 600)),
                             float(cfg.get("intensity", 1.0)))
        ctl, info = make_controller(cfg["controller"], sim)
        ctl.reset(sim)
        health.check()  # populates the status shown inside every prediction block
        model_desc = model_block(health.status())
        out_q.put({"type": "started", "manifest": {k: v for k, v in manifest.items() if k != "sha256"} | {"demandSha": manifest["sha256"]},
                   "controller": info, "forecaster": sim.forecaster.meta if sim.forecaster else None})
        percep = Perception() if cfg.get("perception") else None  # what the forecaster / PPO / camera see: read-only, bounded to every 5 s
        camera_jid, poses_prev, poses_cur = cfg.get("camera") if cfg.get("camera") in C.CONTROLLED else C.CONTROLLED[0], None, None
        speed, paused, dropped = float(cfg.get("speed", 4.0)), False, 0
        wall_ref, sim_ref = time.perf_counter(), sim.t
        while not sim.done:
            try:
                while True:
                    c = cmd_q.get_nowait()
                    if c["cmd"] == "stop":
                        out_q.put({"type": "stopped", "t": sim.t, "metrics": sim.metrics(), "timing": sim.timing(), "dropped": dropped})
                        return
                    if c["cmd"] == "pause":
                        paused = True
                    elif c["cmd"] == "resume":
                        paused, wall_ref, sim_ref = False, time.perf_counter(), sim.t
                    elif c["cmd"] == "camera" and c.get("junction") in C.CONTROLLED:
                        camera_jid = c["junction"]
                    elif c["cmd"] == "speed":
                        speed, wall_ref, sim_ref = max(0.25, min(32.0, float(c["value"]))), time.perf_counter(), sim.t
            except queue.Empty:
                pass
            if paused:
                time.sleep(0.05)
                continue
            if percep is not None:
                poses_prev, poses_cur = poses_cur, poses(sim)  # poses one second apart: the camera tells stopped from moving by comparing them
            if int(sim.t) % int(C.DECISION_S) == 0:
                sim.observe()
                actions = ctl.decide(sim)
                for jid, a in zip(C.CONTROLLED, actions):
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
                if percep is not None:
                    msg["perception"] = {"forecaster": percep.forecaster_view(sim), "ppo": percep.ppo_view(sim, list(actions)),
                                         "camera": percep.camera_view(sim, camera_jid, poses_cur or [], poses_prev, sim.t)}
                msg["intel"] = build_report(t=sim.t, queues=sim.state.X[:, 0].tolist(), extra=observed_extra(sim.state.X), source="live",
                                            forecast=fc and {k: v.tolist() for k, v in fc.items() if k in ("p30", "p60", "lo30", "hi30", "lo60", "hi60")},
                                            model=model_desc if fc else None, context={"scenario": cfg["scenario"], "seed": int(cfg["seed"])},
                                            controller={"id": cfg["controller"], "mlInControl": bool(info.get("mlInControl")), "emergencyGreen": bool(info.get("emergencyGreen"))},
                                            emergency=[{"id": v, "status": code, "state": EV_TEXT[code], "metresToNextSignal": round(d, 1) if d >= 0 else None} for v, (code, d) in sim.ev_status.items()],
                                            decisions=getattr(ctl, "last_evidence", []))
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
