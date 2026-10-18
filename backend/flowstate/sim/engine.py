"""Simulation engine: one SUMO process driven through TraCI.

``Engine.step()`` advances SUMO by one step and updates the authoritative world
state (vehicles, pedestrians, signals, incidents, metrics). Nothing in a frame
is invented: positions, headings, brake/indicator lights, signal states and
measurements all come from SUMO.

Time: SUMO runs a short *warm-up* first (default 90 s) so the network is
populated when the scenario clock starts. ``engine.t`` is scenario time
(SUMO time minus warm-up); it is negative during warm-up.
"""

from __future__ import annotations

import itertools
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import os

import traci
import traci.constants as tc

try:  # in-process binding: no socket, no Python-side message parsing
    import libsumo  # type: ignore
except ImportError:  # pragma: no cover
    libsumo = None

from .. import sumo_env
from ..network.info import NetInfo
from ..performance.network_cache import load_network
from ..scenarios.base import Scenario
from .demand import DemandGenerator
from .events import Event, EventLog
from .metrics import Metrics
from .safety import SafetyObservations
from .signals import SignalManager, write_actuated_programs
from .vtypes import EMERGENCY_TYPES, MODE_OF, QUIET, TYPE_CODES, base_type, write_vtypes

log = logging.getLogger(__name__)

_LABELS = itertools.count(1)

VEH_VARS = (tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED, tc.VAR_SIGNALS, tc.VAR_TIMELOSS, tc.VAR_ACCUMULATED_WAITING_TIME, tc.VAR_DISTANCE)
PER_VARS = (tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED)
_LIBSUMO_IN_USE = False


def backend_name() -> str:
    return os.environ.get("FLOWSTATE_BACKEND", "libsumo")


@dataclass
class RunConfig:
    scenario_id: str
    params: dict[str, Any]
    seed: int
    signal_mode: str = "fixed"
    step_length: float = 0.25
    warmup: float = 90.0
    label: str = "live"


@dataclass
class Tracked:
    id: str
    kind: str  # ambulance | police | suspect | bus | responder
    label: str
    dispatched: float
    data: dict[str, Any] = field(default_factory=dict)
    arrived: float | None = None
    status: str = "en route"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "dispatched": round(self.dispatched, 1),
                "arrived": None if self.arrived is None else round(self.arrived, 1), "status": self.status,
                **{k: v for k, v in self.data.items() if isinstance(v, (int, float, str, bool, type(None)))}}


@dataclass
class Incident:
    id: str
    kind: str  # accident | construction
    edge: str
    lanes: list[int]
    pos: float
    length: float
    t_start: float
    t_end: float | None
    x: float
    y: float
    angle: float
    severity: str = "moderate"
    blockers: list[str] = field(default_factory=list)
    disallowed: dict[str, list[str]] = field(default_factory=dict)
    orig_speed: float | None = None
    cleared: bool = False
    closed: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "edge": self.edge, "lanes": self.lanes, "pos": round(self.pos, 1),
                "length": round(self.length, 1), "tStart": round(self.t_start, 1), "tEnd": None if self.t_end is None else round(self.t_end, 1),
                "x": round(self.x, 2), "y": round(self.y, 2), "angle": round(self.angle, 1), "severity": self.severity,
                "cleared": self.cleared, "closed": self.closed, "description": self.description}


class Engine:
    def __init__(self, scenario: Scenario, config: RunConfig, networks_dir: Path, work_dir: Path, on_event: Callable[[Event], None] | None = None):
        self.scenario = scenario
        self.config = config
        self.network_id = scenario.network_id()
        self.net_path = networks_dir / f"{self.network_id}.net.xml"
        if not self.net_path.exists():
            raise FileNotFoundError(f"network not built: {self.net_path} (run `python -m flowstate.cli build-network`)")
        self.net: NetInfo = load_network(str(self.net_path), self.network_id)
        self.work_dir = work_dir
        self.dt = config.step_length
        self.warmup = config.warmup
        self.T: Any = None
        self.t = -self.warmup
        self.started = False
        self.finished = False
        self.events = EventLog(listener=on_event)
        self.metrics = Metrics()
        self.vehicles: dict[str, tuple] = {}  # id -> (x, y, angle, speed, signals)
        self.vtype: dict[str, str] = {}
        self.persons: dict[str, tuple] = {}
        self.tls_state: dict[str, str] = {}
        self.tls_phase: dict[str, int] = {}
        self.edge_stats: dict[str, tuple] = {}
        self.tracked: dict[str, Tracked] = {}
        self.incidents: dict[str, Incident] = {}
        self.tags: dict[str, str] = {}
        self.teleports = 0
        self.collisions = 0
        self.safety = SafetyObservations()
        self.wall_ms = 0.0
        self._inc_ids = itertools.count(1)
        self._route_ids = itertools.count(1)
        self._reroute_rng = random.Random(config.seed * 31 + 7)
        self._approach_lanes: dict[str, list[str]] = {}
        self._queue_alert: dict[str, float] = {}
        self._queue_alerted: set[str] = set()
        self._last_sample = -1e9
        self._last_edge_poll = -1e9
        self.kpi: dict[str, Any] = {}
        scenario.instantiate(config.seed, self.net)
        self.signals = SignalManager(self, config.signal_mode, scenario.tsp)
        self._quiet: set[str] = set()

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        vtypes = self.work_dir / "vtypes.add.xml"
        profiles = write_vtypes(vtypes, self.config.seed, self.scenario.conditions())
        additional = [str(vtypes)]
        stops = self.net_path.with_name(f"{self.network_id}.stops.add.xml")
        if stops.exists():
            additional.append(str(stops))
        if self.config.signal_mode == "actuated":
            act = self.work_dir / "actuated.add.xml"
            write_actuated_programs(self.net.tls_programs, act)
            additional.append(str(act))
        cmd = [
            sumo_env.binary("sumo"), "-n", str(self.net_path), "-a", ",".join(additional),
            "--step-length", f"{self.dt}", "--seed", str(self.config.seed),
            "--lateral-resolution", "0.8", "--pedestrian.model", "striping",
            "--time-to-teleport", "240", "--time-to-teleport.highways", "0",
            "--device.rerouting.probability", "0.35", "--device.rerouting.period", "45",
            "--device.rerouting.adaptation-interval", "10", "--device.rerouting.adaptation-weight", "0.0",
            "--collision.action", "warn", "--collision.check-junctions", "true",
            "--collision-output", str(self.work_dir / "collisions.xml"),
            "--no-step-log", "true", "--no-warnings", "false", "--xml-validation", "never",
            "--error-log", str(self.work_dir / "sumo-errors.log"), "--default.speeddev", "0",
            "--max-depart-delay", "180", "--eager-insert", "false",
        ]
        global _LIBSUMO_IN_USE
        label = f"{self.config.label}-{next(_LABELS)}"
        if libsumo is not None and backend_name() == "libsumo" and not _LIBSUMO_IN_USE:
            libsumo.start(cmd)
            self.T = libsumo
            _LIBSUMO_IN_USE = True
            self._libsumo = True
        else:
            traci.start(cmd, label=label)
            self.T = traci.getConnection(label)
            self._libsumo = False
        self._label = label
        self.started = True
        self.demand = DemandGenerator(self, random.Random(self.config.seed * 7919 + 1), profiles)
        self.profiles = profiles
        for tid in self.T.trafficlight.getIDList():
            self.T.trafficlight.subscribe(tid, (tc.TL_RED_YELLOW_GREEN_STATE, tc.TL_CURRENT_PHASE))
        controlled = set(self.net.city.get("junctions", {}))
        for e in self.net.edges.values():
            if e.to in controlled and e.kind in ("street", "entry"):
                self._approach_lanes.setdefault(e.to, []).extend(self.net.street_segment(e.id))
        self.signals.start()
        self.scenario.on_start(self)
        log.info("engine %s started: %s seed=%s signals=%s", label, self.scenario.spec.id, self.config.seed, self.config.signal_mode)

    def close(self) -> None:
        global _LIBSUMO_IN_USE
        if self.T is not None:
            try:
                self.T.close()
            except Exception:  # noqa: BLE001
                pass
            if getattr(self, "_libsumo", False):
                _LIBSUMO_IN_USE = False
            self.T = None

    # ------------------------------------------------------------- helpers
    def emit(self, category: str, message: str, severity: str = "info", **kw: Any) -> Event | None:
        if self.t < 0 and category not in ("system",):
            return None
        return self.events.emit(self.t, category, message, severity, **kw)

    def junction_name(self, node: str) -> str:
        return self.net.junction_name(node)

    def label(self, vid: str) -> str:
        tr = self.tracked.get(vid)
        return tr.label if tr else vid

    def emergency_active(self) -> list[str]:
        return [v for v, tr in self.tracked.items() if tr.kind in ("ambulance", "police", "responder") and v in self.vehicles and tr.data.get("priority", True)]

    def _lights_off(self) -> None:
        """A unit that turns its lights off switches to its ``@quiet`` type and keeps to the limit again.

        Its bluelight device stays attached across the type switch, so its reaction distance drops to zero and drivers
        stop making way for it.
        """
        for vid, tr in self.tracked.items():
            if tr.kind in ("ambulance", "police", "responder") and vid in self.vehicles and vid not in self._quiet and not tr.data.get("priority", True):
                self._quiet.add(vid)
                quiet = f"{self.vtype.get(vid) or self.T.vehicle.getTypeID(vid)}{QUIET}"
                try:
                    self.T.vehicle.setType(vid, quiet)
                    self.vtype[vid] = quiet
                    self.T.vehicle.setParameter(vid, "device.bluelight.reactiondist", "0")
                except Exception as e:  # noqa: BLE001
                    log.debug("could not switch %s to %s: %s", vid, quiet, e)

    def buses_active(self) -> list[str]:
        return [v for v, tr in self.tracked.items() if tr.kind == "bus" and v in self.vehicles]

    @property
    def person_count(self) -> int:
        return len(self.persons)

    def add_vehicle(self, vid: str, route_id: str, vtype: str, depart_pos: str = "base", depart_speed: str = "desired",
                    arrival_pos: str = "max", depart_lane: str = "best", tag: str | None = None) -> None:
        self.T.vehicle.add(vid, route_id, typeID=vtype, depart="now", departLane=depart_lane, departPos=depart_pos,
                           departSpeed=depart_speed, arrivalPos=arrival_pos)
        self.vtype[vid] = vtype
        if tag:
            self.tags[vid] = tag

    def spawn(self, vid: str, origin: str, dest: str, base: str, *, kind: str | None = None, label: str = "", tag: str | None = None,
              depart_pos: str = "base", arrival_pos: str = "max", depart_speed: str = "desired", data: dict[str, Any] | None = None) -> bool:
        """Insert a specific vehicle on a SUMO route from ``origin`` to ``dest``."""
        vtype = self._type_id(base)
        r = self.demand.route(origin, dest, vtype)
        if r is None:
            return False
        self.add_vehicle(vid, r[0], vtype, depart_pos=depart_pos, depart_speed=depart_speed, arrival_pos=arrival_pos, tag=tag or kind)
        if kind:
            self.tracked[vid] = Tracked(vid, kind, label or vid, self.t, data={"routeLength": round(r[1], 1), **(data or {})})
        return True

    def route_edges(self, origin: str, dest: str, vtype: str) -> tuple[list[str], float] | None:
        try:
            r = self.T.simulation.findRoute(origin, dest, vType=self._type_id(vtype))
        except Exception:  # noqa: BLE001
            return None
        if r is None or not r.edges:
            return None
        return list(r.edges), r.length

    def add_route(self, edges: list[str]) -> str:
        rid = f"rx{next(self._route_ids)}"
        self.T.route.add(rid, edges)
        return rid

    def _type_id(self, base_or_profile: str) -> str:
        if base_or_profile in self.profiles:
            return self.profiles[base_or_profile][0]
        return base_or_profile

    def track(self, vid: str, kind: str, label: str, data: dict[str, Any] | None = None) -> Tracked:
        tr = Tracked(vid, kind, label, self.t, data=dict(data or {}))
        self.tracked[vid] = tr
        return tr

    def spawn_via(self, vid: str, points: list[str], vtype: str, *, kind: str | None = None, label: str = "", tag: str | None = None,
                  stops: list[tuple[str, float, int, float]] | None = None, data: dict[str, Any] | None = None) -> bool:
        """Insert a vehicle on a route through ``points`` (edges), with optional stops (edge, pos, lane, duration)."""
        vt = self._type_id(vtype)
        edges: list[str] = []
        length = 0.0
        for a, b in zip(points, points[1:]):
            r = self.route_edges(a, b, vt)
            if r is None:
                return False
            edges.extend(r[0] if not edges else r[0][1:])
            length += r[1]
        rid = self.add_route(edges)
        self.add_vehicle(vid, rid, vt, depart_pos="base", depart_speed="0", arrival_pos="max", tag=tag or kind)
        for (edge, pos, lane, dur) in stops or []:
            L = self.net.edges[edge].length
            try:
                self.T.vehicle.setStop(vid, edge, pos=min(max(pos, 8.0), L - 1.0), laneIndex=lane, duration=dur)
            except Exception as e:  # noqa: BLE001
                log.warning("stop for %s on %s failed: %s", vid, edge, e)
        if kind:
            self.track(vid, kind, label or vid, {"routeLength": round(length, 1), **(data or {})})
        return True

    def remove_vehicle(self, vid: str) -> None:
        try:
            self.T.vehicle.unsubscribe(vid)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.T.vehicle.remove(vid)
        except Exception:  # noqa: BLE001
            pass

    def is_stopped_at(self, vid: str, edge: str) -> bool:
        try:
            return bool(self.T.vehicle.isStopped(vid)) and self.T.vehicle.getRoadID(vid) == edge
        except Exception:  # noqa: BLE001
            return False

    def release_stop(self, vid: str) -> None:
        if vid not in self.vehicles:
            return
        try:
            if self.T.vehicle.isStopped(vid):
                self.T.vehicle.resume(vid)
            else:
                stops = self.T.vehicle.getStops(vid, 1)
                if stops:
                    s = stops[0]
                    self.T.vehicle.setStop(vid, s.lane.rsplit("_", 1)[0], pos=s.endPos, laneIndex=int(s.lane.rsplit("_", 1)[1]), duration=0)
        except Exception as e:  # noqa: BLE001
            log.debug("release stop failed for %s: %s", vid, e)
        tr = self.tracked.get(vid)
        if tr:
            tr.status = "returning"
            tr.data["priority"] = False

    def invalidate_routes(self) -> None:
        self.demand._route_cache.clear()

    def reroute_around(self, edge: str, share: float) -> int:
        """Drivers aware of a closure (navigation, signage) re-plan their route."""
        self.invalidate_routes()
        seg = set(self.net.street_segment(edge))
        n = 0
        rng = self._reroute_rng
        T = self.T
        for vid in list(self.vehicles):
            if vid in self.tracked or base_type(self.vtype.get(vid, "")) in ("wreck", "bus"):
                continue
            try:
                route = T.vehicle.getRoute(vid)
                idx = T.vehicle.getRouteIndex(vid)
            except Exception:  # noqa: BLE001
                continue
            rest = route[idx + 1:]
            if seg.intersection(rest) and rng.random() < share:
                try:
                    T.vehicle.rerouteTraveltime(vid)
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
        if n:
            self.emit("traffic", f"{n} drivers re-routed around {self.net.edges[edge].name}", "info", edge=edge)
        return n

    def edge_point(self, edge: str, pos: float, lane_index: int | None = None) -> tuple[float, float, float]:
        lanes = self.net.car_lanes(edge)
        lane = lanes[0] if lane_index is None else f"{edge}_{lane_index}"
        shape = self.T.lane.getShape(lane)
        # walk the polyline
        remaining = pos
        for (x0, y0), (x1, y1) in zip(shape, shape[1:]):
            seg = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            if remaining <= seg or (x1, y1) == shape[-1]:
                f = 0 if seg == 0 else min(1.0, remaining / seg)
                import math

                ang = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
                return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, ang
            remaining -= seg
        x, y = shape[-1]
        return x, y, 0.0

    # ------------------------------------------------------------ incidents
    def create_incident(self, kind: str, edge: str, lanes: list[int], pos: float, length: float, duration: float | None,
                        severity: str = "moderate", speed_limit: float | None = None, description: str = "") -> Incident:
        T = self.T
        iid = f"inc{next(self._inc_ids)}"
        car_lanes = [self.net.lanes[l].index for l in self.net.car_lanes(edge)]
        lanes = [l for l in lanes if l in car_lanes] or car_lanes[:1]
        mid_lane = lanes[len(lanes) // 2]
        x, y, ang = self.edge_point(edge, pos + length / 2, mid_lane)
        inc = Incident(iid, kind, edge, lanes, pos, length, self.t, None if duration is None else self.t + duration, x, y, ang, severity, description=description)
        if kind == "accident":
            # physically blocked lanes: crashed vehicles stopped in each lane
            for k, li in enumerate(lanes):
                for n in range(2 if severity == "severe" else 1):
                    wid = f"{iid}_w{k}{n}"
                    rid = f"{iid}_r"
                    if rid not in T.route.getIDList():
                        T.route.add(rid, [edge])
                    p = pos + length - n * 6.5
                    T.vehicle.add(wid, rid, typeID="wreck", depart="now", departLane=str(li), departPos=f"{max(1.0, p - 5):.1f}", departSpeed="0")
                    T.vehicle.setStop(wid, edge, pos=max(5.0, p), laneIndex=li, duration=1e6)
                    T.vehicle.setLaneChangeMode(wid, 0)
                    T.vehicle.setSpeedMode(wid, 0)
                    self.vtype[wid] = "wreck"
                    self.tags[wid] = iid
                    inc.blockers.append(wid)
        else:
            for li in lanes:
                lid = f"{edge}_{li}"
                inc.disallowed[lid] = list(T.lane.getDisallowed(lid))
                T.lane.setDisallowed(lid, ["passenger", "truck", "bus", "delivery", "emergency", "taxi", "coach", "bicycle"])
        if speed_limit is not None:
            inc.orig_speed = self.net.edges[edge].speed
            T.edge.setMaxSpeed(edge, speed_limit)
        if len(lanes) >= len(car_lanes):
            # closed in this direction: routing treats it as impassable
            T.edge.adaptTraveltime(edge, 1e5)
            inc.closed = True
        self.incidents[iid] = inc
        return inc

    def clear_incident(self, iid: str) -> None:
        inc = self.incidents.get(iid)
        if inc is None or inc.cleared:
            return
        T = self.T
        for wid in inc.blockers:
            self.remove_vehicle(wid)
        for lid, dis in inc.disallowed.items():
            T.lane.setDisallowed(lid, dis)
        if inc.orig_speed is not None:
            T.edge.setMaxSpeed(inc.edge, inc.orig_speed)
        if inc.closed:
            e = self.net.edges[inc.edge]
            T.edge.adaptTraveltime(inc.edge, e.length / e.speed)
        self.invalidate_routes()
        inc.cleared = True
        inc.t_end = self.t

    # ------------------------------------------------------------------ step
    def step(self) -> None:
        w0 = time.perf_counter()
        T = self.T
        prof = self.scenario.demand(self.t)
        self.demand.step(self.t, self.dt, prof, self.scenario.density)
        self.scenario.on_step(self, self.t)
        self._lights_off()
        self.signals.step(self.t)
        T.simulationStep()
        self.t = T.simulation.getTime() - self.warmup
        self._read_state()
        if self.t >= 0 and self.t - self._last_sample >= 1.0 - 1e-6:
            self._last_sample = self.t
            self._sample()
        dur = self.scenario.duration
        if dur is not None and self.t >= dur:
            self.finished = True
        self.wall_ms = (time.perf_counter() - w0) * 1000

    def _read_state(self) -> None:
        T = self.T
        t = self.t
        for vid in T.simulation.getDepartedIDList():
            T.vehicle.subscribe(vid, VEH_VARS)
            vt = self.vtype.get(vid) or T.vehicle.getTypeID(vid)
            self.vtype[vid] = vt
            b = base_type(vt)
            mode = MODE_OF.get(b, "car")
            self.metrics.depart(vid, mode, b, t, self.tags.get(vid))
            tr = self.tracked.get(vid)
            if tr and tr.data.get("departed") is None:
                tr.data["departed"] = round(t, 1)
        for vid in T.simulation.getArrivedIDList():
            self.vehicles.pop(vid, None)
            self.metrics.arrive(vid, t)
            tr = self.tracked.get(vid)
            if tr and tr.arrived is None:
                tr.arrived = t
                self.scenario.on_arrival(self, tr)
            if not tr:
                self.vtype.pop(vid, None)
            self.tags.pop(vid, None)
        tele = T.simulation.getStartingTeleportNumber()
        if tele:
            self.teleports += tele
            ids = T.simulation.getStartingTeleportIDList()
            self.emit("traffic", f"SUMO started teleporting {tele} vehicle{'s' if tele > 1 else ''} ({', '.join(ids[:3])}); inspect the native log for the cause", "warning")
        col = T.simulation.getCollidingVehiclesNumber()
        if col:
            self.collisions += col
        self.safety.observe(t, T.simulation.getCollisions() if col else ())
        subs = T.vehicle.getAllSubscriptionResults()
        vehicles: dict[str, tuple] = {}
        for vid, r in subs.items():
            x, y = r[tc.VAR_POSITION]
            if x < -1e6 or r[tc.VAR_SPEED] < 0:  # teleporting: SUMO reports INVALID_DOUBLE_VALUE
                continue
            vehicles[vid] = (x, y, r[tc.VAR_ANGLE], r[tc.VAR_SPEED], r[tc.VAR_SIGNALS])
            self.metrics.update_vehicle(vid, r[tc.VAR_TIMELOSS], r[tc.VAR_ACCUMULATED_WAITING_TIME], r[tc.VAR_DISTANCE])
        self.vehicles = vehicles
        for pid in T.simulation.getDepartedPersonIDList():
            T.person.subscribe(pid, PER_VARS)
        persons: dict[str, tuple] = {}
        for pid, r in T.person.getAllSubscriptionResults().items():
            if tc.VAR_POSITION not in r:
                continue
            x, y = r[tc.VAR_POSITION]
            if x < -1e6:
                continue
            persons[pid] = (x, y, r[tc.VAR_ANGLE], r[tc.VAR_SPEED])
        self.persons = persons
        for tid, r in T.trafficlight.getAllSubscriptionResults().items():
            self.tls_state[tid] = r[tc.TL_RED_YELLOW_GREEN_STATE]
            self.tls_phase[tid] = r[tc.TL_CURRENT_PHASE]
        self.signals.observe(t, self.tls_state, self.tls_phase)
        if t - self._last_edge_poll >= 1.0 - 1e-6:
            self._last_edge_poll = t
            self._poll_edges()

    def _poll_edges(self) -> None:
        """Edge occupancy, queues and speeds (once per simulated second)."""
        E = self.T.edge
        stats: dict[str, tuple] = {}
        for eid in self.net.edges:
            n = E.getLastStepVehicleNumber(eid)
            if n:
                stats[eid] = (n, E.getLastStepHaltingNumber(eid), E.getLastStepMeanSpeed(eid))
        self.edge_stats = stats

    def junction_queues(self) -> dict[str, int]:
        es = self.edge_stats
        return {j: sum(es[e][1] for e in edges if e in es) for j, edges in self._approach_lanes.items()}

    def _sample(self) -> None:
        t = self.t
        active: dict[str, int] = {}
        speeds: list[float] = []
        for vid, v in self.vehicles.items():
            mode = MODE_OF.get(base_type(self.vtype.get(vid, "sedan")), "car")
            if mode == "incident":
                continue
            active[mode] = active.get(mode, 0) + 1
            if mode in ("car", "truck", "bus"):
                speeds.append(v[3])
        queues = self.junction_queues()
        pending = len(self.T.simulation.getPendingVehicles())
        self.kpi = self.metrics.sample(t, active, speeds, queues, pending, self.teleports, len(self.persons))
        self.kpi["queues"] = {j: q for j, q in queues.items() if q}
        self.kpi["modes"] = {m: st.to_dict() for m, st in sorted(self.metrics.modes.items())}
        # congestion alerts (measured, with hysteresis)
        for j, q in queues.items():
            if q >= 14:
                since = self._queue_alert.setdefault(j, t)
                if t - since >= 30 and j not in self._queue_alerted:
                    self._queue_alerted.add(j)
                    self.emit("traffic", f"Queue building at {self.junction_name(j)}: {q} vehicles stopped on approaches", "warning", tls=j)
            else:
                self._queue_alert.pop(j, None)
                if j in self._queue_alerted and q <= 5:
                    self._queue_alerted.discard(j)
                    self.emit("traffic", f"Queue at {self.junction_name(j)} has dissipated", "success", tls=j)

    # ---------------------------------------------------------------- output
    def frame(self) -> dict[str, Any]:
        vt = self.vtype
        v = [
            [vid, TYPE_CODES.get(base_type(vt.get(vid, "sedan")), 0), round(s[0], 2), round(s[1], 2), round(s[2], 1), round(s[3], 2), s[4]]
            for vid, s in self.vehicles.items()
        ]
        p = [[pid, round(s[0], 2), round(s[1], 2), round(s[2], 0), round(s[3], 2)] for pid, s in self.persons.items()]
        return {"type": "frame", "t": round(self.t, 2), "v": v, "p": p, "tls": self.tls_state}

    def edge_frame(self) -> dict[str, list]:
        return {e: [s[0], s[1], round(s[2], 1)] for e, s in self.edge_stats.items() if s[0]}

    def state(self) -> dict[str, Any]:
        """Slow-changing state: incidents, tracked vehicles, scenario status."""
        return {
            "incidents": [i.to_dict() for i in self.incidents.values()],
            "tracked": [tr.to_dict() for tr in self.tracked.values()],
            "signals": self.signals.summary(),
            "scenario": self.scenario.status(self),
        }

    def vehicle_detail(self, vid: str) -> dict[str, Any] | None:
        if vid not in self.vehicles:
            return None
        T = self.T
        try:
            route = list(T.vehicle.getRoute(vid))
            idx = T.vehicle.getRouteIndex(vid)
            road = T.vehicle.getRoadID(vid)
            nxt = T.vehicle.getNextTLS(vid)
            leader = T.vehicle.getLeader(vid, 80)
            trip = self.metrics.trips.get(vid)
            b = base_type(self.vtype.get(vid, ""))
            out = {
                "id": vid, "type": b, "profile": self.vtype.get(vid), "mode": MODE_OF.get(b, "car"),
                "speed": round(T.vehicle.getSpeed(vid) * 3.6, 1), "accel": round(T.vehicle.getAcceleration(vid), 2),
                "allowedSpeed": round(T.vehicle.getAllowedSpeed(vid) * 3.6, 1),
                "road": road, "roadName": self.net.describe_edge(road) if road in self.net.edges else "Intersection",
                "lane": T.vehicle.getLaneIndex(vid), "routeLength": len(route), "routeIndex": idx,
                "destination": self.net.describe_edge(route[-1]) if route and route[-1] in self.net.edges else route[-1] if route else None,
                "route": route[idx:],
                "nextSignal": {"junction": self.junction_name(nxt[0][0]), "distance": round(nxt[0][2], 1), "state": nxt[0][3]} if nxt else None,
                "leader": {"id": leader[0], "gap": round(leader[1], 1)} if leader and leader[0] else None,
                "waiting": round(trip.waiting, 1) if trip else None, "timeLoss": round(trip.time_loss, 1) if trip else None,
                "travelled": round(trip.distance, 1) if trip else None, "departed": round(trip.depart, 1) if trip else None,
                "tau": round(T.vehicle.getTau(vid), 2), "maxAccel": round(T.vehicle.getAccel(vid), 2), "decel": round(T.vehicle.getDecel(vid), 2),
                "minGap": round(T.vehicle.getMinGap(vid), 2), "speedFactor": round(T.vehicle.getSpeedFactor(vid), 2),
                "tracked": self.tracked[vid].to_dict() if vid in self.tracked else None,
            }
            return out
        except Exception as e:  # noqa: BLE001
            log.debug("vehicle detail failed: %s", e)
            return None

    def junction_detail(self, jid: str) -> dict[str, Any] | None:
        meta = self.net.city.get("junctions", {}).get(jid)
        if meta is None:
            return None
        out: dict[str, Any] = {"id": jid, "name": meta["name"], "control": meta["control"]}
        approaches = []
        for e in self.net.edges.values():
            if e.to != jid or e.kind == "exit":
                continue
            seg = self.net.street_segment(e.id)
            sts = [self.edge_stats.get(x, (0, 0, 0)) for x in seg]
            n = sum(s[0] for s in sts)
            approaches.append({"edge": e.id, "heading": self.net.edge_heading(e.id), "name": e.name, "queue": sum(s[1] for s in sts),
                               "vehicles": n, "speed": round(sum(s[0] * s[2] for s in sts) / n * 3.6, 1) if n else None})
        out["approaches"] = approaches
        if jid in self.tls_state:
            T = self.T
            prog = T.trafficlight.getProgram(jid)
            out["signal"] = {
                "state": self.tls_state[jid], "phase": self.tls_phase.get(jid), "program": prog,
                "nextSwitch": round(T.trafficlight.getNextSwitch(jid) - self.warmup - self.t, 1),
                "phases": [{"duration": d, "state": s} for d, s in self.net.tls_programs.get(jid, [])],
            }
        out["maxQueue"] = self.metrics.junction_queue_max.get(jid, 0)
        return out

    def results(self) -> dict[str, Any]:
        return {
            "t": round(self.t, 1),
            "metrics": self.metrics.summary(),
            "last": {k: v for k, v in self.kpi.items() if k not in ("queues",)},
            "teleports": self.teleports,
            "collisions": self.collisions,
            "safety": self.safety.summary(),
            "demandAccounting": {"backgroundSubmitted": self.demand.counter,
                                 "backgroundRouteFailures": self.demand.failed,
                                 "pendingNow": len(self.T.simulation.getPendingVehicles()),
                                 "note": "Submitted vehicles may still be pending or be discarded by SUMO; they are not departures. Legacy metrics include warm-up."},
            "signals": self.signals.summary(),
            "scenario": self.scenario.results(self),
            "tracked": [tr.to_dict() for tr in self.tracked.values()],
            "incidents": [i.to_dict() for i in self.incidents.values()],
        }
