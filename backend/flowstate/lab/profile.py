"""Profile the intelligence pipeline under heavy SUMO traffic -> ``artifacts/lab/performance.json``.

One ML-controlled episode at stress demand. Per decision interval (5 s) it times, separately: SUMO steps, TrafficState collection, forecaster
inference, contract-report construction, JSON serialisation of the report and of a frame (what the WebSocket carries), and samples the process's
resident memory. Also reports peak vehicles in the network. All values are wall-clock measurements on the machine that ran it.
"""

from __future__ import annotations

import json
import os
import platform
import time
from typing import Any

import numpy as np
import psutil

from ..rl import config as C
from . import health
from .contract import build_report, model_block, observed_extra
from .control import make_controller
from .forecast import lab_dir
from .sim import LabSim


def _stats(v: list[float]) -> dict[str, float]:
    a = np.asarray(v) if v else np.zeros(1)
    return {"mean_ms": round(float(a.mean()), 3), "p50_ms": round(float(np.percentile(a, 50)), 3), "p95_ms": round(float(np.percentile(a, 95)), 3),
            "max_ms": round(float(a.max()), 3), "n": len(v)}


def _micro(sim: LabSim, ctl: Any, n_inf: int = 300, n_obs: int = 150) -> dict[str, Any]:
    """Repeat the hot calls at the current (heavy) traffic state. Wall time is contention-sensitive; CPU time (aggregated over many calls, because
    Windows thread timers tick at ~15 ms) is not. TrafficState.sample is repeated on the live simulation; inference on the live feature matrix."""
    st = sim.state
    saved = (st.q_hist.copy(), st.c_hist.copy(), st.samples, st.X.copy(), sim.forecast, list(sim.obs_ms), list(sim.infer_ms))
    ctl_saved = {k: (list(v) if isinstance(v, list) else v) for k, v in vars(ctl).items()}
    X = st.X.copy()
    out: dict[str, Any] = {"vehicles_in_network": len(sim.live)}
    for name, fn, n in (("traffic_state_collection", lambda: sim.state.sample(sim), n_obs), ("forecast_inference", lambda: sim.forecaster.predict_full(X), n_inf),
                        ("controller_decide", lambda: ctl.decide(sim), n_inf // 3)):
        c0, w0 = time.thread_time(), time.perf_counter()
        for _ in range(n):
            fn()
        out[name] = {"cpu_ms_per_call": round((time.thread_time() - c0) / n * 1000, 3), "wall_ms_per_call": round((time.perf_counter() - w0) / n * 1000, 3), "calls": n}
    st.q_hist, st.c_hist, st.samples, st.X, sim.forecast, sim.obs_ms, sim.infer_ms = saved  # the repeated calls must not leak into the profiled run
    vars(ctl).update(ctl_saved)
    return out


def run_one(scenario: str, intensity: float, seed: int = 3100) -> dict[str, Any]:
    proc = psutil.Process(os.getpid())
    sim = LabSim("profile", record=True)
    try:
        sim.reset(scenario, seed, 900.0, 300.0, intensity)
        ctl, info = make_controller("ml_forecast", sim)
        ctl.reset(sim)
        model = model_block(health.status())
        rss0 = proc.memory_info().rss / 2**20
        psutil.cpu_percent(None)
        cpu, rss, veh, rep_ms, rep_json_ms, frame_json_ms, rep_bytes, frame_bytes, loop_ms = ([] for _ in range(9))
        t_run = time.perf_counter()
        micro: dict[str, Any] | None = None
        while not sim.done:
            t0 = time.perf_counter()
            sim.observe()
            if micro is None and len(sim.live) >= 400:
                micro = _micro(sim, ctl)
            for jid, a in zip(C.CONTROLLED, ctl.decide(sim)):
                if a:
                    sim.request(jid)
            t1 = time.perf_counter()
            fc = sim.forecast
            rep = build_report(t=sim.t, queues=sim.state.X[:, 0].tolist(), extra=observed_extra(sim.state.X), source="live",
                               forecast={k: v.tolist() for k, v in fc.items() if k in ("p30", "p60", "lo30", "hi30", "lo60", "hi60")}, model=model,
                               controller={"id": "ml_forecast", "mlInControl": True}, decisions=ctl.last_evidence)
            t2 = time.perf_counter()
            body = json.dumps(rep, separators=(",", ":"))
            t3 = time.perf_counter()
            rep_ms.append((t2 - t1) * 1000), rep_json_ms.append((t3 - t2) * 1000), rep_bytes.append(len(body))
            for _ in range(int(C.DECISION_S)):
                sim.advance(C.STEP_S)
                fr = sim.frames.pop()
                sim.frames.clear()
                t4 = time.perf_counter()
                fb = json.dumps({"type": "frame", **fr}, separators=(",", ":"))
                frame_json_ms.append((time.perf_counter() - t4) * 1000), frame_bytes.append(len(fb))
            loop_ms.append((time.perf_counter() - t0) * 1000 - (time.perf_counter() - t3) * 0)  # whole 5 s interval incl. 5 steps
            veh.append(len(sim.live))
            rss.append(proc.memory_info().rss / 2**20)
            cpu.append(psutil.cpu_percent(None))
        wall = time.perf_counter() - t_run
        t = sim.timing()
        out = {"scenario": scenario, "seed": seed, "intensity": intensity, "controller": "ml_forecast",
               "simulated_s": sim.t, "wall_s": round(wall, 1), "realtime_factor": round(sim.t / wall, 1),
               "peak_vehicles_in_network": int(max(veh)), "mean_vehicles_in_network": round(float(np.mean(veh)), 1),
               "sumo_step": t["sumo_step"], "observation_collection": t["observation"], "forecast_inference": t["inference"],
               "report_build": _stats(rep_ms), "report_json_serialise": _stats(rep_json_ms), "frame_json_serialise": _stats(frame_json_ms),
               "decision_interval_total": _stats(loop_ms), "microbenchmark_at_heavy_traffic": micro,
               "payload_bytes": {"report_mean": int(np.mean(rep_bytes)), "report_max": int(np.max(rep_bytes)), "frame_mean": int(np.mean(frame_bytes)), "frame_max": int(np.max(frame_bytes))},
               "memory_mb": {"rss_start": round(rss0, 1), "rss_peak": round(max(rss), 1), "rss_end": round(rss[-1], 1), "growth_over_run": round(rss[-1] - rss0, 1),
                             "includes": "python + libsumo + scikit-learn model, one process; replay frames are held in memory in this profile"},
               "budget": {"decision_interval_ms": C.DECISION_S * 1000, "note": "one decision interval of simulated time; 'decision_interval_total' must be far below real-time pace at the chosen speed"},
               "machine": {"platform": platform.platform(), "logical_cores": os.cpu_count(), "python": platform.python_version(),
                           "system_cpu_percent_during_run": {"mean": round(float(np.mean(cpu)), 1), "max": round(float(np.max(cpu)), 1)},
                           "caveat": "the machine is shared with other processes; when system CPU is near 100% every latency here is inflated"},
               "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        return out
    finally:
        sim.close()


def run(scenario: str = "rush_ramp", intensity: float = 1.4, write: bool = True) -> dict[str, Any]:
    """Profile at standard demand and at ``intensity`` (heavy), in separate processes-worth of state (each run resets SUMO)."""
    runs = [run_one(scenario, i) for i in dict.fromkeys([1.0, intensity])]
    out = {"schema": "flowstate.performance/1", "runs": runs}
    if write:
        (lab_dir() / "performance.json").write_text(json.dumps(out, indent=1))
    return out
