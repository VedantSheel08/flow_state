"""``LabSim``: the RL ``SignalSim`` plus what the lab needs on top: frames for replay, CO2, and a TrafficState observation.

Measurement is inherited unchanged (waiting, travel, throughput, queues, teleports, collisions come from the same code that
produced the RL report). The lab adds:

* per-vehicle position, angle, speed and CO2 (SUMO HBEFA3 per step) through one extended subscription;
* :class:`TrafficState`: a detector-like observation of every approach, sampled every decision interval with a short history,
  used both as forecasting features and by the forecast-aware controller;
* optional frame capture (1 per simulated second) for synchronized browser replay.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from ..rl import config as C
from ..rl.sim import VEH_VARS, SignalSim, T, tc

LAB_VARS = VEH_VARS + (tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED, tc.VAR_CO2EMISSION)
# HBEFA3 classes per vehicle type: a fleet assumption (as in the experiments emissions module), not measured fleet data
EMISSION_CLASS = {"car": "HBEFA3/PC_G_EU4", "truck": "HBEFA3/HDV_D_EU4", "emergency": "HBEFA3/LDV_D_EU4"}
TYPE_CODE = {"car": 0, "truck": 1, "emergency": 2}
HISTORY = 7  # decision samples kept per approach (35 s at the 5 s grid)
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}

FEATURES = ("queue", "count", "moving", "mean_wait", "down_occ", "is_green", "in_transition", "elapsed", "to_max_green",
            "up_count", "up_halting", "up_released", "q_lag10", "q_lag20", "q_lag30", "count_lag30", "capacity", "is_entry",
            "stage_demand_other")


@dataclass
class Approach:
    jid: str
    direction: str  # side of the junction the traffic comes from
    edge: str
    out_edge: str  # straight-through exit
    upstream: list[str] = field(default_factory=list)  # edges feeding this approach (empty for a fringe entry)
    up_tls: list[tuple[str, str]] = field(default_factory=list)  # (junction, direction) of each upstream edge if signalised
    capacity: float = 1.0
    is_entry: bool = False


class Topology:
    """Static description of the 16 approaches and how they connect, read once from the network."""

    def __init__(self, sim: SignalSim):
        import sumolib

        net = sumolib.net.readNet(str(sim.net_path), withInternal=False)
        g = sim.grid
        self.approaches: list[Approach] = []
        by_edge: dict[str, tuple[str, str]] = {}
        for jid in C.CONTROLLED:
            for d, e in sorted(g.junctions[jid].approach_edges.items()):
                by_edge[e] = (jid, d)
        for jid in C.CONTROLLED:
            j = g.junctions[jid]
            for d in C.DIRECTIONS:
                if d not in j.approach_edges:
                    continue
                e = j.approach_edges[d]
                edge = net.getEdge(e)
                frm = edge.getFromNode()
                ups = [u.getID() for u in frm.getIncoming() if u.getFromNode().getID() != jid] if frm.getID() in C.CONTROLLED else []
                a = Approach(jid, d, e, j.out_edges[OPPOSITE[d]], ups, [by_edge[u] for u in ups if u in by_edge], g.capacity(e),
                             frm.getID() not in C.CONTROLLED)
                self.approaches.append(a)
        self.index = {(a.jid, a.direction): i for i, a in enumerate(self.approaches)}
        self.edges = sorted({a.edge for a in self.approaches} | {a.out_edge for a in self.approaches} | {u for a in self.approaches for u in a.upstream})
        # for every approach: the approach at the neighbour junction that its straight exit feeds (None = leaves the grid)
        self.feeds: list[int | None] = [self.index.get(by_edge.get(a.out_edge, ("", ""))) for a in self.approaches]

    @property
    def keys(self) -> list[str]:
        return [f"{a.jid}:{a.direction}" for a in self.approaches]


class TrafficState:
    """Detector-like observation of every approach, plus a ring buffer of recent samples. One libsumo pass per sample."""

    def __init__(self, topo: Topology):
        self.topo = topo
        n = len(topo.approaches)
        self.q_hist = np.zeros((HISTORY, n), dtype=np.float32)
        self.c_hist = np.zeros((HISTORY, n), dtype=np.float32)
        self.samples = 0
        self.t = -1.0
        self.X = np.zeros((n, len(FEATURES)), dtype=np.float32)
        self.cost_ms = 0.0

    def sample(self, sim: SignalSim) -> np.ndarray:
        t0 = time.perf_counter()
        topo = self.topo
        halt = {e: float(T.edge.getLastStepHaltingNumber(e)) for e in topo.edges}
        cnt = {e: float(T.edge.getLastStepVehicleNumber(e)) for e in topo.edges}
        wait = {a.edge: float(T.edge.getWaitingTime(a.edge)) for a in topo.approaches}
        green_dirs: dict[str, set[str]] = {}
        for jid, s in sim.signals.items():
            green_dirs[jid] = set(sim.grid.junctions[jid].served[s.stage]) if s.mode == "green" else set()
        self.q_hist = np.roll(self.q_hist, 1, axis=0)
        self.c_hist = np.roll(self.c_hist, 1, axis=0)
        X = self.X
        for i, a in enumerate(topo.approaches):
            q, c = halt[a.edge], cnt[a.edge]
            self.q_hist[0, i], self.c_hist[0, i] = q, c
            s = sim.signals[a.jid]
            green = a.direction in green_dirs[a.jid]
            other = sum(halt[topo.approaches[k].edge] for k in range(len(topo.approaches))
                        if topo.approaches[k].jid == a.jid and topo.approaches[k].direction not in green_dirs[a.jid] and k != i)
            released = sum(cnt[u] for u, (uj, ud) in zip(a.upstream, a.up_tls) if ud in green_dirs[uj])
            X[i] = (q, c, c - q, wait[a.edge] / c if c else 0.0, min(1.0, cnt[a.out_edge] / sim.grid.capacity(a.out_edge)),
                    1.0 if green else 0.0, 0.0 if s.mode == "green" else 1.0, s.elapsed if s.mode == "green" else 0.0,
                    max(0.0, C.MAX_GREEN_S - s.elapsed) if green else 0.0,
                    sum(cnt[u] for u in a.upstream), sum(halt[u] for u in a.upstream), released,
                    0, 0, 0, 0, a.capacity, 1.0 if a.is_entry else 0.0, other)
        self.samples += 1
        k = min(self.samples - 1, HISTORY - 1)
        X[:, 12] = self.q_hist[min(2, k)]
        X[:, 13] = self.q_hist[min(4, k)]
        X[:, 14] = self.q_hist[min(6, k)]
        X[:, 15] = self.c_hist[min(6, k)]
        self.t = sim.t
        self.cost_ms = (time.perf_counter() - t0) * 1000.0
        return X


class LabSim(SignalSim):
    """``SignalSim`` with CO2, frames and the TrafficState observation. Measurement of the inherited metrics is unchanged."""

    def __init__(self, label: str = "lab", record: bool = False):
        super().__init__(label)
        self.topo = Topology(self)
        self.record = record
        self.forecaster: Any = None  # set by the caller; predictions are shadow-only unless a controller reads them
        # Like the main simulator, the lab gives emergency vehicles NO special treatment: they stop at red lights and queue behind other cars.
        # A controller only reacts to them if it is the "emergency green" variant (``*_ev``), which sets this flag before the run.
        self.controllers_see_emergency = False

    def reset(self, scenario: str, seed: int, horizon: float | None = None, drain_s: float = 0.0, intensity: float = 1.0) -> dict[str, Any]:
        m = super().reset(scenario, seed, horizon, drain_s, intensity)
        for vt, cls in EMISSION_CLASS.items():
            T.vehicletype.setEmissionClass(vt, cls)
        self.state = TrafficState(self.topo)
        self.co2_g = 0.0
        self.vtype: dict[str, int] = {}
        self.frames: list[dict[str, Any]] = []
        self.forecast: dict[str, Any] | None = None
        self.ev_status: dict[str, tuple[int, float]] = {}  # emergency vehicle -> (status code, metres to the next signal), this step
        self.ev_stop: dict[str, list[float]] = {}  # emergency vehicle -> [seconds stopped at a red light, seconds stuck in a queue]
        self.step_ms: list[float] = []
        self.infer_ms: list[float] = []
        self.obs_ms: list[float] = []
        self._last_frame: dict[str, Any] | None = None
        return m

    def advance(self, seconds: float) -> dict[str, float]:
        n = max(1, int(round(seconds / C.STEP_S)))
        arrivals = tele = coll = 0
        for _ in range(n):
            self._apply_all()
            t0 = time.perf_counter()
            T.simulationStep()
            self.step_ms.append((time.perf_counter() - t0) * 1000.0)
            self.t += C.STEP_S
            for s in self.signals.values():
                s.tick(C.STEP_S, self.t)
            a, tl, co = self._observe_step()
            arrivals, tele, coll = arrivals + a, tele + tl, coll + co
        return {"arrivals": arrivals, "teleports": tele, "collisions": coll}

    def _observe_step(self) -> tuple[int, int, int]:
        out = super()._observe_step()
        for vid in T.simulation.getDepartedIDList():  # same step: extend the subscription the base class just made
            T.vehicle.subscribe(vid, LAB_VARS)
            self.vtype[vid] = TYPE_CODE.get(T.vehicle.getTypeID(vid), 0)
        subs = T.vehicle.getAllSubscriptionResults()
        co2 = 0.0
        for r in subs.values():
            v = r.get(tc.VAR_CO2EMISSION, 0.0)
            if v >= 0.0:  # libsumo reports INVALID_DOUBLE_VALUE (-2^30) for a vehicle that is being teleported
                co2 += v
        self.co2_g += co2 * C.STEP_S / 1000.0  # mg/s over one step -> g
        self._track_emergency(subs)
        if self.record:
            self.frames.append(self._frame(subs))
        return out

    EV_MOVING, EV_RED, EV_QUEUE, EV_GREEN = 0, 1, 2, 3  # emergency vehicle status codes (also written into replay frames)

    def approach(self, jid: str, direction: str):  # type: ignore[override]
        a = super().approach(jid, direction)
        return a if self.controllers_see_emergency else replace(a, emergency=False)

    def _track_emergency(self, subs: dict[str, dict[int, Any]]) -> None:
        """Why is each emergency vehicle not moving? Stopped within 12 m of a red/amber stop line = a red light; stopped anywhere else = a queue."""
        self.ev_status = {}
        for vid in self.emergency_ids & set(subs):
            r = subs[vid]
            speed = r.get(tc.VAR_SPEED, 0.0)
            if speed < -1e5:  # libsumo's invalid marker (vehicle being teleported)
                continue
            nxt = T.vehicle.getNextTLS(vid)
            tls = nxt[0] if nxt else None  # (tls id, link index, distance, state)
            dist = float(tls[2]) if tls else -1.0
            if speed < 0.1:
                red = tls is not None and dist <= 12.0 and tls[3] in "rRyY"
                code = self.EV_RED if red else self.EV_QUEUE
                self.ev_stop.setdefault(vid, [0.0, 0.0])[0 if red else 1] += C.STEP_S
            else:
                code = self.EV_GREEN if tls is not None and dist <= 80.0 and tls[3] in "Gg" else self.EV_MOVING
            self.ev_status[vid] = (code, dist)

    def _frame(self, subs: dict[str, dict[int, Any]]) -> dict[str, Any]:
        veh = []
        waits = []
        for vid, r in subs.items():
            if tc.VAR_POSITION not in r:
                continue
            x, y = r[tc.VAR_POSITION]
            if x < -1e6 or y < -1e6:  # teleporting: not on the road, nothing to draw
                continue
            w = r[tc.VAR_WAITING_TIME]
            waits.append(w)
            veh.append([int(vid[1:]), int(round(x * 10)), int(round(y * 10)), int(round(r[tc.VAR_ANGLE])), self.vtype.get(vid, 0),
                        int(round(r[tc.VAR_SPEED] * 10)), 1 if w > 0 else 0])
        return {"t": int(self.t), "v": veh, "s": [self.signals[j].state for j in C.CONTROLLED], "k": self.kpis(waits),
                "e": [[int(v[1:]), code, int(round(d * 10))] for v, (code, d) in self.ev_status.items()]}

    def kpis(self, waits: list[float] | None = None) -> list[float]:
        """Instantaneous measured KPIs: [in network, halting on approaches, mean current wait, arrived, CO2 kg, teleports, collisions, pending]."""
        if waits is None:
            waits = [r[tc.VAR_WAITING_TIME] for r in T.vehicle.getAllSubscriptionResults().values()]
        q = sum(T.edge.getLastStepHaltingNumber(e) for e in self._approach_edges)
        return [len(waits), q, round(float(np.mean(waits)), 1) if waits else 0.0, len(self.arrived), round(self.co2_g / 1000.0, 3),
                self.teleports, self.collisions, self.pending()]

    def observe(self) -> np.ndarray:
        """Sample TrafficState and, when a forecaster is attached, predict every approach's queue 30 s and 60 s ahead."""
        X = self.state.sample(self)
        self.obs_ms.append(self.state.cost_ms)
        if self.forecaster is not None:
            t0 = time.perf_counter()
            full = self.forecaster.predict_full(X)  # point forecasts (+ calibrated bounds when a calibration is bound to the artifact)
            self.infer_ms.append((time.perf_counter() - t0) * 1000.0)
            self.forecast = {"t": self.t, "q": X[:, 0].copy(), "cap": X[:, 16].copy(), **full}
        return X

    def metrics(self) -> dict[str, Any]:
        m = super().metrics()
        m["co2_kg"] = round(self.co2_g / 1000.0, 3)
        m["co2_g_per_veh"] = round(self.co2_g / max(1, m["vehicles_completed"]), 1)
        civ = [d for v, d in self.arrived.items() if v not in self.emergency_ids]  # (travel, wait, loss) of every non-emergency vehicle
        m["mean_wait_civilian_s"] = float(np.mean([d[1] for d in civ])) if civ else 0.0
        m["mean_travel_civilian_s"] = float(np.mean([d[0] for d in civ])) if civ else 0.0
        # every scheduled emergency vehicle counts; one still on the road at the end is measured up to that moment (a lower bound, flagged)
        units = []
        for v in sorted((v for v in self.emergency_ids if self.sched[v] <= self.t), key=lambda x: self.sched[x]):  # only units already due to depart
            done = self.arrived.get(v)
            red, queue = self.ev_stop.get(v, [0.0, 0.0])
            units.append({"id": v, "depart_s": self.sched[v], "travel_s": round(done[0] if done else self.t - self.sched[v], 1), "finished": done is not None,
                          "stopped_red_s": red, "stopped_queue_s": queue})
        m["ev_total"] = len(units)
        m["ev_unfinished"] = sum(1 for u in units if not u["finished"])
        m["ev_travel_mean_s"] = float(np.mean([u["travel_s"] for u in units])) if units else 0.0
        m["ev_stopped_red_s"] = float(np.mean([u["stopped_red_s"] for u in units])) if units else 0.0
        m["ev_stopped_queue_s"] = float(np.mean([u["stopped_queue_s"] for u in units])) if units else 0.0
        m["ev_units"] = units
        m["controllers_see_emergency"] = self.controllers_see_emergency
        return m

    def timing(self) -> dict[str, float]:
        def stats(v: list[float]) -> dict[str, float]:
            if not v:
                return {"mean_ms": 0.0, "p95_ms": 0.0, "n": 0}
            a = np.asarray(v)
            return {"mean_ms": round(float(a.mean()), 3), "p95_ms": round(float(np.percentile(a, 95)), 3), "n": len(v)}

        return {"sumo_step": stats(self.step_ms), "observation": stats(self.obs_ms), "inference": stats(self.infer_ms)}


def geometry(sim: SignalSim) -> dict[str, Any]:
    """Lane polygons, junction shapes and signal-head positions for the browser (SUMO coordinates, metres)."""
    import sumolib

    net = sumolib.net.readNet(str(sim.net_path), withInternal=True, withPrograms=True)
    lanes = []
    for e in net.getEdges(withInternal=True):
        for ln in e.getLanes():
            lanes.append({"id": ln.getID(), "s": [[round(x, 1), round(y, 1)] for x, y in ln.getShape()], "w": round(ln.getWidth(), 2),
                          "i": 1 if e.getFunction() == "internal" else 0})
    junctions = [{"id": n.getID(), "s": [[round(x, 1), round(y, 1)] for x, y in n.getShape()], "tls": n.getID() in C.CONTROLLED,
                  "c": [round(n.getCoord()[0], 1), round(n.getCoord()[1], 1)]} for n in net.getNodes() if n.getShape()]
    heads = []
    for tls in net.getTrafficLights():
        jid = tls.getID()
        if jid not in C.CONTROLLED:
            continue
        per_lane: dict[str, list[int]] = {}
        for idx, lst in sorted(tls.getLinks().items()):
            per_lane.setdefault(lst[0][0].getID(), []).append(idx)
        for lane_id, idxs in per_lane.items():
            x, y = net.getLane(lane_id).getShape()[-1]
            heads.append({"j": jid, "l": lane_id, "x": round(x, 1), "y": round(y, 1), "k": idxs})
    xs = [p[0] for ln in lanes for p in ln["s"]]
    ys = [p[1] for ln in lanes for p in ln["s"]]
    topo = Topology(sim) if not isinstance(sim, LabSim) else sim.topo
    approaches = []
    for a in topo.approaches:
        shp = net.getEdge(a.edge).getLanes()[0].getShape()
        approaches.append({"key": f"{a.jid}:{a.direction}", "edge": a.edge, "cap": round(a.capacity, 1), "entry": a.is_entry,
                           "a": [round(v, 1) for v in shp[0]], "b": [round(v, 1) for v in shp[-1]]})
    return {"lanes": lanes, "junctions": junctions, "heads": heads, "approaches": approaches,
            "bounds": [min(xs), min(ys), max(xs), max(ys)], "controlled": list(C.CONTROLLED)}


def write_json(path: Path, obj: Any) -> None:
    import gzip
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, separators=(",", ":")).encode()
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(data, 6))
    else:
        path.write_bytes(data)
