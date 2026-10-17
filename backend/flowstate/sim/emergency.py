"""Drivers make way for emergency vehicles with their lights on.

SUMO's *bluelight* device (see ``vtypes.py``) only nudges drivers sideways inside their lane, so a responder still
queues behind traffic and at red lights. :class:`EmergencyCorridor` adds the behaviour drivers are taught for an
approaching siren, acting through TraCI on vehicles near each active unit (``Engine.emergency_active()``, i.e. a
tracked responder whose lights are on):

* **Ahead, same lane** — on a multi-lane street the driver changes out of the responder's lane; otherwise it pulls
  toward the curb and keeps moving, since stopping on a one-lane street would only hold the responder up.
* **Ahead, other lanes** — the driver slows down, edges away from the responder's lane and does not change into it
  (on multi-lane streets this opens a corridor between lanes, the way drivers part for a siren on an arterial).
* **Cross traffic** — drivers approaching the junction the responder is about to enter stop short of the stop line
  if they can do so with normal braking; drivers already committed to the junction carry on and clear it.
* **The responder** — when its own lane is blocked ahead it takes a clearer lane that still continues on its route.

A unit that turns its lights off switches to its ``@quiet`` vehicle type (see ``vtypes.py``) and stops at red
lights again; otherwise a returning unit creeping over the stop line can lock a junction with a car inside it.

Every command is released as soon as the responder has passed, has left, or turned its lights off (and after
``MAX_HOLD_S`` at most), so drivers resume their own trips. Red lights are still crossed through SUMO's junction
model (``jmDriveAfterRedTime`` / ``jmDriveRedSpeed``): slowly, and yielding to vehicles that are already moving.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .vtypes import QUIET, base_type

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine

log = logging.getLogger(__name__)

DEFAULT_LC_MODE = 1621  # SUMO default lane-change mode
YIELD_LC_MODE = 513  # strategic changes and TraCI requests only (no overtaking, no lateral re-centring)
NO_YIELD_TYPES = {"bike", "wreck", "suspect", "ambulance", "police"}


@dataclass
class Yield:
    vid: str
    ev: str
    action: str  # lane | adjacent | curb | gap | hold
    since: float
    lc_mode_set: bool = False


class EmergencyCorridor:
    PERIOD = 0.5
    AHEAD_M = 80.0  # how far ahead of a responder drivers notice it
    CHANGE_LANE_M = 60.0  # drivers in its lane closer than this move out of it
    LANE_END_M = 40.0  # no lane changes asked for closer than this to the end of the lane
    GAP_BACK_M = 25.0  # drivers this far behind it in the lane it must merge into hold back to open a gap
    CROSS_ARM_M = 70.0  # cross traffic is held once the responder is this close to the stop line
    CROSS_HOLD_M = 40.0  # ... for drivers within this distance of their own stop line
    ADJACENT_SPEED = 0.55  # share of current speed kept by drivers beside the responder's path
    MIN_CRAWL = 2.0
    CURB_MARGIN = 0.15
    MAX_HOLD_S = 20.0
    COOLDOWN_S = 8.0  # a driver released after MAX_HOLD_S is left alone this long
    ANNOUNCE_AT = 3

    def __init__(self, engine: "Engine", enabled: bool = True):
        self.engine = engine
        self.enabled = enabled
        self.yielding: dict[str, Yield] = {}
        self._last = -1e9
        self._incoming: dict[str, list[str]] = {}
        self._announced: set[tuple[str, str]] = set()
        self._cooldown: dict[str, float] = {}
        self._motor_lanes: dict[str, list[int]] = {}
        self._quiet: set[str] = set()
        self.yields = 0
        self.cross_holds = 0
        self.gap_holds = 0
        self.lane_moves = 0
        self.max_concurrent = 0
        self.failures = 0

    @property
    def T(self):
        return self.engine.T

    def start(self) -> None:
        for e in self.engine.net.edges.values():
            if e.kind in ("street", "entry"):
                self._incoming.setdefault(e.to, []).append(e.id)

    # ------------------------------------------------------------------ step
    def step(self, t: float) -> None:
        if t - self._last < self.PERIOD:
            return
        self._last = t
        self._lights_off()
        if not self.enabled:
            return
        eng = self.engine
        wanted: dict[str, tuple[str, str, int]] = {}
        for ev in eng.emergency_active():
            try:
                self._plan(ev, wanted)
            except Exception as e:  # noqa: BLE001
                self.failures += 1
                log.debug("emergency corridor failed for %s: %s", ev, e)
        for vid in [v for v in self.yielding if v not in wanted or t - self.yielding[v].since > self.MAX_HOLD_S]:
            if t - self.yielding[vid].since > self.MAX_HOLD_S:
                self._cooldown[vid] = t + self.COOLDOWN_S
            self._release(vid)
        self._cooldown = {v: until for v, until in self._cooldown.items() if until > t}
        for vid in self._cooldown:
            wanted.pop(vid, None)
        for vid, (ev, action, lane) in wanted.items():
            if vid in self.yielding and self.yielding[vid].action == action:
                continue
            try:
                self._apply(vid, ev, action, lane)
            except Exception as e:  # noqa: BLE001
                self.failures += 1
                log.debug("yield command failed for %s: %s", vid, e)
                continue
            if vid not in self.yielding:
                self.yields += 1
                self.yielding[vid] = Yield(vid, ev, action, t)
            y = self.yielding[vid]
            y.action = action
            y.lc_mode_set = y.lc_mode_set or action not in ("hold", "gap")
        self.max_concurrent = max(self.max_concurrent, len(self.yielding))
        self._announce()

    def _plan(self, ev: str, wanted: dict[str, tuple[str, str, int]]) -> None:
        """Decide what each driver near ``ev`` should do (first responder to claim a driver wins)."""
        T = self.T
        eng = self.engine
        road = T.vehicle.getRoadID(ev)
        route, idx = T.vehicle.getRoute(ev), T.vehicle.getRouteIndex(ev)
        internal = road.startswith(":")
        ev_pos = T.vehicle.getLanePosition(ev)
        if not internal and road in eng.net.edges:
            self._plan_block(ev, road, ev_pos, wanted)
        # blocks further along the route: drivers ahead shift aside (a corridor on multi-lane streets) without
        # slowing, since the responder does not know yet which of their lanes it will use
        offset = T.lane.getLength(T.vehicle.getLaneID(ev)) - ev_pos
        nxt = idx + 1
        while nxt < len(route) and offset < self.AHEAD_M and route[nxt] in eng.net.edges:
            edge = route[nxt]
            for vid in T.edge.getLastStepVehicleIDs(edge):
                if vid not in wanted and offset + T.vehicle.getLanePosition(vid) <= self.AHEAD_M and self._can_yield(vid):
                    wanted[vid] = (ev, "curb", -1)
            offset += eng.net.edges[edge].length
            nxt += 1
        self._cross_traffic(ev, road, route, idx, internal, wanted)
        self._choose_lane(ev, road, internal)

    def _plan_block(self, ev: str, road: str, ev_pos: float, wanted: dict[str, tuple[str, str, int]]) -> None:
        """Drivers on the responder's own block."""
        T = self.T
        motor = self._motor(road)
        ev_lane = T.vehicle.getLaneIndex(ev)
        target = self._target_lane(ev, road, ev_lane)
        for vid in T.edge.getLastStepVehicleIDs(road):
            if vid == ev or vid in wanted or not self._can_yield(vid):
                continue
            ahead = T.vehicle.getLanePosition(vid) - ev_pos
            if ahead > self.AHEAD_M:
                continue
            vl = T.vehicle.getLaneIndex(vid)
            if vl == target != ev_lane:
                if -self.GAP_BACK_M <= ahead <= 2.0:
                    wanted[vid] = (ev, "gap", -1)  # open a gap for the lane change the responder needs
                elif ahead > 2.0:
                    wanted[vid] = (ev, "curb", -1)
            elif ahead < 0:
                continue
            elif vl == ev_lane:
                out = self._exit_lane(vid, road, vl, target, motor) if ahead < self.CHANGE_LANE_M else None
                wanted[vid] = (ev, "lane", out) if out is not None else (ev, "curb", -1)
            else:
                wanted[vid] = (ev, "adjacent", -1)  # beside the responder's path: slow down so it can overtake

    def _exit_lane(self, vid: str, road: str, lane: int, avoid: int, motor: list[int]) -> int | None:
        """A neighbouring lane the driver can move into and still follow its own route, or None.

        Drivers that are stopped, or already close to the stop line, stay in their lane: a lane change there either
        cannot happen or would strand them in a lane that does not continue their trip, boxing the responder in.
        """
        T = self.T
        if T.vehicle.getSpeed(vid) < 1.0:
            return None
        if T.lane.getLength(T.vehicle.getLaneID(vid)) - T.vehicle.getLanePosition(vid) < self.LANE_END_M:
            return None
        ok = {int(info[0].rsplit("_", 1)[1]) for info in T.vehicle.getBestLanes(vid) if info[0].startswith(f"{road}_") and info[4]}
        for i in (lane - 1, lane + 1):  # pull right where possible
            if i in motor and i != avoid and i in ok:
                return i
        return None

    def _target_lane(self, ev: str, road: str, ev_lane: int) -> int:
        """The lane the responder must be in to continue on its route (its own lane when that already works)."""
        best = []
        for info in self.T.vehicle.getBestLanes(ev):
            if info[0].startswith(f"{road}_") and info[3] == 0 and info[4]:
                best.append(int(info[0].rsplit("_", 1)[1]))
        if not best or ev_lane in best:
            return ev_lane
        return min(best, key=lambda i: abs(i - ev_lane))

    def _motor(self, edge: str) -> list[int]:
        lanes = self._motor_lanes.get(edge)
        if lanes is None:
            net = self.engine.net
            e = net.edges.get(edge)
            lanes = [] if e is None else [net.lanes[l].index for l in e.lanes if l in net.lanes and net.lanes[l].allows_car]
            self._motor_lanes[edge] = lanes
        return lanes

    def _cross_traffic(self, ev: str, road: str, route: tuple[str, ...], idx: int, internal: bool,
                       wanted: dict[str, tuple[str, str, int]]) -> None:
        T = self.T
        eng = self.engine
        if internal:
            if idx >= len(route) or route[idx] not in eng.net.edges:
                return
            approach, dist = route[idx], 0.0  # still inside the junction box
        else:
            if road not in eng.net.edges:
                return
            approach = road
            dist = T.lane.getLength(T.vehicle.getLaneID(ev)) - T.vehicle.getLanePosition(ev)
        nxt = idx + 1
        while len(self._incoming.get(eng.net.edges[approach].to, ())) < 2:  # mid-block split (left-turn bay)
            if nxt >= len(route) or route[nxt] not in eng.net.edges or dist > self.CROSS_ARM_M:
                return
            approach = route[nxt]
            dist += eng.net.edges[approach].length
            nxt += 1
        if dist > self.CROSS_ARM_M:
            return
        incoming = self._incoming[eng.net.edges[approach].to]
        for edge in incoming:
            if edge == approach:
                continue
            for vid in T.edge.getLastStepVehicleIDs(edge):
                if vid in wanted or not self._can_yield(vid):
                    continue
                left = T.lane.getLength(T.vehicle.getLaneID(vid)) - T.vehicle.getLanePosition(vid)
                if left > self.CROSS_HOLD_M:
                    continue
                if vid not in self.yielding or self.yielding[vid].action != "hold":
                    v = T.vehicle.getSpeed(vid)
                    if v * v / (2.0 * max(1.0, T.vehicle.getDecel(vid))) + 1.0 > left:
                        continue  # cannot stop comfortably: it goes through and clears the box
                wanted[vid] = (ev, "hold", -1)

    def _choose_lane(self, ev: str, road: str, internal: bool) -> None:
        """Leave a blocked lane for one that is clearer and still continues on the route."""
        T = self.T
        if internal:
            return
        leader = T.vehicle.getLeader(ev, 50.0)
        if not leader or not leader[0] or T.vehicle.getSpeed(leader[0]) > 0.6 * max(3.0, T.vehicle.getSpeed(ev)):
            return
        cur = T.vehicle.getLaneIndex(ev)
        best = None
        for info in T.vehicle.getBestLanes(ev):
            lane_id, _length, occupation, offset, allows = info[0], info[1], info[2], info[3], info[4]
            if not lane_id.startswith(f"{road}_") or not allows or offset != 0:
                continue
            i = int(lane_id.rsplit("_", 1)[1])
            if i == cur or abs(i - cur) != 1 or i not in self._motor(road):
                continue
            if best is None or occupation < best[1]:
                best = (i, occupation)
        if best is not None:
            T.vehicle.changeLane(ev, best[0], 3.0)
            self.lane_moves += 1

    def _lights_off(self) -> None:
        eng = self.engine
        for vid, tr in eng.tracked.items():
            if tr.kind in ("ambulance", "police", "responder") and vid in eng.vehicles and vid not in self._quiet                     and not tr.data.get("priority", True):
                self._quiet.add(vid)
                quiet = f"{eng.vtype.get(vid) or self.T.vehicle.getTypeID(vid)}{QUIET}"
                try:
                    self.T.vehicle.setType(vid, quiet)
                    eng.vtype[vid] = quiet
                except Exception as e:  # noqa: BLE001
                    self.failures += 1
                    log.debug("could not switch %s to %s: %s", vid, quiet, e)

    def _can_yield(self, vid: str) -> bool:
        eng = self.engine
        if vid not in eng.vehicles:
            return False
        if base_type(eng.vtype.get(vid, "")) in NO_YIELD_TYPES:
            return False
        tr = eng.tracked.get(vid)
        if tr is not None and tr.kind in ("ambulance", "police", "responder", "suspect"):
            return False
        return not self.T.vehicle.isStopped(vid)

    # --------------------------------------------------------------- commands
    def _apply(self, vid: str, ev: str, action: str, lane: int) -> None:
        T = self.T
        if action in ("hold", "gap"):
            T.vehicle.setSpeed(vid, 0.0)
            if action == "hold":
                self.cross_holds += 1
            else:
                self.gap_holds += 1
            return
        T.vehicle.setLaneChangeMode(vid, YIELD_LC_MODE)
        if action == "lane":
            T.vehicle.changeLane(vid, lane, 4.0)
            T.vehicle.setSpeed(vid, -1)
            return
        side = self._away_side(vid, ev)
        room = (T.lane.getWidth(T.vehicle.getLaneID(vid)) - T.vehicle.getWidth(vid)) / 2.0 - self.CURB_MARGIN
        shift = side * max(0.0, room) - T.vehicle.getLateralLanePosition(vid)
        if abs(shift) > 0.1:
            T.vehicle.changeSublane(vid, shift)
        v = T.vehicle.getSpeed(vid)
        if action == "adjacent" and v > self.MIN_CRAWL:
            T.vehicle.setSpeed(vid, max(self.MIN_CRAWL, self.ADJACENT_SPEED * v))
        else:
            T.vehicle.setSpeed(vid, -1)

    def _away_side(self, vid: str, ev: str) -> int:
        """+1 = toward the left edge of its lane, -1 = toward the right curb."""
        T = self.T
        road = T.vehicle.getRoadID(vid)
        motor = self._motor(road)
        idx = T.vehicle.getLaneIndex(vid)
        if len(motor) <= 1:
            return -1
        if T.vehicle.getRoadID(ev) == road:
            ev_idx = T.vehicle.getLaneIndex(ev)
            if idx != ev_idx:
                return -1 if idx < ev_idx else 1
        return 1 if idx == motor[-1] else -1  # corridor between the leftmost lane and the rest

    def _release(self, vid: str) -> None:
        y = self.yielding.pop(vid, None)
        if y is None:
            return
        T = self.T
        try:
            T.vehicle.setSpeed(vid, -1)
            if y.lc_mode_set:
                T.vehicle.setLaneChangeMode(vid, DEFAULT_LC_MODE)
        except Exception as e:  # noqa: BLE001 - the vehicle has usually just arrived
            if vid in self.engine.vehicles:
                self.failures += 1
                log.debug("release failed for %s: %s", vid, e)

    def release_all(self) -> None:
        for vid in list(self.yielding):
            self._release(vid)

    def _announce(self) -> None:
        eng = self.engine
        per: dict[str, int] = {}
        for y in self.yielding.values():
            per[y.ev] = per.get(y.ev, 0) + 1
        for ev, n in per.items():
            if n < self.ANNOUNCE_AT or ev not in eng.vehicles:
                continue
            road = self.T.vehicle.getRoadID(ev)
            street = eng.net.edges[road].name if road in eng.net.edges else ""
            key = (ev, street)
            if not street or key in self._announced:
                continue
            self._announced.add(key)
            eng.emit("emergency", f"Drivers pulling over for {eng.label(ev).split(' →')[0]} on {street}", "info", vehicle=ev)

    def summary(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "yields": self.yields, "crossHolds": self.cross_holds, "gapHolds": self.gap_holds, "laneMoves": self.lane_moves,
                "maxConcurrent": self.max_concurrent, "yieldingNow": len(self.yielding)}
