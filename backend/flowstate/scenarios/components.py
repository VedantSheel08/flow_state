"""Reusable stochastic scenario components.

Each component draws its concrete instance in :meth:`instantiate` from a
dedicated RNG stream (derived from the run seed) and then acts on SUMO only
through the engine: inserting vehicles, changing lane permissions or speed
limits, adding stops, or shaping the demand profile. Outcomes — queues,
response times, detours — are measured, never scripted.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Any

from ..sim.vtypes import Conditions
from .base import BriefingItem, Component, DemandProfile, Marker, bell, fmt_clock

if TYPE_CHECKING:  # pragma: no cover
    from ..network.info import NetInfo
    from ..sim.engine import Engine, Tracked
    from .base import Scenario

CLASS_WEIGHT = {"arterial": 3.0, "arterial_bike": 3.0, "collector": 2.0, "local": 1.0}
SEVERITIES = {
    # lanes blocked: "one" | "most" | "all"; clearance minutes range; speed limit past the scene
    "minor": {"lanes": "one", "clear": (5, 9), "limit": None, "label": "Minor"},
    "moderate": {"lanes": "most", "clear": (8, 14), "limit": 25 / 3.6, "label": "Moderate"},
    "severe": {"lanes": "all", "clear": (12, 20), "limit": 20 / 3.6, "label": "Severe"},
}


def run_length(sc: "Scenario") -> float:
    return sc.duration or 600.0


def pick_edge(rng: random.Random, net: "NetInfo", sc: "Scenario", requested: str = "random", min_lanes: int = 1, classes: tuple[str, ...] = ()) -> str:
    cands = [e for e in net.block_edges(min_lanes, classes) if e not in sc.reserved]
    if requested and requested != "random":
        seg = net.street_segment(requested) if requested in net.edges else []
        main = [e for e in seg if net.edges[e].part != "bay" and e in cands]
        if not main:
            raise ValueError(f"'{requested}' is not a valid mid-block street for this scenario")
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

    def on_step(self, eng, t: float) -> None:
        if t >= self.t0 and not getattr(self, "_said", False):
            self._said = True
            eng.emit("scenario", f"Localized demand spike near {self.where}", "warning")


# ---------------------------------------------------------------------------
# Capacity reductions
# ---------------------------------------------------------------------------
class Accident(Component):
    """Collision that physically blocks lanes; responders attend; scene is cleared."""

    name = "accident"

    def __init__(self, location: str = "random", severity: str = "random", responders: bool = True, window: tuple[float, float] = (0.1, 0.3)):
        self.location = location
        self.sev_req = severity
        self.responders = responders
        self.window = window

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        self.edge = pick_edge(rng, net, sc, self.location)
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
        L = net.edges[self.edge].length
        self.pos = rng.uniform(0.3, 0.7) * max(30.0, L - 30)
        self.t_start = D * rng.uniform(*self.window)
        self.clearance = 60 * rng.uniform(*cfg["clear"])
        self.t_clear = self.t_start + self.clearance
        self.limit = cfg["limit"]
        self.response_delay = rng.uniform(50, 110)
        self.desc = net.describe_edge(self.edge)
        self.closed = len(self.lanes) >= len(car)
        self.inc = None
        self.peak_queue = 0
        self._responders: list[str] = []
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

    def on_step(self, eng: "Engine", t: float) -> None:
        if self.inc is None and t >= self.t_start:
            lanes_txt = "all lanes closed" if self.closed else f"{len(self.lanes)} lane{'s' if len(self.lanes) > 1 else ''} blocked"
            self.inc = eng.create_incident("accident", self.edge, self.lanes, self.pos, 10.0, self.clearance, self.severity, self.limit,
                                           description=f"{SEVERITIES[self.severity]['label']} collision, {lanes_txt}")
            eng.emit("incident", f"Collision on {self.desc}: {lanes_txt}", "critical", edge=self.edge)
            eng.reroute_around(self.edge, share=0.6 if self.closed else 0.25)
        if self.inc and self.responders and not self._responders and t >= self.t_start + self.response_delay:
            self._dispatch(eng)
        for vid in self._responders:
            tr = eng.tracked.get(vid)
            if tr and tr.status == "en route" and vid in eng.vehicles and eng.is_stopped_at(vid, self.edge):
                tr.status = "on scene"
                tr.data["responseTime"] = round(t - self.t_start, 1)
                eng.emit("emergency", f"{tr.label.split(' →')[0]} on scene after {fmt_clock(t - self.t_start)}", "success", vehicle=vid)
        if self.inc and not self.inc.cleared:
            q = eng.edge_stats.get(self.edge, (0, 0, 0))[1]
            for up in eng.net.edges.values():
                if up.to == eng.net.edges[self.edge].frm and up.kind != "exit":
                    q += eng.edge_stats.get(up.id, (0, 0, 0))[1]
            self.peak_queue = max(self.peak_queue, q)
            if t >= self.t_clear:
                eng.clear_incident(self.inc.id)
                eng.emit("incident", f"Scene cleared on {self.desc}; lanes reopened", "success", edge=self.edge)
                for vid in self._responders:
                    eng.release_stop(vid)

    def _dispatch(self, eng: "Engine") -> None:
        lane = self.lanes[0]
        stop_pos = max(6.0, self.pos - 4.0)
        stations = [("police", "police", "Police unit")]
        if self.severity != "minor":
            stations.append(("hospital", "ambulance", "Paramedics"))
        for k, (poi, vtype, label) in enumerate(stations):
            home = poi_edges(eng.net, poi)
            if not home:
                continue
            vid = f"resp{k}_{self.uid}"
            ok = eng.spawn_via(vid, [home[0], self.edge, home[0]], vtype, kind="responder", label=f"{label} → collision",
                               stops=[(self.edge, stop_pos - 9 * k, lane, max(30.0, self.t_clear - eng.t + 5))], data={"priority": True})
            if ok:
                self._responders.append(vid)
                eng.emit("emergency", f"{label} dispatched to the collision on {eng.net.edges[self.edge].name}", "info", vehicle=vid)

    def status(self, eng) -> dict[str, Any]:
        return {"accidentQueue": self.peak_queue}

    def results(self, eng) -> dict[str, Any]:
        return {"accident": {"edge": self.edge, "description": self.desc, "severity": self.severity, "lanes": self.lanes,
                             "start": round(self.t_start, 1), "clearance": round(self.clearance, 1), "peakQueue": self.peak_queue}}


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

    def _install(self, eng: "Engine") -> None:
        L = eng.net.edges[self.edge].length
        self.inc = eng.create_incident("construction", self.edge, self.lanes, 2.0, L - 4.0, None, "moderate", self.limit,
                                       description="Full closure, detour" if self.closure == "full" else "Lane closed")
        if self.closure == "full":
            eng.reroute_around(self.edge, share=1.0)

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
            self.peak_queue = max(self.peak_queue, eng.edge_stats.get(self.edge, (0, 0, 0))[1])

    def results(self, eng) -> dict[str, Any]:
        return {"construction": {"edge": self.edge, "description": self.desc, "closure": self.closure, "peakQueue": self.peak_queue}}


# ---------------------------------------------------------------------------
# Emergency services
# ---------------------------------------------------------------------------
class EmergencyDispatch(Component):
    """Ambulance calls: hospital → scene (on-scene time) → hospital."""

    name = "ems"

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
        times = sorted(D * rng.uniform(*self.window) for _ in range(self.n))
        if times:
            times[0] = min(times[0], D * 0.12)
        cands = [e for e in net.block_edges(1) if e not in self.base and e not in sc.reserved]
        self.calls = []
        for k, t in enumerate(times):
            far = [e for e in cands if math.dist(net.node_pos[net.edges[e].to], net.node_pos[net.edges[self.base[0]].to]) > 250]
            e = rng.choice(far or cands)
            L = net.edges[e].length
            self.calls.append({"k": k + 1, "t": t, "edge": e, "pos": rng.uniform(0.3, 0.75) * L, "onScene": rng.uniform(90, 180),
                               "where": net.describe_edge(e), "vid": f"amb{k + 1}", "dispatched": False, "arrived": None, "ff": None})

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label=f"Call {c['k']}", value=f"{fmt_clock(c['t'])} · {c['where']}", detail=f"from {self.hospital}") for c in self.calls]

    def markers(self) -> list[Marker]:
        return [Marker(t=c["t"], label=f"EMS call {c['k']}", kind="emergency") for c in self.calls]

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

    def results(self, eng) -> dict[str, Any]:
        rows = [{"call": c["k"], "where": c["where"], "dispatched": round(c["t"], 1), "responseTime": None if c["arrived"] is None else round(c["arrived"] - c["t"], 1),
                 "freeFlow": c["ff"]} for c in self.calls]
        done = [r for r in rows if r["responseTime"] is not None]
        return {"ems": rows, "meanResponse": round(sum(r["responseTime"] for r in done) / len(done), 1) if done else None}

    def status(self, eng) -> dict[str, Any]:
        return {"ems": [{"call": c["k"], "state": "pending" if not c["dispatched"] else ("on scene" if c["arrived"] is not None else "en route")} for c in self.calls]}


class Pursuit(Component):
    """Simplified pursuit: an evading suspect re-targets erratically; units chase its position."""

    name = "pursuit"
    CONTACT_M = 22.0

    def __init__(self, units: int, max_minutes: float):
        self.units = units
        self.max_s = max_minutes * 60

    def instantiate(self, rng, sc, net) -> None:
        D = run_length(sc)
        self.rng = rng
        self.t0 = D * rng.uniform(0.04, 0.1)
        cands = [e for e in net.block_edges(1) if e not in sc.reserved]
        self.origin = rng.choice(cands)
        self.cands = cands
        self.station = poi_edges(net, "police")
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

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label="Stolen vehicle", value=f"Reported {fmt_clock(self.t0)}", detail=self.where),
                BriefingItem(label="Response", value=f"{self.units} unit{'s' if self.units > 1 else ''}", detail="first unit from Division 52, others on patrol")]

    def markers(self) -> list[Marker]:
        return [Marker(t=self.t0, label="Pursuit begins", kind="emergency")]

    def _far_target(self, eng: "Engine", from_xy: tuple[float, float]) -> str:
        far = [e for e in self.cands if math.dist(eng.net.node_pos[eng.net.edges[e].to], from_xy) > 280]
        return self.rng.choice(far or self.cands)

    def on_step(self, eng: "Engine", t: float) -> None:
        T = eng.T
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
                if eng.spawn(vid, self.unit_origins[k], dest, "police", kind="police", label=f"Unit {k + 1}", depart_pos="random_free" if k else "base", data={"priority": True}):
                    self.spawned_units.append(vid)
                    eng.emit("emergency", f"Unit {k + 1} joining the pursuit", "info", vehicle=vid)
        if self.state == "fleeing":
            if t >= self.next_retarget:
                self.next_retarget = t + self.retarget_every * self.rng.uniform(0.7, 1.3)
                try:
                    T.vehicle.changeTarget("suspect", self._far_target(eng, (sus[0], sus[1])))
                except Exception:  # noqa: BLE001
                    pass
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
                            pass
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
                            "closestApproach": None if self.min_dist is None else round(self.min_dist, 1), "units": len(self.spawned_units)}}


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
        self.fringe = net.entry_edges
        self.zone_set = set(self.edges)
        self.stop_edges = [e for e in self.edges if net.edges[e].length > 40]

    def briefing(self) -> list[BriefingItem]:
        return [BriefingItem(label="Dismissal bell", value=fmt_clock(self.bell_t), detail=self.poi.name),
                BriefingItem(label="Pick-ups", value=f"{len(self.arrivals)} parent vehicles", detail=f"school zone {self.zone * 3.6:.0f} km/h on {len(self.edges)} frontage segments")]

    def markers(self) -> list[Marker]:
        return [Marker(t=self.bell_t, label="Dismissal bell", kind="demand")]

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
            stop_e = rng.choice(self.stop_edges or self.edges)
            o = rng.choice(self.fringe)
            d = rng.choice(eng.net.exit_edges)
            L = eng.net.edges[stop_e].length
            eng.spawn_via(f"pickup{k}", [o, stop_e, d], rng.choice(eng.profiles["suv"] + eng.profiles["van"] + eng.profiles["sedan"]),
                          tag="pickup", stops=[(stop_e, rng.uniform(0.3, 0.9) * L, curb_lane(eng.net, stop_e), rng.uniform(25, 90))])

    def results(self, eng) -> dict[str, Any]:
        q = [sum(eng.edge_stats.get(e, (0, 0, 0))[1] for e in self.zone_set)]
        return {"school": {"pickups": self.spawned, "frontageQueueNow": q[0]}}


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
                first = rng.uniform(5, self.headway * 0.8)
                deps = []
                t = first
                while t < D - 60:
                    deps.append(t)
                    t += self.headway * rng.uniform(0.85, 1.15)
                heading = {"e": "eastbound", "w": "westbound", "n": "northbound", "s": "southbound"}[direction]
                ff = free_flow_time(net, route, 0.95)
                self.lines.append({"line": line, "dir": heading, "route": route, "stops": stops, "deps": deps, "next": 0, "ff": ff})
        self.rng = rng
        self.buses: list[dict[str, Any]] = []

    def briefing(self) -> list[BriefingItem]:
        out = []
        for ln in self.lines:
            out.append(BriefingItem(label=f"{ln['line']['name']} {ln['dir']}", value=f"{len(ln['deps'])} trips · {len(ln['stops'])} stops",
                                    detail=f"first departure {fmt_clock(ln['deps'][0]) if ln['deps'] else '—'}"))
        return out

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
        return [BriefingItem(label="Conditions", value=k["label"], detail=f"desired speed −{(1 - k['speed']) * 100:.0f}%, headway +{(k['tau'] - 1) * 100:.0f}%, braking −{(1 - k['decel']) * 100:.0f}%")]

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
        self._last = -1.0

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

    def results(self, eng) -> dict[str, Any]:
        return {"corridor": {"street": self.name_, **{f"{m}Speed": round(a[0] / a[1] * 3.6, 1) if a[1] else None for m, a in self.acc.items()}}}

    def status(self, eng) -> dict[str, Any]:
        return {"corridor": {f"{m}Speed": round(a[0] / a[1] * 3.6, 1) if a[1] else None for m, a in self.acc.items()}}
