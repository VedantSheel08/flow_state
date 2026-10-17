"""Reusable stochastic scenario components.

Each component draws its concrete instance in :meth:`instantiate` from a
dedicated RNG stream (derived from the run seed) and then acts on SUMO only
through the engine: inserting vehicles, changing lane permissions or speed
limits, adding stops, or shaping the demand profile. Outcomes — queues,
response times, detours — are measured, never scripted.
"""

from __future__ import annotations

import heapq
import logging
import math
import random
from typing import TYPE_CHECKING, Any

from ..sim.vtypes import Conditions, base_type
from .base import BriefingItem, Component, DemandProfile, Marker, Overlay, bell, fmt_clock

if TYPE_CHECKING:  # pragma: no cover
    from ..network.info import NetInfo
    from ..sim.engine import Engine, Tracked
    from .base import Scenario

log = logging.getLogger(__name__)

CLASS_WEIGHT = {"arterial": 3.0, "arterial_bike": 3.0, "collector": 2.0, "local": 1.0}
SEVERITIES = {
    # lanes blocked: "one" | "most" | "all"; clearance minutes range; speed limit past the scene
    "minor": {"lanes": "one", "clear": (5, 9), "limit": None, "label": "Minor"},
    "moderate": {"lanes": "most", "clear": (8, 14), "limit": 25 / 3.6, "label": "Moderate"},
    "severe": {"lanes": "all", "clear": (12, 20), "limit": 20 / 3.6, "label": "Severe"},
}


def run_length(sc: "Scenario") -> float:
    return sc.duration or 600.0


def pick_edge(rng: random.Random, net: "NetInfo", sc: "Scenario", requested: str = "random", min_lanes: int = 1, classes: tuple[str, ...] = (),
              min_length: float = 0.0) -> str:
    cands = [e for e in net.block_edges(min_lanes, classes) if e not in sc.reserved and e not in sc.protected]
    if requested in (None, "", "random") and min_length:
        cands = [e for e in cands if net.edges[e].length >= min_length] or cands
    if requested and requested != "random":
        seg = net.street_segment(requested) if requested in net.edges else []
        main = [e for e in seg if net.edges[e].part != "bay" and e in cands]
        if not main:
            raise ValueError(f"'{requested}' is not a valid mid-block street for this scenario (it may serve the hospital or police station, or already be in use)")
        return main[0]
    if not cands:
        raise ValueError("no valid street segment available")
    return rng.choices(cands, weights=[CLASS_WEIGHT.get(net.edges[e].cls, 1.0) for e in cands])[0]


def poi_edges(net: "NetInfo", kind: str) -> list[str]:
    poi = next((p for p in net.pois.values() if p.kind == kind), None)
    if poi is None:
        return []
    return [e for e in poi.access if e in net.edges and net.edges[e].part != "bay"] or [e for e in poi.access if e in net.edges]


def zone_edges_of_poi(net: "NetInfo", kind: str) -> list[str]:
    poi = next((p for p in net.pois.values() if p.kind == kind), None)
    if poi is None:
        return []
    for z in net.zones:
        if z.use == kind:
            return [e for e in z.edges if net.edges[e].part != "bay"]
    return poi_edges(net, kind)


def curb_lane(net: "NetInfo", edge: str) -> int:
    return net.lanes[net.car_lanes(edge)[0]].index


def free_flow_time(net: "NetInfo", edges: list[str], speed_factor: float = 1.0) -> float:
    return sum(net.edges[e].length / (net.edges[e].speed * speed_factor) for e in edges if e in net.edges)


def estimate_travel(net: "NetInfo", src_edge: str, dst_edge: str) -> float:
    """Free-flow seconds from the end of ``src_edge`` to the start of ``dst_edge`` (graph search, ignores turn bans)."""
    out: dict[str, list[tuple[str, float]]] = {}
    for e in net.edges.values():
        if e.kind in ("street", "entry", "exit") and e.speed > 0:
            out.setdefault(e.frm, []).append((e.to, e.length / e.speed))
    start, goal = net.edges[src_edge].to, net.edges[dst_edge].frm
    dist = {start: 0.0}
    heap = [(0.0, start)]
    while heap:
        d, n = heapq.heappop(heap)
        if n == goal:
            return d + net.edges[dst_edge].length / net.edges[dst_edge].speed
        if d > dist.get(n, 1e18):
            continue
        for m, w in out.get(n, ()):
            if d + w < dist.get(m, 1e18):
                dist[m] = d + w
                heapq.heappush(heap, (d + w, m))
    return 0.0


def scene_stop_pos(rng: random.Random, length: float) -> float:
    """Where a responder stops on a block: clear of the junction on both sides so it can reach its lane."""
    lo, hi = min(25.0, 0.35 * length), max(26.0, length - 20.0)
    return min(max(rng.uniform(0.3, 0.75) * length, lo), hi)


CIVILIAN_CLASSES = ["passenger", "truck", "bus", "delivery", "taxi", "coach", "bicycle"]


def queue_at(eng: "Engine", edge: str) -> int:
    """Stopped vehicles on a block and on the streets feeding it."""
    q = eng.edge_stats.get(edge, (0, 0, 0))[1]
    frm = eng.net.edges[edge].frm
    for up in eng.net.edges.values():
        if up.to == frm and up.kind != "exit":
            q += eng.edge_stats.get(up.id, (0, 0, 0))[1]
    return q


def closed_edges(eng: "Engine") -> set[str]:
    return {i.edge for i in eng.incidents.values() if i.closed and not i.cleared}


def divert_from_closure(eng: "Engine", edge: str) -> int:
    """Re-plan every driver whose trip uses a street that has just been closed.

    Drivers who merely pass through are re-routed (SUMO's travel times already price the closure in). Drivers whose
    *destination* is on the closed block cannot be: they would wait at the barrier for good and block everyone behind
    them, so they head for the nearest open block instead. So do drivers boxed in by one-way streets and turn bans,
    for whom every way to their destination runs over the closed block (a block nearby, else the nearest map exit).
    """
    net, T = eng.net, eng.T
    seg = set(net.street_segment(edge))
    closed = closed_edges(eng) | seg
    ex, ey = net.node_pos[net.edges[edge].to]
    open_blocks = sorted((e for e in net.block_edges(1) if e not in closed),
                         key=lambda e: math.dist(net.node_pos[net.edges[e].to], (ex, ey)))
    exits = sorted(net.exit_edges, key=lambda e: math.dist(net.node_pos[net.edges[e].to], (ex, ey)))
    moved = 0
    for vid in list(eng.vehicles):
        if vid in eng.tracked or base_type(eng.vtype.get(vid, "")) in ("wreck", "bus"):
            continue
        try:
            route, idx = T.vehicle.getRoute(vid), T.vehicle.getRouteIndex(vid)
            ahead = route[idx + 1:]
            if not seg.intersection(ahead):
                continue
            if route[-1] not in seg:
                T.vehicle.rerouteTraveltime(vid)
                moved += 1
                r2, i2 = T.vehicle.getRoute(vid), T.vehicle.getRouteIndex(vid)
                if not seg.intersection(r2[i2 + 1:]):
                    continue
                # boxed in (one-way streets, turn bans): no way to the destination that avoids the closure
            for alt in open_blocks[:6] + exits:
                try:
                    T.vehicle.changeTarget(vid, alt)
                    moved += 1
                    break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    if moved:
        eng.emit("traffic", f"{moved} drivers diverted around the closure of {net.edges[edge].name}", "info", edge=edge)
    return moved


class ResponderGuard:
    """Keeps emergency vehicles moving.

    * closures that appear while a unit is already en route: re-plan its route with current travel times;
    * a unit whose stop is in a lane it never reached (blocked lane change) gets the stop moved to its own lane;
    * a unit wedged inside a junction (SUMO's bluelight device makes nearby cars yield to it, and a car that yields
      inside the junction can lock the unit in) is placed on the far side after ``JUNCTION_STUCK_S`` instead of waiting
      for SUMO's 240 s teleport. Each of these is counted in ``rescues`` and reported in the results.
    """

    PERIOD = 2.0
    STUCK_S = 8.0
    JUNCTION_STUCK_S = 30.0

    def __init__(self) -> None:
        self._last = -1e9
        self._planned: dict[str, frozenset[str]] = {}
        self.reroutes = 0
        self.stop_fixes = 0
        self.rescues = 0
        self.failures = 0

    def step(self, eng: "Engine", t: float, scenes: dict[str, str | None]) -> None:
        """``scenes`` maps each active unit to the edge it is heading for (its scene is allowed to be closed), or None."""
        if t - self._last < self.PERIOD:
            return
        self._last = t
        T = eng.T
        closed = closed_edges(eng)
        for vid, scene in scenes.items():
            if vid not in eng.vehicles:
                continue
            try:
                if closed - {scene} and self._planned.get(vid) != frozenset(closed):
                    route, idx = T.vehicle.getRoute(vid), T.vehicle.getRouteIndex(vid)
                    if (set(route[idx + 1:]) & closed) - {scene}:
                        T.vehicle.rerouteTraveltime(vid)
                        self.reroutes += 1
                        eng.emit("emergency", f"{eng.label(vid).split(' →')[0]} re-routed around a closure", "info", vehicle=vid)
                    self._planned[vid] = frozenset(closed)
                self._unstick(eng, vid)
            except Exception as e:  # noqa: BLE001
                self.failures += 1
                log.debug("responder guard failed for %s: %s", vid, e)

    def _leave_junction(self, eng: "Engine", vid: str) -> None:
        T = eng.T
        route, idx = T.vehicle.getRoute(vid), T.vehicle.getRouteIndex(vid)
        nxt = route[idx + 1] if idx + 1 < len(route) else None
        for link in T.lane.getLinks(T.vehicle.getLaneID(vid)):
            target = link[0]
            if nxt is not None and not target.startswith(f"{nxt}_"):
                continue
            if any(T.vehicle.getLanePosition(v) < 15.0 for v in T.lane.getLastStepVehicleIDs(target)):
                continue
            T.vehicle.moveTo(vid, target, 2.0)
            self.rescues += 1
            eng.emit("emergency", f"{eng.label(vid).split(' →')[0]} cleared a junction deadlock", "warning", vehicle=vid)
            return

    def _unstick(self, eng: "Engine", vid: str) -> None:
        T = eng.T
        if eng.vehicles[vid][3] > 0.3 or T.vehicle.isStopped(vid) or T.vehicle.getWaitingTime(vid) < self.STUCK_S:
            return
        if T.vehicle.getRoadID(vid).startswith(":"):
            if T.vehicle.getWaitingTime(vid) >= self.JUNCTION_STUCK_S:
                self._leave_junction(eng, vid)
            return
        stops = T.vehicle.getStops(vid, 1)
        if not stops:
            return
        s = stops[0]
        edge, lane = s.lane.rsplit("_", 1)
        cur = T.vehicle.getLaneIndex(vid)
        if T.vehicle.getRoadID(vid) != edge:
            return
        pos = T.vehicle.getLanePosition(vid)
        last = eng.net.edges[edge].length - 1.0
        if int(lane) != cur:  # never reached the stop's lane: stop where it is, in its own lane
            T.vehicle.replaceStop(vid, 0, edge, pos=min(pos + 3.0, last), laneIndex=cur, duration=max(s.duration, 5.0))
        elif 0.05 < s.endPos - pos < 25.0:  # held short of the stop by a vehicle parked ahead: take the spot it left
            T.vehicle.replaceStop(vid, 0, edge, pos=min(pos + 0.3, last), laneIndex=cur, duration=max(s.duration, 5.0), startPos=max(pos - 0.2, 0.0))
        else:
            return
        self.stop_fixes += 1


# ---------------------------------------------------------------------------
# Demand shaping
# ---------------------------------------------------------------------------
class RushHour(Component):
    """Directional commuter peak with a seeded demand curve and OD pattern."""

    name = "rush_hour"

    def __init__(self, period: str, peak: float, variability: float):
        self.period = period
        self.peak_req = peak
        self.var = variability

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        v = self.var
        self.peak = max(1.0, self.peak_req * rng.uniform(1 - 0.18 * v, 1 + 0.18 * v))
        self.start = D * rng.uniform(0.02, 0.12)
        self.ramp = D * rng.uniform(0.14, 0.24)
        self.plateau = D * rng.uniform(0.22, 0.38)
        self.end_ramp = D * rng.uniform(0.15, 0.25)
        raw = {s: rng.gammavariate(2.0 + 4 * (1 - v), 1.0) for s in ("W", "E", "N", "S")}
        tot = sum(raw.values())
        self.sides = {s: raw[s] / tot for s in raw}
        self.core_bias = rng.uniform(3.0, 5.0)
        self.dominant = max(self.sides, key=self.sides.get)

    def _b(self, t: float) -> float:
        s = self.start
        return bell(t, s, s + self.ramp, s + self.ramp + self.plateau, s + self.ramp + self.plateau + self.end_ramp)

    def modify_demand(self, t: float, p: DemandProfile) -> None:
        b = self._b(t)
        p.rate *= 1.0 + (self.peak - 1.0) * b
        fringe = {s: 0.6 + 2.4 * self.sides[s] * 4 * b / 4 for s in self.sides}
        if self.period == "am":  # suburbs -> downtown blocks
            p.origin_w = {**fringe, "internal": 1.6 * (1 - 0.7 * b)}
            p.dest_w = {"W": 1 - 0.6 * b, "E": 1 - 0.6 * b, "N": 1 - 0.6 * b, "S": 1 - 0.6 * b, "internal": 1.6 + 4.0 * b}
        else:  # downtown blocks -> suburbs
            p.origin_w = {"W": 1 - 0.6 * b, "E": 1 - 0.6 * b, "N": 1 - 0.6 * b, "S": 1 - 0.6 * b, "internal": 1.6 + 4.0 * b}
            p.dest_w = {**fringe, "internal": 1.6 * (1 - 0.7 * b)}
        p.core_bias = 1.5 + (self.core_bias - 1.5) * b

    def markers(self) -> list[Marker]:
        s = self.start
        return [Marker(t=s, label="Demand ramps up", kind="demand"), Marker(t=s + self.ramp, label="Peak", kind="demand"),
                Marker(t=s + self.ramp + self.plateau, label="Peak easing", kind="demand")]

    def briefing(self) -> list[BriefingItem]:
        names = {"W": "west", "E": "east", "N": "north", "S": "south"}
        ranked = sorted(self.sides.items(), key=lambda kv: -kv[1])
        corridor = ", ".join(f"{names[s]} {w:.0%}" for s, w in ranked[:2])
        s = self.start
        return [
            BriefingItem(label="Peak demand", value=f"{self.peak:.2f}× base", detail=f"{fmt_clock(s + self.ramp)}–{fmt_clock(s + self.ramp + self.plateau)}"),
            BriefingItem(label="Direction", value="Inbound to downtown" if self.period == "am" else "Outbound from downtown",
                         detail=f"suburban trip ends: {corridor}"),
        ]

    def on_step(self, eng, t: float) -> None:
        s = self.start
        for key, at, msg in (("ramp", s, "Commuter demand rising"), ("peak", s + self.ramp, f"Peak demand reached ({self.peak:.1f}× base)"),
                             ("ease", s + self.ramp + self.plateau, "Peak demand easing")):
            if t >= at and key not in getattr(self, "_said", set()):
                self._said = getattr(self, "_said", set()) | {key}
                eng.emit("scenario", msg, "info")

    def results(self, eng) -> dict[str, Any]:
        rows = [h for h in eng.metrics.history if self._b(h["t"]) > 0.6]
        tl = [h["timeLoss"] for h in rows if h["timeLoss"] is not None]
        return {"peakQueued": max((h["queued"] for h in rows), default=0), "peakTimeLoss": round(sum(tl) / len(tl), 1) if tl else None}


class VenueSurge(Component):
    """Arrival or departure surge around a venue (stadium) with walkers."""

    name = "venue"

    def __init__(self, venue: str, phase: str, attendance: int):
        self.venue = venue
        self.phase = phase
        self.attendance = attendance

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        self.edges = zone_edges_of_poi(net, self.venue)
        if not self.edges:
            raise ValueError(f"network has no {self.venue}")
        self.poi = next(p for p in net.pois.values() if p.kind == self.venue)
        self.vehicles = int(self.attendance * rng.uniform(0.85, 1.15))
        self.center = D * rng.uniform(0.35, 0.55)
        self.width = D * rng.uniform(0.18, 0.3)
        self.start = self.center - self.width
        self.end = self.center + self.width
        # bell area ≈ width * 1.0 (two smoothstep ramps of `width` each with no plateau ≈ width)
        self.peak_rate = self.vehicles / (self.width / 3600.0)

    def _b(self, t: float) -> float:
        return bell(t, self.start, self.center - 1, self.center + 1, self.end)

    def modify_demand(self, t: float, p: DemandProfile) -> None:
        b = self._b(t)
        if b <= 0:
            return
        extra = self.peak_rate * b
        share = extra / (p.rate + extra)
        p.rate += extra
        if self.phase == "arrival":
            p.attractor_edges, p.attractor_share = self.edges, share
            p.ped_attractor_edges = self.edges
        else:
            p.generator_edges, p.generator_share = self.edges, share
            p.ped_generator_edges = self.edges
        p.ped_rate += 2500 * b
        p.ped_venue_share = 0.85

    def markers(self) -> list[Marker]:
        lbl = "Gates open" if self.phase == "arrival" else "Final whistle"
        return [Marker(t=self.start, label=lbl, kind="demand"), Marker(t=self.center, label="Surge peak", kind="demand")]

    def overlays(self) -> list[Overlay]:
        return [Overlay(id="venue", kind="zone", tone="demand", label=f"{self.poi.name} · {'arrivals' if self.phase == 'arrival' else 'departures'}",
                        edges=self.edges, tStart=round(self.start, 1), tEnd=round(self.end, 1))]

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label="Event traffic", value=f"{self.vehicles} vehicles {'arriving at' if self.phase == 'arrival' else 'leaving'} {self.poi.name}",
                             detail=f"surge {fmt_clock(self.start)}–{fmt_clock(self.end)}, peak {fmt_clock(self.center)}")]

    def on_step(self, eng, t: float) -> None:
        if t >= self.start and not getattr(self, "_said", False):
            self._said = True
            eng.emit("scenario", f"{'Arrivals' if self.phase == 'arrival' else 'Departures'} surge at {self.poi.name} begins", "warning")

    def results(self, eng) -> dict[str, Any]:
        venue = set(self.edges)
        q = [sum(eng.edge_stats.get(e, (0, 0, 0))[1] for e in venue)]
        return {"venueEdges": len(venue), "venueQueueNow": q[0]}


class DemandSpike(Component):
    """Short localized surge of trips from/to a random city block."""

    name = "spike"
    instantiate_order = 0

    def reserved_edges(self) -> set[str]:
        return set(self.edges)

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        zones = [z for z in net.zones if z.edges and not any(e in sc.reserved for e in z.edges)]
        self.zone = rng.choice(zones)
        self.edges = [e for e in self.zone.edges if net.edges[e].part != "bay"]
        self.t0 = D * rng.uniform(0.2, 0.5)
        self.len = D * rng.uniform(0.12, 0.2)
        self.rate = rng.uniform(600, 1100)
        self.outbound = rng.random() < 0.5
        self.where = net.describe_edge(self.edges[0])

    def modify_demand(self, t: float, p: DemandProfile) -> None:
        b = bell(t, self.t0, self.t0 + self.len * 0.3, self.t0 + self.len * 0.7, self.t0 + self.len)
        if b <= 0:
            return
        extra = self.rate * b
        share = extra / (p.rate + extra)
        p.rate += extra
        if self.outbound:
            p.generator_edges, p.generator_share = self.edges, share
        else:
            p.attractor_edges, p.attractor_share = self.edges, share

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label="Demand spike", value=f"+{self.rate:.0f} veh/h {'leaving' if self.outbound else 'arriving at'} a {self.zone.use} block",
                             detail=f"near {self.where}, {fmt_clock(self.t0)}–{fmt_clock(self.t0 + self.len)}")]

    def markers(self) -> list[Marker]:
        return [Marker(t=self.t0, label="Demand spike", kind="demand")]

    def overlays(self) -> list[Overlay]:
        return [Overlay(id=f"spike_{self.edges[0]}", kind="zone", tone="demand", label=f"Demand spike · {self.zone.use} block",
                        edges=self.edges, tStart=round(self.t0, 1), tEnd=round(self.t0 + self.len, 1))]

    def on_step(self, eng, t: float) -> None:
        if t >= self.t0 and not getattr(self, "_said", False):
            self._said = True
            eng.emit("scenario", f"Localized demand spike near {self.where}", "warning")


# ---------------------------------------------------------------------------
# Capacity reductions
# ---------------------------------------------------------------------------
class Accident(Component):
    """Collision that physically blocks lanes; responders attend; scene is cleared.

    The wrecks are real SUMO vehicles stopped in the blocked lanes. When every car lane is blocked the street is also
    closed to civilian traffic (emergency vehicles may still enter) so that trips are planned around it and nobody
    starts or ends a trip inside the closure.
    """

    name = "accident"
    GAP_M = 1.0  # beyond the follower's minimum gap, between parked responders
    VERIFY_FOR_S = 30.0
    CLOSED_MAX_S = 190.0
    CLOSED_HARD_S = 235.0
    SCENE_HOLD_S = 25.0  # units stay at least this long once the last one has arrived

    def __init__(self, location: str = "random", severity: str = "random", responders: bool = True, window: tuple[float, float] = (0.1, 0.3)):
        self.location = location
        self.sev_req = severity
        self.responders = responders
        self.window = window

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        # wrecks plus up to two responding units parked behind them need a block of ~60 m (short downtown blocks are skipped)
        self.edge = pick_edge(rng, net, sc, self.location, min_length=62.0)
        self.severity = self.sev_req if self.sev_req != "random" else rng.choices(list(SEVERITIES), weights=[0.45, 0.35, 0.2])[0]
        cfg = SEVERITIES[self.severity]
        car = [net.lanes[l].index for l in net.car_lanes(self.edge)]
        if cfg["lanes"] == "one" or len(car) == 1:
            n = 1
        elif cfg["lanes"] == "most":
            n = max(1, len(car) - 1)
        else:
            n = len(car)
        start_idx = rng.randint(0, len(car) - n)
        self.lanes = car[start_idx:start_idx + n]
        self.car_lanes = car
        L = net.edges[self.edge].length
        self.pos = rng.uniform(0.3, 0.7) * max(30.0, L - 30)
        # short downtown blocks: leave room behind the wreck(s) for the responding units to park nose-to-tail
        room = 22.0 + (6.5 if self.severity == "severe" else 0.0) + (12.0 if self.severity != "minor" else 0.0)
        self.pos = min(max(self.pos, room), max(room, L - 14.0)) if L > room + 14.0 else self.pos
        # a crash late in a short run would never clear: start early enough that 150 s remain before 85 % of the run
        self.t_start = min(D * rng.uniform(*self.window), max(0.05 * D, 0.85 * D - 150.0))
        # clearance is drawn in real-world minutes, then scaled to the run so the whole story (crash, response,
        # clearing, recovery) fits a short demonstration: it always ends with at least 15 % of the run left to watch
        # the queue discharge
        scale = min(1.0, max(0.25, D / 1800.0))
        room = 0.85 * D - self.t_start
        self.response_delay = rng.uniform(10, 25)
        # the scene is never cleared before the units that were sent have had time to arrive and work on it
        stations = [poi_edges(net, k) for k in (("police",) if self.severity == "minor" else ("police", "hospital"))]
        eta = max((estimate_travel(net, st[0], self.edge) for st in stations if st), default=0.0)
        needed = self.response_delay + 2.4 * eta + self.SCENE_HOLD_S  # observed arrivals: 1.5-2.3x free flow
        self.clearance = min(max(60 * rng.uniform(*cfg["clear"]) * scale, needed), room)
        # hard stop for waiting on late responders
        self.hard_clear = min(self.clearance + 90.0, room)
        if len(self.lanes) >= len(car):
            # vehicles that were already inside a closed block wait for it to reopen, and SUMO teleports a vehicle that
            # has waited 240 s: a full closure is kept just under that
            self.clearance = min(self.clearance, max(self.CLOSED_MAX_S, needed))
            self.hard_clear = min(self.hard_clear, max(self.clearance, self.CLOSED_HARD_S))
        self.t_clear = self.t_start + self.clearance
        self.limit = cfg["limit"]
        self.desc = net.describe_edge(self.edge)
        self.closed = len(self.lanes) >= len(car)
        self.inc = None
        self.peak_queue = 0
        self._responders: list[str] = []
        self._stops: dict[str, tuple[float, int, float]] = {}
        self._orig_disallowed: dict[str, list[str]] = {}
        self._verified = False
        self._created_at = 0.0
        self._last_park = -1e9
        self._last_arrival = -1e9
        self.obstructed: bool | None = None
        self.guard = ResponderGuard()
        self.uid = f"{self.edge}"

    def reserved_edges(self) -> set[str]:
        return {self.edge}

    def briefing(self) -> list[BriefingItem]:
        car = len([1 for _ in self.lanes])
        what = "all lanes closed" if self.closed else f"{car} lane{'s' if car > 1 else ''} blocked"
        return [
            BriefingItem(label="Collision", value=f"{SEVERITIES[self.severity]['label']} · {what}", detail=self.desc),
            BriefingItem(label="Timing", value=f"{fmt_clock(self.t_start)} → cleared {fmt_clock(self.t_clear)}", detail=f"{self.clearance / 60:.0f} min on scene"),
        ]

    def markers(self) -> list[Marker]:
        return [Marker(t=self.t_start, label="Collision", kind="incident"), Marker(t=self.t_clear, label="Scene cleared", kind="incident")]

    def overlays(self) -> list[Overlay]:
        what = "all lanes closed" if self.closed else f"{len(self.lanes)} lane{'s' if len(self.lanes) > 1 else ''} blocked"
        return [Overlay(id=f"accident_{self.uid}", kind="point", tone="incident", label=f"{SEVERITIES[self.severity]['label']} collision",
                        edges=[self.edge], pos=round(self.pos + 5.0, 1), tStart=round(self.t_start, 1), tEnd=round(self.t_clear, 1),
                        detail=f"{self.desc} · {what}")]

    def on_step(self, eng: "Engine", t: float) -> None:
        if self.inc is None and t >= self.t_start:
            lanes_txt = "all lanes closed" if self.closed else f"{len(self.lanes)} lane{'s' if len(self.lanes) > 1 else ''} blocked"
            self.inc = eng.create_incident("accident", self.edge, self.lanes, self.pos, 10.0, self.clearance, self.severity, self.limit,
                                           description=f"{SEVERITIES[self.severity]['label']} collision, {lanes_txt}")
            self._created_at = t
            eng.emit("incident", f"Collision on {self.desc}: {lanes_txt}", "critical", edge=self.edge)
            # drivers who know about a closed street re-plan; a partial blockage only moves some of them
            eng.reroute_around(self.edge, share=1.0 if self.closed else 0.25)
        if self.inc and not self.inc.cleared:
            if not self._verified and t >= self._created_at + 1.0:
                self._verify(eng, t)
            if self.responders and not self._responders and t >= self.t_start + self.response_delay:
                self._dispatch(eng)
            if self._responders:
                self._manage_responders(eng, t)
            self.peak_queue = max(self.peak_queue, queue_at(eng, self.edge))
            if t >= self.t_clear and self._may_clear(eng, t):
                eng.clear_incident(self.inc.id)
                self._reopen(eng)
                eng.emit("incident", f"Scene cleared on {self.desc}; lanes reopened", "success", edge=self.edge)
                for vid in self._responders:
                    eng.release_stop(vid)
        elif self._responders:  # units driving home after the scene is cleared
            self.guard.step(eng, t, {v: self.edge for v in self._responders})

    def _may_clear(self, eng: "Engine", t: float) -> bool:
        """Clearing waits for every dispatched unit to be on scene (plus a short hold), up to ``hard_clear``."""
        if t - self.t_start >= self.hard_clear or not self._responders:
            return True
        if any(eng.tracked[v].status == "en route" for v in self._responders if v in eng.tracked):
            return False
        return t >= self._last_arrival + self.SCENE_HOLD_S

    # -- the scene ---------------------------------------------------------
    def _verify(self, eng: "Engine", t: float) -> None:
        """The crash only counts once its wrecks stand in the blocked lanes (insertion can be delayed by traffic)."""
        T = eng.T
        live = set(T.vehicle.getIDList())
        placed = [w for w in self.inc.blockers if w in live]
        if len(placed) < len(self.inc.blockers):
            if t - self._created_at > self.VERIFY_FOR_S:
                self._verified, self.obstructed = True, False
                eng.emit("incident", f"Collision on {self.desc}: wrecks could not be placed", "warning", edge=self.edge)
            return
        lanes = {T.vehicle.getLaneIndex(w) for w in placed}
        self._verified, self.obstructed = True, lanes >= set(self.lanes)
        if self.closed:
            for li in self.car_lanes:
                lid = f"{self.edge}_{li}"
                self._orig_disallowed[lid] = list(T.lane.getDisallowed(lid))
                T.lane.setDisallowed(lid, CIVILIAN_CLASSES)
            eng.invalidate_routes()
            divert_from_closure(eng, self.edge)

    def _reopen(self, eng: "Engine") -> None:
        for lid, dis in self._orig_disallowed.items():
            eng.T.lane.setDisallowed(lid, dis)
        self._orig_disallowed.clear()
        eng.invalidate_routes()

    # -- responders --------------------------------------------------------
    def _dispatch(self, eng: "Engine") -> None:
        lane = self.lanes[0]
        # the wreck chain ends at the scene mark (engine: wreck fronts at pos+10, a second one 6.5 m behind when severe);
        # responders stop one vehicle gap short of the rearmost wreck's tail
        tail = self.pos + 10.0 - (6.5 if self.severity == "severe" else 0.0) - 4.75
        stop_pos = max(8.0, tail - 6.5)
        stations = [("police", "police", "Police unit")]
        if self.severity != "minor":
            stations.append(("hospital", "ambulance", "Paramedics"))
        for k, (poi, vtype, label) in enumerate(stations):
            home = poi_edges(eng.net, poi)
            if not home:
                continue
            vid = f"resp{k}_{self.uid}"
            dur = max(30.0, self.t_clear - eng.t + 5)
            ok = eng.spawn_via(vid, [home[0], self.edge, home[0]], vtype, kind="responder", label=f"{label} → collision",
                               stops=[(self.edge, stop_pos, lane, dur)], data={"priority": True})
            if ok:
                self._responders.append(vid)
                self._stops[vid] = (stop_pos, lane, dur)
                eng.emit("emergency", f"{label} dispatched to the collision on {eng.net.edges[self.edge].name}", "info", vehicle=vid)

    def _manage_responders(self, eng: "Engine", t: float) -> None:
        T = eng.T
        for vid in self._responders:
            tr = eng.tracked.get(vid)
            if tr and tr.status == "en route" and vid in eng.vehicles and eng.is_stopped_at(vid, self.edge):
                tr.status = "on scene"
                tr.data["responseTime"] = round(t - self.t_start, 1)
                self._last_arrival = t
                eng.emit("emergency", f"{tr.label.split(' →')[0]} on scene after {fmt_clock(t - self.t_start)}", "success", vehicle=vid)
        self.guard.step(eng, t, {v: self.edge for v in self._responders})
        if t - self._last_park >= 1.0:
            self._last_park = t
            self._queue_behind_parked(eng, T)

    def _queue_behind_parked(self, eng: "Engine", T: Any) -> None:
        """Units park nose-to-tail in the blocked lane; whoever arrives later stops behind whoever is already there."""
        parked = [v for v in self._responders if eng.is_stopped_at(v, self.edge)]
        if not parked:
            return
        tail = min(T.vehicle.getLanePosition(v) - T.vehicle.getLength(v) for v in parked)
        for vid in self._responders:
            if vid in parked or vid not in eng.vehicles:
                continue
            try:
                limit = tail - T.vehicle.getMinGap(vid) - self.GAP_M
                stops = T.vehicle.getStops(vid, 1)
                if not stops or stops[0].lane.rsplit("_", 1)[0] != self.edge:
                    continue
                if stops[0].endPos > limit and limit > 6.0 and T.vehicle.getLanePosition(vid) < limit:
                    lane = int(stops[0].lane.rsplit("_", 1)[1])  # keep whichever lane the unit is working towards
                    T.vehicle.replaceStop(vid, 0, self.edge, pos=limit, laneIndex=lane, duration=max(30.0, self._stops[vid][2]))
            except Exception as e:  # noqa: BLE001
                log.debug("parking order failed for %s: %s", vid, e)

    def on_arrival(self, eng, tracked) -> None:
        if tracked.id in self._responders:
            tracked.status = "back in service"

    def status(self, eng) -> dict[str, Any]:
        return {"accidentQueue": self.peak_queue}

    def results(self, eng) -> dict[str, Any]:
        on_scene = [tr.data["responseTime"] for v in self._responders if (tr := eng.tracked.get(v)) and "responseTime" in tr.data]
        return {"accident": {"edge": self.edge, "description": self.desc, "severity": self.severity, "lanes": self.lanes,
                             "start": round(self.t_start, 1), "clearance": round(self.clearance, 1), "peakQueue": self.peak_queue,
                             "obstructed": self.obstructed, "closed": self.closed, "cleared": bool(self.inc and self.inc.cleared),
                             "responders": len(self._responders), "firstResponse": min(on_scene) if on_scene else None,
                             "responderReroutes": self.guard.reroutes, "responderRescues": self.guard.rescues}}


class Construction(Component):
    """Work zone: lane closure (merge) or full closure (signed detour) with a reduced limit."""

    name = "construction"

    def __init__(self, location: str = "random", closure: str = "random", limit_kmh: float = 30.0, at_start: bool = True):
        self.location = location
        self.closure_req = closure
        self.limit = limit_kmh / 3.6
        self.at_start = at_start

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        closure = self.closure_req if self.closure_req != "random" else rng.choice(["lane", "full"])
        min_lanes = 2 if closure == "lane" else 1
        try:
            self.edge = pick_edge(rng, net, sc, self.location, min_lanes)
        except ValueError:
            closure = "full"
            self.edge = pick_edge(rng, net, sc, self.location, 1)
        self.closure = closure
        car = [net.lanes[l].index for l in net.car_lanes(self.edge)]
        self.lanes = car if closure == "full" else [rng.choice([car[0], car[-1]] if len(car) > 1 else car)]
        self.t_start = 0.0 if self.at_start else D * rng.uniform(0.1, 0.4)
        self.desc = net.describe_edge(self.edge)
        self.inc = None
        self.peak_queue = 0

    def reserved_edges(self) -> set[str]:
        return {self.edge}

    def briefing(self) -> list[BriefingItem]:
        what = "Full closure with detour" if self.closure == "full" else ("Curb lane closed" if self.lanes[0] == 1 else "Inside lane closed")
        return [BriefingItem(label="Work zone", value=what, detail=f"{self.desc}, {self.limit * 3.6:.0f} km/h")]

    def markers(self) -> list[Marker]:
        return [Marker(t=self.t_start, label="Work zone active", kind="incident")]

    def overlays(self) -> list[Overlay]:
        return [Overlay(id=f"work_{self.edge}", kind="zone", tone="incident", label="Work zone" + (" · road closed" if self.closure == "full" else " · lane closed"),
                        edges=[self.edge], tStart=round(self.t_start, 1), detail=self.desc)]

    def _install(self, eng: "Engine") -> None:
        L = eng.net.edges[self.edge].length
        self.inc = eng.create_incident("construction", self.edge, self.lanes, 2.0, L - 4.0, None, "moderate", self.limit,
                                       description="Full closure, detour" if self.closure == "full" else "Lane closed")
        if self.closure == "full":
            eng.reroute_around(self.edge, share=1.0)
            divert_from_closure(eng, self.edge)

    def on_start(self, eng: "Engine") -> None:
        if self.at_start:
            self._install(eng)

    def on_step(self, eng: "Engine", t: float) -> None:
        if self.inc is None and t >= self.t_start:
            self._install(eng)
        if self.inc is not None and t >= 0 and not getattr(self, "_said", False):
            self._said = True
            eng.emit("incident", f"Work zone on {self.desc}: {'road closed, detour in effect' if self.closure == 'full' else 'one lane closed'}", "warning", edge=self.edge)
        if self.inc:
            self.peak_queue = max(self.peak_queue, queue_at(eng, self.edge))

    def results(self, eng) -> dict[str, Any]:
        return {"construction": {"edge": self.edge, "description": self.desc, "closure": self.closure, "peakQueue": self.peak_queue}}


# ---------------------------------------------------------------------------
# Emergency services
# ---------------------------------------------------------------------------
class EmergencyDispatch(Component):
    """Ambulance calls: hospital → scene (on-scene time) → hospital."""

    name = "ems"
    instantiate_order = 0
    RESPONSE_MARGIN_S = 190.0  # latest call leaves this much of the run for the ambulance to arrive

    def __init__(self, calls: int, window: tuple[float, float] = (0.06, 0.6)):
        self.n = calls
        self.window = window

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        self.base = poi_edges(net, "hospital")
        if not self.base:
            raise ValueError("network has no hospital")
        hosp = net.pois.get("hospital")
        self.hospital = hosp.name if hosp else "Hospital"
        latest = max(0.1 * D, D - self.RESPONSE_MARGIN_S)
        times = sorted(min(D * rng.uniform(*self.window), latest) for _ in range(self.n))
        if times:
            times[0] = min(times[0], D * 0.12)
        cands = [e for e in net.block_edges(1) if e not in self.base and e not in sc.reserved and e not in sc.protected]
        hx, hy = net.node_pos[net.edges[self.base[0]].to]
        self.calls = []
        used: set[str] = set()
        for k, t in enumerate(times):
            fresh = [e for e in cands if e not in used]  # one scene per street: two ambulances never share a block
            far = [e for e in fresh if math.dist(net.node_pos[net.edges[e].to], (hx, hy)) > 250]
            e = rng.choice(far or fresh or cands)
            used.update(net.street_segment(e))
            L = net.edges[e].length
            self.calls.append({"k": k + 1, "t": t, "edge": e, "pos": scene_stop_pos(rng, L), "onScene": rng.uniform(90, 180),
                               "where": net.describe_edge(e), "vid": f"amb{k + 1}", "dispatched": False, "arrived": None, "ff": None,
                               "failed": False})
        self.guard = ResponderGuard()

    def reserved_edges(self) -> set[str]:
        return {c["edge"] for c in self.calls}

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label=f"Call {c['k']}", value=f"{fmt_clock(c['t'])} · {c['where']}", detail=f"from {self.hospital}") for c in self.calls]

    def markers(self) -> list[Marker]:
        return [Marker(t=c["t"], label=f"EMS call {c['k']}", kind="emergency") for c in self.calls]

    def overlays(self) -> list[Overlay]:
        return [Overlay(id=f"ems{c['k']}", kind="point", tone="emergency", label=f"EMS call {c['k']}", edges=[c["edge"]], pos=round(c["pos"], 1),
                        tStart=round(c["t"], 1), tEnd=round(c["t"] + c["onScene"] + 240.0, 1), detail=c["where"]) for c in self.calls]

    def on_step(self, eng: "Engine", t: float) -> None:
        for c in self.calls:
            if not c["dispatched"] and t >= c["t"]:
                c["dispatched"] = True
                ok = eng.spawn_via(c["vid"], [self.base[0], c["edge"], self.base[0]], "ambulance", kind="ambulance", label=f"Ambulance {c['k']}",
                                   stops=[(c["edge"], c["pos"], curb_lane(eng.net, c["edge"]), c["onScene"])], data={"call": c["k"], "priority": True})
                if ok:
                    out = eng.route_edges(self.base[0], c["edge"], "ambulance")
                    c["ff"] = round(free_flow_time(eng.net, out[0], 1.25), 1) if out else None
                    c["routeM"] = round(out[1]) if out else None
                    eng.emit("emergency", f"Ambulance {c['k']} dispatched from {self.hospital} to {c['where']} ({c['routeM']} m, free-flow {fmt_clock(c['ff'] or 0)})", "critical", vehicle=c["vid"])
                else:
                    c["failed"] = True
                    eng.emit("emergency", f"Ambulance {c['k']}: no route to {c['where']}", "warning")
            if c["dispatched"] and c["arrived"] is None and c["vid"] in eng.vehicles:
                if eng.is_stopped_at(c["vid"], c["edge"]):
                    c["arrived"] = t
                    resp = t - c["t"]
                    tr = eng.tracked.get(c["vid"])
                    if tr:
                        tr.status = "on scene"
                        tr.data["responseTime"] = round(resp, 1)
                        tr.data["freeFlow"] = c["ff"]
                    eng.emit("emergency", f"Ambulance {c['k']} on scene after {fmt_clock(resp)} (free-flow {fmt_clock(c['ff'] or 0)})", "success", vehicle=c["vid"])
            if c["arrived"] is not None and c["vid"] in eng.tracked and eng.tracked[c["vid"]].status == "on scene" and not eng.is_stopped_at(c["vid"], c["edge"]):
                eng.tracked[c["vid"]].status = "returning"
                eng.tracked[c["vid"]].data["priority"] = False
        self.guard.step(eng, t, {c["vid"]: c["edge"] for c in self.calls if c["dispatched"] and not c["failed"]})

    def on_arrival(self, eng, tracked) -> None:
        if tracked.kind == "ambulance" and any(c["vid"] == tracked.id for c in self.calls):
            tracked.status = "back at hospital"

    def results(self, eng) -> dict[str, Any]:
        rows = [{"call": c["k"], "where": c["where"], "dispatched": round(c["t"], 1), "responseTime": None if c["arrived"] is None else round(c["arrived"] - c["t"], 1),
                 "freeFlow": c["ff"], "failed": c["failed"], "edge": c["edge"]} for c in self.calls]
        done = [r for r in rows if r["responseTime"] is not None]
        return {"ems": rows, "meanResponse": round(sum(r["responseTime"] for r in done) / len(done), 1) if done else None,
                "emsGuard": {"reroutes": self.guard.reroutes, "rescues": self.guard.rescues, "stopFixes": self.guard.stop_fixes}}

    def status(self, eng) -> dict[str, Any]:
        return {"ems": [{"call": c["k"], "state": "pending" if not c["dispatched"] else ("on scene" if c["arrived"] is not None else "en route")} for c in self.calls]}


class Pursuit(Component):
    """Simplified pursuit: an evading suspect re-targets erratically; units chase its position."""

    name = "pursuit"
    instantiate_order = 0
    CONTACT_M = 22.0

    def __init__(self, units: int, max_minutes: float):
        self.units = units
        self.max_s = max_minutes * 60

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        self.rng = rng
        self.t0 = D * rng.uniform(0.04, 0.1)
        cands = [e for e in net.block_edges(1) if e not in sc.reserved and e not in sc.protected]
        self.station = poi_edges(net, "police")
        # the theft is reported away from the division, so the first unit has to give chase
        hq = net.node_pos[net.edges[self.station[0]].to] if self.station else None
        away = [e for e in cands if hq is None or math.dist(net.node_pos[net.edges[e].to], hq) > 250]
        self.origin = rng.choice(away or cands)
        self.cands = cands
        self.retarget_every = rng.uniform(20, 35)
        self.unit_delays = sorted(rng.uniform(8, 40) for _ in range(self.units))
        self.unit_origins = [self.station[0] if k == 0 and self.station else rng.choice(cands) for k in range(self.units)]
        self.state = "pending"
        self.contact_since: float | None = None
        self.stopped_at: float | None = None
        self.next_retarget = 0.0
        self.spawned_units: list[str] = []
        self.where = net.describe_edge(self.origin)
        self.min_dist: float | None = None
        self.guard = ResponderGuard()
        self.retarget_failures = 0
        self.unit_failures: set[str] = set()

    def reserved_edges(self) -> set[str]:
        return {self.origin}

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label="Stolen vehicle", value=f"Reported {fmt_clock(self.t0)}", detail=self.where),
                BriefingItem(label="Response", value=f"{self.units} unit{'s' if self.units > 1 else ''}", detail="first unit from 52 Division, others on patrol")]

    def markers(self) -> list[Marker]:
        return [Marker(t=self.t0, label="Pursuit begins", kind="emergency")]

    def overlays(self) -> list[Overlay]:
        return [Overlay(id="pursuit", kind="point", tone="emergency", label="Stolen vehicle reported", edges=[self.origin],
                        tStart=round(self.t0, 1), tEnd=round(self.t0 + 60.0, 1), detail=self.where)]

    def _far_target(self, eng: "Engine", from_xy: tuple[float, float]) -> str:
        far = [e for e in self.cands if math.dist(eng.net.node_pos[eng.net.edges[e].to], from_xy) > 280]
        return self.rng.choice(far or self.cands)

    def on_step(self, eng: "Engine", t: float) -> None:
        T = eng.T
        if self.spawned_units:  # units keep being watched while they drive back after the pursuit
            self.guard.step(eng, t, {v: None for v in self.spawned_units})
        if self.state == "pending" and t >= self.t0:
            target = self._far_target(eng, eng.net.node_pos[eng.net.edges[self.origin].to])
            if eng.spawn("suspect", self.origin, target, "suspect", kind="suspect", label="Suspect vehicle", depart_pos="random_free", depart_speed="0", data={"priority": False}):
                self.state = "fleeing"
                self.next_retarget = t + self.retarget_every
                eng.emit("emergency", f"Stolen vehicle fleeing from {self.where}; units responding", "critical", vehicle="suspect")
            return
        if self.state not in ("fleeing", "stopped"):
            return
        sus = eng.vehicles.get("suspect")
        if sus is None:
            if self.state == "fleeing" and "suspect" in eng.tracked and eng.tracked["suspect"].arrived is not None:
                self.state = "escaped"
                eng.emit("emergency", "Suspect left the district; pursuit ended", "warning")
            return
        # dispatch units
        for k, delay in enumerate(self.unit_delays):
            vid = f"police{k + 1}"
            if vid not in self.spawned_units and t >= self.t0 + delay:
                road = T.vehicle.getRoadID("suspect")
                dest = road if road in eng.net.edges else self.origin
                if self._spawn_unit(eng, k, vid, dest):
                    self.spawned_units.append(vid)
                    eng.emit("emergency", f"Unit {k + 1} joining the pursuit", "info", vehicle=vid)
        if self.state == "fleeing":
            if t >= self.next_retarget:
                self.next_retarget = t + self.retarget_every * self.rng.uniform(0.7, 1.3)
                try:
                    T.vehicle.changeTarget("suspect", self._far_target(eng, (sus[0], sus[1])))
                except Exception:  # noqa: BLE001
                    self.retarget_failures += 1
            # units chase the suspect's current or next edge
            sroute = T.vehicle.getRoute("suspect")
            sidx = T.vehicle.getRouteIndex("suspect")
            ahead = [e for e in sroute[sidx:sidx + 3] if e in eng.net.edges]
            best = None
            for vid in self.spawned_units:
                pv = eng.vehicles.get(vid)
                if pv is None:
                    continue
                d = math.dist((pv[0], pv[1]), (sus[0], sus[1]))
                best = d if best is None else min(best, d)
                if int(t * 4) % 16 == 0 and ahead:
                    proad = T.vehicle.getRoadID(vid)
                    tgt = ahead[1] if len(ahead) > 1 and proad == ahead[0] else ahead[0]
                    if proad != tgt:
                        try:
                            T.vehicle.changeTarget(vid, tgt)
                        except Exception:  # noqa: BLE001
                            self.retarget_failures += 1
            if best is not None:
                self.min_dist = best if self.min_dist is None else min(self.min_dist, best)
            slow = sus[3] < 1.0
            if best is not None and (best < self.CONTACT_M or (slow and best < 40)):
                self.contact_since = self.contact_since if self.contact_since is not None else t
                if t - self.contact_since >= 6.0:
                    self.state = "stopped"
                    self.stopped_at = t
                    T.vehicle.slowDown("suspect", 0.0, 4.0)
                    T.vehicle.setSpeed("suspect", 0.0)
                    for vid in self.spawned_units:
                        if vid in eng.vehicles:
                            pv = eng.vehicles[vid]
                            if math.dist((pv[0], pv[1]), (sus[0], sus[1])) < 60:
                                T.vehicle.setSpeed(vid, 0.0)
                    eng.tracked["suspect"].status = "stopped"
                    road = T.vehicle.getRoadID("suspect")
                    eng.emit("emergency", f"Suspect stopped on {eng.net.describe_edge(road) if road in eng.net.edges else 'an intersection'} after {fmt_clock(t - self.t0)}", "success", vehicle="suspect")
            else:
                self.contact_since = None
            if t - self.t0 > self.max_s and self.state == "fleeing":
                self.state = "called off"
                eng.emit("emergency", "Pursuit called off for public safety; units disengaging", "warning")
                self._release(eng)
        elif self.state == "stopped" and self.stopped_at is not None and t - self.stopped_at > 45:
            self.state = "arrested"
            eng.remove_vehicle("suspect")
            eng.tracked["suspect"].status = "in custody"
            eng.emit("emergency", "Suspect in custody; units returning to service", "success")
            self._release(eng)

    def _spawn_unit(self, eng: "Engine", k: int, vid: str, dest: str) -> bool:
        """Insert unit ``k``; a patrol car whose start has no usable route re-starts from another street."""
        origins = [self.unit_origins[k]] + ([self.rng.choice(self.cands) for _ in range(3)] if k else [])
        for o in origins:
            if o != dest and eng.spawn(vid, o, dest, "police", kind="police", label=f"Unit {k + 1}", depart_pos="random_free" if k else "base", data={"priority": True}):
                return True
            self.unit_failures.add(vid)
        return eng.spawn_via(vid, [origins[0], dest], "police", kind="police", label=f"Unit {k + 1}", data={"priority": True})

    def on_arrival(self, eng, tracked) -> None:
        if tracked.id in self.spawned_units:
            tracked.status = "back in service"

    def _release(self, eng: "Engine") -> None:
        for vid in self.spawned_units:
            if vid in eng.vehicles:
                try:
                    eng.T.vehicle.setSpeed(vid, -1)
                    if self.station:
                        eng.T.vehicle.changeTarget(vid, self.station[0])
                    eng.tracked[vid].data["priority"] = False
                    eng.tracked[vid].status = "returning"
                except Exception:  # noqa: BLE001
                    pass

    def status(self, eng) -> dict[str, Any]:
        return {"pursuit": self.state}

    def results(self, eng) -> dict[str, Any]:
        return {"pursuit": {"outcome": self.state, "duration": None if self.stopped_at is None else round(self.stopped_at - self.t0, 1),
                            "closestApproach": None if self.min_dist is None else round(self.min_dist, 1), "units": len(self.spawned_units),
                            "unitsRequested": self.units, "rescues": self.guard.rescues, "routingFailures": self.retarget_failures + len(self.unit_failures - set(self.spawned_units))}}


# ---------------------------------------------------------------------------
# School, transit, weather, bikes
# ---------------------------------------------------------------------------
class SchoolDismissal(Component):
    name = "school"

    def __init__(self, pickups: int, zone_kmh: float):
        self.pickups = pickups
        self.zone = zone_kmh / 3.6

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        self.edges = zone_edges_of_poi(net, "school")
        if not self.edges:
            raise ValueError("network has no school")
        self.poi = next(p for p in net.pois.values() if p.kind == "school")
        self.bell_t = D * rng.uniform(0.15, 0.3)
        n = int(self.pickups * rng.uniform(0.85, 1.15))
        self.arrivals = sorted(self.bell_t + rng.gauss(60, 150) for _ in range(n))
        self.rng = rng
        self.spawned = 0
        self.placed = 0
        self.failed = 0
        self.fringe = net.entry_edges
        self.zone_set = set(self.edges)
        self.stop_edges = [e for e in self.edges if net.edges[e].length > 40]

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label="Dismissal bell", value=fmt_clock(self.bell_t), detail=self.poi.name),
                BriefingItem(label="Pick-ups", value=f"{len(self.arrivals)} parent vehicles", detail=f"school zone {self.zone * 3.6:.0f} km/h on {len(self.edges)} frontage segments")]

    def markers(self) -> list[Marker]:
        return [Marker(t=self.bell_t, label="Dismissal bell", kind="demand")]

    def overlays(self) -> list[Overlay]:
        return [Overlay(id="school_zone", kind="zone", tone="demand", label=f"School zone · {self.zone * 3.6:.0f} km/h", edges=self.edges,
                        tStart=0.0, detail=self.poi.name),
                Overlay(id="school_bell", kind="point", tone="demand", label="Dismissal", edges=[self.stop_edges[0] if self.stop_edges else self.edges[0]],
                        tStart=round(self.bell_t, 1), tEnd=round(self.bell_t + 600.0, 1), detail=self.poi.name)]

    def on_start(self, eng: "Engine") -> None:
        for e in self.edges:
            for seg in eng.net.street_segment(e):
                eng.T.edge.setMaxSpeed(seg, min(self.zone, eng.net.edges[seg].speed))
        eng.invalidate_routes()

    def modify_demand(self, t: float, p: DemandProfile) -> None:
        b = bell(t, self.bell_t - 30, self.bell_t + 30, self.bell_t + 240, self.bell_t + 600)
        if b > 0:
            p.ped_rate += 3200 * b
            p.ped_generator_edges = self.edges
            p.ped_venue_share = 0.85

    def on_step(self, eng: "Engine", t: float) -> None:
        if t >= self.bell_t and not getattr(self, "_said", False):
            self._said = True
            eng.emit("scenario", f"Dismissal at {self.poi.name}: students leaving, pick-up traffic peaking", "warning")
        while self.spawned < len(self.arrivals) and t >= self.arrivals[self.spawned] - 90:
            k = self.spawned
            self.spawned += 1
            rng = self.rng
            vt = rng.choice(eng.profiles["suv"] + eng.profiles["van"] + eng.profiles["sedan"])
            for _ in range(3):  # a parent whose trip cannot be routed tries another drop-off block
                stop_e = rng.choice(self.stop_edges or self.edges)
                o = rng.choice(self.fringe)
                d = rng.choice(eng.net.exit_edges)
                L = eng.net.edges[stop_e].length
                if eng.spawn_via(f"pickup{k}", [o, stop_e, d], vt, tag="pickup",
                                 stops=[(stop_e, scene_stop_pos(rng, L), curb_lane(eng.net, stop_e), rng.uniform(25, 90))]):
                    self.placed += 1
                    break
            else:
                self.failed += 1

    def results(self, eng) -> dict[str, Any]:
        q = [sum(eng.edge_stats.get(e, (0, 0, 0))[1] for e in self.zone_set)]
        return {"school": {"pickups": self.placed, "failedPickups": self.failed, "frontageQueueNow": q[0]}}


class Transit(Component):
    """Scheduled bus lines with stops and optional transit signal priority."""

    name = "transit"

    def __init__(self, headway_min: float, lines: str):
        self.headway = headway_min * 60
        self.which = lines

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        self.lines = []
        stops_by_edge: dict[str, list[dict[str, Any]]] = {}
        for s in net.bus_stops:
            stops_by_edge.setdefault(s["edge"], []).append(s)
        for line in net.bus_lines:
            if self.which != "all" and line["id"] != self.which:
                continue
            for direction in ("e", "w", "n", "s"):
                fwd = direction in ("e", "n")
                edges = sorted([e for e in net.edges.values() if e.street == line["street"] and e.dir == direction],
                               key=lambda e: (e.seg if fwd else -e.seg, 0 if e.part != "bay" else 1))
                if not edges:
                    continue
                route = [e.id for e in edges]
                stops = [s for e in route for s in stops_by_edge.get(e, [])]
                heading = {"e": "eastbound", "w": "westbound", "n": "northbound", "s": "southbound"}[direction]
                ff = free_flow_time(net, route, 0.95)
                # no bus leaves too late to finish its trip: observed trips take 1.2-1.6x the timetable estimate
                # (free-flow running, dwell, 18 s per signal), so leave 1.7x plus a margin
                signals = sum(1 for e in route if net.edges[e].to in net.tls_ids)
                last = max(30.0, D - (1.7 * (ff + 25.0 * len(stops) + 18.0 * signals) + 30.0))
                first = rng.uniform(5, max(6.0, min(self.headway * 0.8, last)))
                deps = []
                t = first
                while t <= last:
                    deps.append(t)
                    t += self.headway * rng.uniform(0.85, 1.15)
                self.lines.append({"line": line, "dir": heading, "route": route, "stops": stops, "deps": deps, "next": 0, "ff": ff})
        self.rng = rng
        self.buses: list[dict[str, Any]] = []

    def briefing(self) -> list[BriefingItem]:
        out = []
        for ln in self.lines:
            out.append(BriefingItem(label=f"{ln['line']['name']} {ln['dir']}", value=f"{len(ln['deps'])} trips · {len(ln['stops'])} stops",
                                    detail=f"first departure {fmt_clock(ln['deps'][0]) if ln['deps'] else '—'}"))
        return out

    def overlays(self) -> list[Overlay]:
        return [Overlay(id=f"line{ln['line']['id']}{ln['dir'][0]}", kind="route", tone="transit", label=f"{ln['line']['name']} {ln['dir']}",
                        edges=ln["route"], tStart=0.0, color=ln["line"].get("color")) for ln in self.lines]

    def on_step(self, eng: "Engine", t: float) -> None:
        for ln in self.lines:
            while ln["next"] < len(ln["deps"]) and t >= ln["deps"][ln["next"]]:
                k = ln["next"]
                ln["next"] += 1
                vid = f"bus{ln['line']['id']}{ln['dir'][0]}{k + 1}"
                rid = eng.add_route(ln["route"])
                vtype = self.rng.choice(eng.profiles["bus"])
                eng.add_vehicle(vid, rid, vtype, depart_pos="base", depart_speed="desired", arrival_pos="max", depart_lane="best", tag="bus")
                dwell_total = 0.0
                for s in ln["stops"]:
                    boardings = sum(1 for _ in range(int(self.headway / 60 * 1.2)) if self.rng.random() < 0.6)
                    dwell = 6.0 + 2.4 * boardings
                    dwell_total += dwell
                    try:
                        eng.T.vehicle.setBusStop(vid, s["id"], duration=dwell)
                    except Exception:  # noqa: BLE001
                        pass
                signals = sum(1 for e in ln["route"] if eng.net.edges[e].to in eng.net.tls_ids)
                # timetable: free-flow running + dwell + stop acceleration losses + 18 s mean signal delay per signal
                sched = ln["ff"] + dwell_total + 8.0 * len(ln["stops"]) + 18.0 * signals
                eng.track(vid, "bus", f"{ln['line']['name']} {ln['dir']} · trip {k + 1}", {"scheduled": round(sched, 1), "line": ln["line"]["id"], "color": ln["line"].get("color")})
                self.buses.append({"vid": vid, "sched": sched, "dep": t})

    def on_arrival(self, eng: "Engine", tr: "Tracked") -> None:
        if tr.kind != "bus":
            return
        for b in self.buses:
            if b["vid"] == tr.id:
                b["arr"] = tr.arrived
                travel = (tr.arrived or 0) - (tr.data.get("departed") or b["dep"])
                b["travel"] = travel
                b["delay"] = travel - b["sched"]
                tr.data["travel"] = round(travel, 1)
                tr.data["delay"] = round(b["delay"], 1)
                tr.status = "completed"

    def results(self, eng) -> dict[str, Any]:
        done = [b for b in self.buses if "travel" in b]
        return {"transit": {"trips": len(self.buses), "completed": len(done),
                            "meanTravel": round(sum(b["travel"] for b in done) / len(done), 1) if done else None,
                            "meanDelay": round(sum(b["delay"] for b in done) / len(done), 1) if done else None,
                            "tsp": eng.signals.summary()}}

    def status(self, eng) -> dict[str, Any]:
        done = [b for b in self.buses if "travel" in b]
        return {"busesCompleted": len(done), "busMeanDelay": round(sum(b["delay"] for b in done) / len(done), 1) if done else None}


WEATHER = {
    "clear": {"label": "Clear (reference)", "speed": 1.0, "accel": 1.0, "decel": 1.0, "tau": 1.0, "min_gap": 1.0, "bike": 1.0, "demand": 1.0, "bikes": 1.0, "peds": 1.0},
    "light_rain": {"label": "Light rain", "speed": 0.93, "accel": 0.9, "decel": 0.85, "tau": 1.12, "min_gap": 1.1, "bike": 0.9, "demand": 0.97, "bikes": 0.6, "peds": 0.7},
    "heavy_rain": {"label": "Heavy rain", "speed": 0.86, "accel": 0.8, "decel": 0.72, "tau": 1.3, "min_gap": 1.25, "bike": 0.8, "demand": 0.93, "bikes": 0.3, "peds": 0.45},
    "snow": {"label": "Snow", "speed": 0.76, "accel": 0.6, "decel": 0.55, "tau": 1.5, "min_gap": 1.5, "bike": 0.65, "demand": 0.86, "bikes": 0.12, "peds": 0.5},
    "fog": {"label": "Dense fog", "speed": 0.84, "accel": 0.95, "decel": 0.95, "tau": 1.4, "min_gap": 1.3, "bike": 0.85, "demand": 0.95, "bikes": 0.7, "peds": 0.8},
}


class Weather(Component):
    name = "weather"

    def __init__(self, condition: str):
        self.cond_req = condition

    def instantiate(self, rng, sc, net) -> None:
        self.condition = self.cond_req if self.cond_req != "random" else rng.choice([k for k in WEATHER if k != "clear"])
        base = WEATHER[self.condition]
        j = (lambda v: v) if self.condition == "clear" else (lambda v: v * rng.uniform(0.96, 1.04))  # severity varies between runs
        self.k = {k: (j(v) if isinstance(v, float) else v) for k, v in base.items()}

    def conditions(self, cond: Conditions) -> None:
        k = self.k
        cond.speed, cond.accel, cond.decel, cond.tau, cond.min_gap, cond.bike_speed = k["speed"], k["accel"], k["decel"], k["tau"], k["min_gap"], k["bike"]

    def modify_demand(self, t: float, p: DemandProfile) -> None:
        p.rate *= self.k["demand"]
        p.bike_share *= self.k["bikes"]
        p.ped_rate *= self.k["peds"]

    def briefing(self) -> list[BriefingItem]:
        k = self.k
        return [BriefingItem(label="Conditions", value=k["label"], detail=self._effects())]

    def _effects(self) -> str:
        k = self.k
        return f"desired speed −{(1 - k['speed']) * 100:.0f}%, headway +{(k['tau'] - 1) * 100:.0f}%, braking −{(1 - k['decel']) * 100:.0f}%"

    def overlays(self) -> list[Overlay]:
        return [Overlay(id="weather", kind="weather", tone="environment", label=self.k["label"], condition=self.condition, tStart=0.0, detail=self._effects())]

    def on_step(self, eng, t: float) -> None:
        if t >= 0 and not getattr(self, "_said", False):
            self._said = True
            eng.emit("scenario", f"{self.k['label']}: drivers slow down and keep longer gaps", "info")

    def status(self, eng) -> dict[str, Any]:
        return {"weather": self.condition}

    def results(self, eng) -> dict[str, Any]:
        return {"weather": {"condition": self.condition, **{k: round(v, 3) for k, v in self.k.items() if isinstance(v, float)}}}


class CorridorMonitor(Component):
    """Space-mean speed and flow on one street, by mode, sampled each second."""

    name = "corridor"

    def __init__(self, street: str):
        self.street = street

    def instantiate(self, rng, sc, net) -> None:
        self.edges = [e.id for e in net.edges.values() if e.street == self.street]
        self.name_ = next((net.edges[e].name for e in self.edges), self.street)
        self.acc = {"car": [0.0, 0], "bike": [0.0, 0]}
        self.bike_lane = {"protected": 0, "shared": 0, "invalid": 0}
        self._last = -1.0

    def overlays(self) -> list[Overlay]:
        return [Overlay(id=f"corridor_{self.street}", kind="route", tone="transit", label=f"{self.name_} corridor", edges=self.edges, tStart=0.0, color="#0F766E")]

    def on_step(self, eng: "Engine", t: float) -> None:
        if t < 0 or t - self._last < 1.0:
            return
        self._last = t
        es = set(self.edges)
        T = eng.T
        for e in es:
            st = eng.edge_stats.get(e)
            if not st or not st[0]:
                continue
            for vid in T.edge.getLastStepVehicleIDs(e) if st[0] < 40 else []:
                v = eng.vehicles.get(vid)
                if v is None:
                    continue
                mode = "bike" if eng.vtype.get(vid, "").startswith("bike") else "car"
                self.acc[mode][0] += v[3]
                self.acc[mode][1] += 1
                if mode == "bike":
                    lane = eng.net.lanes.get(T.vehicle.getLaneID(vid))
                    if lane is not None:
                        self.bike_lane["invalid" if not lane.allows_bike else "shared" if lane.allows_car else "protected"] += 1

    def results(self, eng) -> dict[str, Any]:
        n = sum(self.bike_lane.values())
        return {"corridor": {"street": self.name_, **{f"{m}Speed": round(a[0] / a[1] * 3.6, 1) if a[1] else None for m, a in self.acc.items()},
                             "bikeSamples": n, "bikeProtectedShare": round(self.bike_lane["protected"] / n, 3) if n else None,
                             "bikeLaneViolations": self.bike_lane["invalid"]}}

    def status(self, eng) -> dict[str, Any]:
        return {"corridor": {f"{m}Speed": round(a[0] / a[1] * 3.6, 1) if a[1] else None for m, a in self.acc.items()}}
