"""One SUMO (libsumo) run of the RL network, with safe signal control and independent measurement.

``SignalSim`` is shared by the Gymnasium environment, the baseline controllers and the evaluation harness, so
every strategy is executed and measured by the same code. Measurement is separate from the reward: the
evaluation metrics come from SUMO's own per-vehicle bookkeeping, not from whatever the agent optimises.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .. import sumo_env
from . import config as C
from .demand import SCENARIOS, write_routes
from .network import GridInfo, build_net, read_grid
from .safety import SafeSignal

import traci.constants as tc  # constants only; the simulation itself runs in-process through libsumo

try:  # in-process binding: one simulation per process, no socket
    import libsumo as T  # type: ignore
except ImportError:  # pragma: no cover
    import traci as T  # type: ignore

VEH_VARS = (tc.VAR_ACCUMULATED_WAITING_TIME, tc.VAR_TIMELOSS, tc.VAR_WAITING_TIME)

_STARTED = False


def work_dir() -> Path:
    return Path(os.environ.get("FLOWSTATE_RL_WORK", Path(tempfile.gettempdir()) / "flowstate-rl")) / f"p{os.getpid()}"


@dataclass
class ApproachView:
    queue: float  # halting vehicles on the approach
    count: float  # all vehicles on the approach
    wait_s: float  # mean current waiting time of the vehicles on the approach
    max_wait_s: float
    downstream_occ: float  # occupancy of the edge the approach feeds, 0..1
    emergency: bool
    capacity: float


class SignalSim:
    def __init__(self, label: str = "sim"):
        self.label = label
        self.dir = work_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.net_path = build_net(self.dir)
        self.grid: GridInfo = read_grid(self.net_path)
        self.signals: dict[str, SafeSignal] = {}
        self.t = 0.0
        self.horizon = 0.0
        self.max_t = 0.0
        self.scenario = ""
        self.seed = 0
        self.manifest: dict[str, Any] = {}

    # ------------------------------------------------------------------ lifecycle
    def reset(self, scenario: str, seed: int, horizon: float | None = None, drain_s: float = 0.0, intensity: float = 1.0) -> dict[str, Any]:
        global _STARTED
        if scenario not in SCENARIOS:
            raise KeyError(f"unknown scenario '{scenario}'")
        self.scenario, self.seed = scenario, seed
        self.horizon = float(horizon or SCENARIOS[scenario].horizon_s)
        self.max_t = self.horizon + drain_s
        routes = self.dir / f"routes-{self.label}.rou.xml"
        self.manifest = write_routes(routes, self.grid, self.net_path, scenario, seed, self.horizon, intensity)
        self.sched: dict[str, float] = self.manifest.pop("scheduled")
        self.emergency_ids: set[str] = set(self.manifest.pop("emergency_ids"))
        cmd = [sumo_env.binary("sumo"), "-n", str(self.net_path), "-r", str(routes), "--step-length", str(C.STEP_S),
               "--no-step-log", "true", "--no-warnings", "true", "--seed", str(seed), "--time-to-teleport", str(C.TELEPORT_S),
               "--collision.action", "warn", "--waiting-time-memory", "3600", "--duration-log.disable", "true",
               "--xml-validation", "never", "--max-depart-delay", "-1"]
        if _STARTED:
            T.load(cmd[1:])
        else:
            T.start(cmd)
            _STARTED = True
        self.signals = {jid: SafeSignal(j.greens, j.yellows) for jid, j in self.grid.junctions.items()}
        self.t = 0.0
        self.depart_t: dict[str, float] = {}
        self.arrived: dict[str, tuple[float, float, float]] = {}  # id -> (travel, wait, loss)
        self.live: dict[str, tuple[float, float]] = {}  # id -> (accumulated wait, time loss) last seen
        self.emerg_wait: dict[str, float] = {}
        self.arrivals_in_horizon = 0
        self.teleports = 0
        self.collisions = 0
        self._collision_pairs: set[tuple[str, str]] = set()
        self.q_sum = 0.0
        self.q_max = 0.0
        self.spill_edge_s = 0.0
        self.samples = 0
        self.series: list[tuple[float, float, float, int]] = []  # (t, queue, mean wait, in network)
        self._apply_all()
        return self.manifest

    def close(self) -> None:
        global _STARTED
        if _STARTED:
            try:
                T.close()
            except Exception:  # noqa: BLE001
                pass
            _STARTED = False

    # ------------------------------------------------------------------ control
    def _apply_all(self) -> None:
        for jid, s in self.signals.items():
            T.trafficlight.setRedYellowGreenState(jid, s.state)

    def request(self, jid: str) -> bool:
        return self.signals[jid].request_switch(self.t)

    def advance(self, seconds: float) -> dict[str, float]:
        """Run ``seconds`` of simulated time; returns the reward ingredients of the interval."""
        n = max(1, int(round(seconds / C.STEP_S)))
        arrivals = tele = coll = 0
        for _ in range(n):
            self._apply_all()  # what the heads show during this step is the state *before* the tick
            T.simulationStep()
            self.t += C.STEP_S
            for s in self.signals.values():
                s.tick(C.STEP_S, self.t)
            a, tl, co = self._observe_step()
            arrivals, tele, coll = arrivals + a, tele + tl, coll + co
        return {"arrivals": arrivals, "teleports": tele, "collisions": coll}

    @property
    def done(self) -> bool:
        if self.t >= self.max_t:
            return True
        return self.t >= self.horizon and T.simulation.getMinExpectedNumber() == 0

    # ------------------------------------------------------------------ measurement
    def _observe_step(self) -> tuple[int, int, int]:
        t = self.t
        for vid in T.simulation.getDepartedIDList():
            self.depart_t[vid] = t
            T.vehicle.subscribe(vid, VEH_VARS)
        subs = T.vehicle.getAllSubscriptionResults()
        ids = list(subs)
        for vid, r in subs.items():
            self.live[vid] = (r[tc.VAR_ACCUMULATED_WAITING_TIME], r[tc.VAR_TIMELOSS])
        arrivals = 0
        for vid in T.simulation.getArrivedIDList():
            sched = self.sched[vid]
            acc, loss = self.live.pop(vid, (0.0, 0.0))
            delay = self.depart_t.get(vid, t) - sched  # waiting to be inserted counts as waiting
            self.arrived[vid] = (t - sched, acc + max(0.0, delay), loss + max(0.0, delay))
            arrivals += 1
            if t <= self.horizon:
                self.arrivals_in_horizon += 1
        tele = T.simulation.getStartingTeleportNumber()
        coll = 0
        for c in T.simulation.getCollisions():  # a contact that lasts is reported every step: count each pair once
            pair = (c.collider, c.victim)
            if pair not in self._collision_pairs:
                self._collision_pairs.add(pair)
                coll += 1
        self.teleports += tele
        self.collisions += coll
        q = 0.0
        for e in self._approach_edges:
            q += T.edge.getLastStepHaltingNumber(e)
        for e in self.grid.internal_edges:
            if T.edge.getLastStepHaltingNumber(e) * C.SLOT_M / (self.grid.edge_length[e] * self.grid.edge_lanes[e]) >= 0.5:
                self.spill_edge_s += 1.0
        self.q_sum += q
        self.q_max = max(self.q_max, q)
        self.samples += 1
        if int(t) % 10 == 0:
            waits = [r[tc.VAR_WAITING_TIME] for r in subs.values()]
            self.series.append((t, q, float(np.mean(waits)) if waits else 0.0, len(ids)))
        for v in self._emergency_in_network(ids):
            self.emerg_wait[v] = self.emerg_wait.get(v, 0.0) + (1.0 if subs[v][tc.VAR_WAITING_TIME] > 0 else 0.0)
        return arrivals, tele, coll

    @property
    def _approach_edges(self) -> list[str]:
        if not hasattr(self, "_ae"):
            self._ae = [e for j in self.grid.junctions.values() for e in j.approach_edges.values()]
        return self._ae

    def _emergency_in_network(self, ids) -> list[str]:
        return [v for v in ids if v in self.emergency_ids] if self.emergency_ids else []

    # ------------------------------------------------------------------ views for observations and heuristics
    def approach(self, jid: str, direction: str) -> ApproachView:
        j = self.grid.junctions[jid]
        e = j.approach_edges[direction]
        out = j.out_edges[{"N": "S", "S": "N", "E": "W", "W": "E"}[direction]]
        n = T.edge.getLastStepVehicleNumber(e)
        wait_sum = T.edge.getWaitingTime(e) if n else 0.0
        emerg = bool(self.emergency_ids) and any(v in self.emergency_ids for v in T.edge.getLastStepVehicleIDs(e))
        n_out = T.edge.getLastStepVehicleNumber(out)
        return ApproachView(float(T.edge.getLastStepHaltingNumber(e)), float(n), wait_sum / n if n else 0.0,
                            wait_sum, min(1.0, n_out / self.grid.capacity(out)), emerg, self.grid.capacity(e))

    def pending(self) -> int:
        return len(T.simulation.getPendingVehicles())

    def in_network(self) -> int:
        return T.vehicle.getIDCount()

    def waiting_seconds(self) -> float:
        """Sum over the vehicles on the approaches of how long each has currently been standing still."""
        return float(sum(T.edge.getWaitingTime(e) for e in self._approach_edges))

    def spill_fraction(self) -> float:
        g = self.grid
        hit = sum(1 for e in g.internal_edges
                  if T.edge.getLastStepHaltingNumber(e) * C.SLOT_M / (g.edge_length[e] * g.edge_lanes[e]) >= 0.5)
        return hit / len(g.internal_edges)

    def emergency_waiting(self) -> float:
        if not self.emergency_ids:
            return 0.0
        return sum(1 for v in T.vehicle.getIDList() if v in self.emergency_ids and T.vehicle.getWaitingTime(v) > 0)

    def total_approach_capacity(self) -> float:
        return sum(self.grid.capacity(e) for e in self._approach_edges)

    # ------------------------------------------------------------------ results
    def metrics(self) -> dict[str, Any]:
        done = list(self.arrived.values())
        total = self.manifest["vehicles"]
        in_net = list(self.live)
        # vehicles still on the road (or never inserted) at the end are censored: report them rather than hide them
        unfinished = total - len(done)
        travel = np.array([d[0] for d in done]) if done else np.array([0.0])
        wait = np.array([d[1] for d in done]) if done else np.array([0.0])
        loss = np.array([d[2] for d in done]) if done else np.array([0.0])
        cens_wait = [self.live[v][0] + max(0.0, self.depart_t.get(v, self.t) - self.sched[v]) for v in in_net]
        n_edges_s = max(1.0, len(self.grid.internal_edges) * self.samples)
        n_approach = max(1, len(self._approach_edges))
        return {
            "scenario": self.scenario, "seed": self.seed, "horizon_s": self.horizon, "sim_end_s": self.t,
            "vehicles_scheduled": total, "vehicles_completed": len(done), "vehicles_unfinished": unfinished,
            "completed_fraction": len(done) / max(1, total),
            "mean_wait_s": float(wait.mean()), "mean_travel_s": float(travel.mean()), "p95_travel_s": float(np.percentile(travel, 95)),
            "mean_time_loss_s": float(loss.mean()),
            "throughput_veh": self.arrivals_in_horizon, "throughput_vph": self.arrivals_in_horizon / self.horizon * 3600.0,
            "queue_mean_veh": self.q_sum / max(1, self.samples) / n_approach, "queue_max_veh": self.q_max,
            "queue_total_mean_veh": self.q_sum / max(1, self.samples),
            "spillback_pct": 100.0 * self.spill_edge_s / n_edges_s,
            "teleports": self.teleports, "collisions": self.collisions,
            "unfinished_mean_wait_s": float(np.mean(cens_wait)) if cens_wait else 0.0,
            "emergency_vehicles": int(self.manifest.get("emergency", 0)),
            "emergency_mean_stopped_s": (float(np.mean(list(self.emerg_wait.values()))) if self.emerg_wait else 0.0),
            "signal_switches": sum(s.switches for s in self.signals.values()),
            "requests_refused": sum(s.refused for s in self.signals.values()),
            "forced_changes": sum(s.forced for s in self.signals.values()),
            "demand_sha": self.manifest["sha256"],
        }
