"""Instrumented episodes for the demo scenarios: the lab's own simulation and controllers, plus the extra measurements the
demo package reports (queue/speed series, spillback origin, teleport and collision identities, dropped demand, resources).

Nothing here changes how traffic is simulated or controlled. ``DemoSim`` only *reads* SUMO state after each step, and every
controller goes through the lab's ``make_controller`` and the SafeSignal layer exactly as in the lab's recorder.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import numpy as np

from ..lab.control import make_controller
from ..lab.sim import LabSim
from ..rl import config as C
from ..rl.sim import T, tc
from .scenarios import SPECS

SERIES_S = 5.0  # one series sample per control decision
SPILL_OCC = 0.5  # an internal link counts as spilled when its halted vehicles fill half its storage (as in the lab's spillback %)
FORECAST_SPILL = 0.5  # the forecaster's own spillback definition: an approach queued over half its storage


class DemoSim(LabSim):
    """``LabSim`` that also records who teleported, which collisions happened and when the incident vehicles are stopped."""

    def reset(self, scenario: str, seed: int, horizon: float | None = None, drain_s: float = 0.0, intensity: float = 1.0) -> dict[str, Any]:
        m = super().reset(scenario, seed, horizon, drain_s, intensity)
        self.incident_ids: set[str] = set(m.get("incident_ids", []))
        self.post_ms: list[float] = []
        self.teleported: set[str] = set()
        self.teleport_log: list[tuple[float, str, str]] = []
        self.collision_log: list[tuple[float, str, str]] = []
        self.incident_stopped: dict[str, list[float]] = {}
        self._logged: set[tuple[str, str]] = set()
        return m

    def _observe_step(self) -> tuple[int, int, int]:
        t0 = time.perf_counter()
        out = super()._observe_step()
        self.post_ms.append((time.perf_counter() - t0) * 1000.0)  # measurement + frame capture of this step
        for vid in T.simulation.getStartingTeleportIDList():
            self.teleported.add(vid)
            try:
                where = T.vehicle.getRoadID(vid)
            except Exception:  # noqa: BLE001 - already gone
                where = ""
            self.teleport_log.append((self.t, vid, where))
        if len(self._collision_pairs) > len(self._logged):  # the base class counts each (collider, victim) pair once: log the same pairs
            for c in T.simulation.getCollisions():
                pr = (c.collider, c.victim)
                if pr not in self._logged:
                    self._logged.add(pr)
                    self.collision_log.append((self.t, c.collider, c.victim))
        for v in self.incident_ids:
            if v in self.live and T.vehicle.isStopped(v):
                self.incident_stopped.setdefault(v, []).append(self.t)
        return out


def _rss_mb() -> float:
    import psutil

    mi = psutil.Process().memory_info()
    return getattr(mi, "peak_wset", mi.rss) / 1e6  # Windows reports the peak working set; elsewhere the current RSS


def run_demo_episode(controller: str, scenario: str, seed: int, intensity: float | None = None, record: bool = False,
                     horizon: float | None = None, drain: float | None = None, series_detail: bool = True) -> dict[str, Any]:
    spec = SPECS[scenario]
    horizon = spec.horizon_s if horizon is None else horizon
    drain = spec.drain_s if drain is None else drain
    intensity = spec.intensity if intensity is None else intensity
    sim = DemoSim(f"demo{os.getpid()}", record=record)
    cpu0, wall0 = time.process_time(), time.perf_counter()
    try:
        man = sim.reset(scenario, seed, horizon, drain, intensity)
        ctl, info = make_controller(controller, sim, shadow_forecast=True)  # forecasts are shadow-only unless the controller reads them
        ctl.reset(sim)
        topo, grid = sim.topo, sim.grid
        caps = np.array([a.capacity for a in topo.approaches])
        internal = list(grid.internal_edges)
        internal_cap = {e: grid.edge_length[e] * grid.edge_lanes[e] / C.SLOT_M for e in internal}
        series: list[dict[str, Any]] = []
        forecasts: list[dict[str, Any]] = []
        decide_ms: list[float] = []
        while not sim.done:
            sim.observe()
            t0 = time.perf_counter()
            actions = ctl.decide(sim)
            decide_ms.append((time.perf_counter() - t0) * 1000.0)
            for jid, a in zip(C.CONTROLLED, actions):
                if a:
                    sim.request(jid)
            q = sim.state.X[:, 0]
            speeds = [r[tc.VAR_SPEED] for r in T.vehicle.getAllSubscriptionResults().values() if tc.VAR_SPEED in r and r[tc.VAR_SPEED] >= 0.0]
            spilled = [e for e in internal if T.edge.getLastStepHaltingNumber(e) / internal_cap[e] >= SPILL_OCC]
            row = {"t": sim.t, "queue": float(q.sum()), "inNet": int(T.vehicle.getIDCount()), "pending": int(sim.pending()),
                   "meanSpeed": float(np.mean(speeds)) if speeds else 0.0, "haltedShare": float(np.mean([s < 0.1 for s in speeds])) if speeds else 0.0,
                   "spilled": spilled, "arrived": len(sim.arrived), "teleports": sim.teleports, "collisions": sim.collisions,
                   "waitNow": float(sim.waiting_seconds())}
            if series_detail:
                row["q"] = [round(float(x), 1) for x in q]
            series.append(row)
            if sim.forecast is not None:
                forecasts.append({"t": sim.t, "p30": sim.forecast["p30"].round(1).tolist(), "p60": sim.forecast["p60"].round(1).tolist()})
            sim.advance(C.DECISION_S)
        m = sim.metrics()
        out: dict[str, Any] = {
            "controller": controller, "controllerInfo": info, "scenario": scenario, "seed": seed, "intensity": intensity,
            "demandSha": man["sha256"], "metrics": m, "series": series, "forecasts": forecasts,
            "approachCap": [round(float(c), 1) for c in caps], "approachKeys": topo.keys,
            "demand": _demand_accounting(sim, man), "incident": _incident_times(sim),
            "teleportLog": [(float(t), v, w) for t, v, w in sim.teleport_log], "collisionLog": [(float(t), a, b) for t, a, b in sim.collision_log],
            "mlLog": list(getattr(ctl, "log", [])), "ml": ctl.stats() if hasattr(ctl, "stats") else None,
            "core": _core_metrics(sim, man),
            "resources": {"wall_s": round(time.perf_counter() - wall0, 2), "cpu_s": round(time.process_time() - cpu0, 2),
                          "peak_rss_mb": round(_rss_mb(), 1)},
            "timing": _timing(sim, decide_ms),
            "perSecondMs": [round(a + b, 3) for a, b in zip(sim.step_ms, sim.post_ms)],
        }
        if record:
            out["frames"] = sim.frames
        return out
    finally:
        sim.close()


def _demand_accounting(sim: DemoSim, man: dict[str, Any]) -> dict[str, Any]:
    """Where did every scheduled vehicle end up? Nothing is dropped silently: each vehicle is completed, on the road, or never inserted."""
    inc = sim.incident_ids
    sched = [v for v in sim.sched if v not in inc]
    completed = sum(1 for v in sched if v in sim.arrived)
    inserted = sum(1 for v in sched if v in sim.depart_t)
    on_road = sum(1 for v in sched if v in sim.live)
    return {"scheduled": len(sched), "inserted": inserted, "completed": completed, "onRoadAtEnd": on_road,
            "neverInserted": len(sched) - inserted, "teleportedVehicles": len(sim.teleported - inc),
            "incidentVehicles": len(inc), "unaccounted": len(sched) - completed - on_road - (len(sched) - inserted),
            "completedFraction": round(completed / max(1, len(sched)), 4)}


def _core_metrics(sim: DemoSim, man: dict[str, Any]) -> dict[str, Any]:
    """Waiting/travel statistics over regular vehicles only (the one or two stalled incident vehicles are excluded)."""
    rows = [v for k, v in sim.arrived.items() if k not in sim.incident_ids]
    if not rows:
        return {}
    tr, wt = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    unfinished = [sim.live[v][0] for v in sim.live if v not in sim.incident_ids]
    return {"mean_wait_s": float(wt.mean()), "p95_wait_s": float(np.percentile(wt, 95)), "mean_travel_s": float(tr.mean()),
            "p95_travel_s": float(np.percentile(tr, 95)), "max_travel_s": float(tr.max()),
            "unfinished_mean_wait_s": float(np.mean(unfinished)) if unfinished else 0.0}


def _incident_times(sim: DemoSim) -> list[dict[str, Any]]:
    out = []
    for v, ts in sorted(sim.incident_stopped.items()):
        out.append({"vehicle": v, "stoppedFrom_s": min(ts), "stoppedTo_s": max(ts), "scheduled_s": sim.sched[v]})
    return out


def _stats(v: list[float]) -> dict[str, float]:
    if not v:
        return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0, "n": 0}
    a = np.asarray(v)
    return {"mean_ms": round(float(a.mean()), 3), "p50_ms": round(float(np.percentile(a, 50)), 3), "p95_ms": round(float(np.percentile(a, 95)), 3),
            "p99_ms": round(float(np.percentile(a, 99)), 3), "max_ms": round(float(a.max()), 3), "n": len(v)}


def _timing(sim: DemoSim, decide_ms: list[float]) -> dict[str, Any]:
    return {"sumo_step": _stats(sim.step_ms), "post_step": _stats(sim.post_ms), "observation": _stats(sim.obs_ms), "inference": _stats(sim.infer_ms), "decide": _stats(decide_ms)}


def _job(args: tuple) -> dict[str, Any]:
    return run_demo_episode(*args)


def run_many(jobs: list[tuple], workers: int = 4) -> list[dict[str, Any]]:
    """Run episodes in fresh worker processes (one episode per process, so RSS/CPU are per episode and libsumo state never leaks)."""
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(workers, mp_context=ctx, max_tasks_per_child=1) as pool:
        return list(pool.map(_job, jobs, chunksize=1))
