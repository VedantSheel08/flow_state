"""Static network facts used by demand generation, controllers and scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import sumolib

from .. import sumo_env  # noqa: F401

SIDES = ("W", "E", "N", "S")


@dataclass
class LaneInfo:
    id: str
    edge: str
    index: int
    length: float
    speed: float
    width: float
    allows_car: bool
    allows_bike: bool
    allows_ped: bool
    capacity: float


@dataclass
class EdgeInfo:
    id: str
    frm: str
    to: str
    kind: str  # entry | exit | street
    length: float
    speed: float
    lanes: list[str]
    side: str | None = None
    street: str = ""
    dir: str = ""
    seg: int = -1
    name: str = ""
    cls: str = ""
    part: str = "full"  # full | main | bay (left-turn bay section of an arterial block)


@dataclass
class TlsLink:
    index: int
    from_lane: str
    to_lane: str
    direction: str


@dataclass
class Zone:
    id: str
    use: str
    edges: list[str]
    core: bool


@dataclass
class Poi:
    id: str
    kind: str
    name: str
    access: list[str]
    x: float
    y: float


@dataclass
class NetInfo:
    path: Path
    network_id: str
    edges: dict[str, EdgeInfo] = field(default_factory=dict)
    lanes: dict[str, LaneInfo] = field(default_factory=dict)
    tls_links: dict[str, list[TlsLink]] = field(default_factory=dict)
    tls_programs: dict[str, list[tuple[float, str]]] = field(default_factory=dict)
    node_pos: dict[str, tuple[float, float]] = field(default_factory=dict)
    entries: dict[str, list[str]] = field(default_factory=dict)
    exits: dict[str, list[str]] = field(default_factory=dict)
    lane_to_tls: dict[str, str] = field(default_factory=dict)
    lane_from_tls: dict[str, str] = field(default_factory=dict)
    edge_to_tls: dict[str, str] = field(default_factory=dict)
    tls_ids: list[str] = field(default_factory=list)
    junction_names: dict[str, str] = field(default_factory=dict)
    zones: list[Zone] = field(default_factory=list)
    pois: dict[str, Poi] = field(default_factory=dict)
    bus_stops: list[dict[str, Any]] = field(default_factory=list)
    bus_lines: list[dict[str, Any]] = field(default_factory=list)
    has_sidewalks: bool = False
    city: dict[str, Any] = field(default_factory=dict)

    # ---- convenience ----------------------------------------------------
    @property
    def street_edges(self) -> list[str]:
        return [e.id for e in self.edges.values() if e.kind == "street"]

    @property
    def entry_edges(self) -> list[str]:
        return [e for lst in self.entries.values() for e in lst]

    @property
    def exit_edges(self) -> list[str]:
        return [e for lst in self.exits.values() for e in lst]

    def block_edges(self, min_car_lanes: int = 1, classes: tuple[str, ...] = ()) -> list[str]:
        """Mid-block street edges (no turn bays, no fringe stubs)."""
        return [e.id for e in self.edges.values() if e.kind == "street" and e.part != "bay" and len(self.car_lanes(e.id)) >= min_car_lanes
                and (not classes or e.cls in classes) and e.length > 50]

    def car_lanes(self, edge_id: str) -> list[str]:
        return [l for l in self.edges[edge_id].lanes if self.lanes[l].allows_car]

    def edges_of_class(self, *classes: str, kind: str = "street") -> list[str]:
        return [e.id for e in self.edges.values() if e.kind == kind and (not classes or e.cls in classes)]

    def tls_incoming_lanes(self, tls: str) -> list[str]:
        seen: list[str] = []
        for link in self.tls_links.get(tls, []):
            if link.from_lane not in seen:
                seen.append(link.from_lane)
        return seen

    def edge_heading(self, edge_id: str) -> str:
        e = self.edges[edge_id]
        x0, y0 = self.node_pos[e.frm]
        x1, y1 = self.node_pos[e.to]
        if abs(x1 - x0) > abs(y1 - y0):
            return "E" if x1 > x0 else "W"
        return "N" if y1 > y0 else "S"

    def reverse(self, edge_id: str) -> str | None:
        """Opposite-direction edge of the same block (bay/main parts resolved)."""
        e = self.edges[edge_id]
        ends = {e.frm, e.to}
        for cand in self.edges.values():
            if cand.id != edge_id and cand.street == e.street and cand.dir != e.dir and cand.seg == e.seg and cand.part != "bay" and e.street:
                return cand.id
            if cand.frm == e.to and cand.to == e.frm:
                return cand.id
        del ends
        return None

    def street_segment(self, edge_id: str) -> list[str]:
        """All edges (main + bay) forming the same directed block."""
        e = self.edges[edge_id]
        return [c.id for c in self.edges.values() if c.street == e.street and c.dir == e.dir and c.seg == e.seg and e.street] or [edge_id]

    def junction_name(self, node: str) -> str:
        return self.junction_names.get(node, node)

    def describe_edge(self, edge_id: str) -> str:
        """Human description, e.g. 'King Street West eastbound between John Street and Simcoe Street'."""
        e = self.edges.get(edge_id)
        if e is None:
            return edge_id
        heading = {"E": "eastbound", "W": "westbound", "N": "northbound", "S": "southbound"}[self.edge_heading(edge_id)]
        name = e.name or edge_id
        seg = [self.edges[x] for x in self.street_segment(edge_id)]
        frm = next((x.frm for x in seg if x.part != "bay"), e.frm)
        to = next((x.to for x in seg if x.part != "main"), e.to)
        a = self._cross_street(frm, e.street)
        b = self._cross_street(to, e.street)
        if a and b:
            return f"{name} {heading} between {a} and {b}"
        if b:
            return f"{name} {heading} approaching {b}"
        if a:
            return f"{name} {heading} beyond {a}"
        return f"{name} {heading}"

    def _cross_street(self, node: str, street: str) -> str | None:
        jn = self.junction_names.get(node)
        if not jn:
            return None
        parts = [p.strip() for p in jn.split("&")]
        for p in parts:
            if not p.lower().startswith(street.lower()):
                return p
        return parts[-1]


def _classify_legacy(edge_id: str) -> tuple[str, str | None]:
    prefixes = {"left": "W", "right": "E", "top": "N", "bottom": "S"}
    for prefix, side in prefixes.items():
        if edge_id.startswith(prefix):
            return "entry", side
    for prefix, side in prefixes.items():
        if prefix in edge_id:
            return "exit", side
    return "street", None


@lru_cache(maxsize=8)
def load_net_info(net_path: str, network_id: str) -> NetInfo:
    net = sumolib.net.readNet(net_path, withPrograms=True)
    info = NetInfo(path=Path(net_path), network_id=network_id)
    city_path = Path(net_path).with_name(f"{network_id}.city.json")
    meta: dict[str, Any] = {}
    if city_path.exists():
        info.city = json.loads(city_path.read_text(encoding="utf-8"))
        meta = info.city.get("edges", {})
        info.junction_names = {k: v["name"] for k, v in info.city.get("junctions", {}).items()}
        info.zones = [Zone(z["id"], z["use"], z["edges"], z["core"]) for z in info.city.get("zones", [])]
        for p in info.city.get("pois", []):
            info.pois[p["id"]] = Poi(p["id"], p["kind"], p["name"], p.get("access", []), p["x"], p["y"])
        info.bus_stops = info.city.get("busStops", [])
        info.bus_lines = info.city.get("busLines", [])
    for node in net.getNodes():
        info.node_pos[node.getID()] = tuple(node.getCoord())
    for edge in net.getEdges(withInternal=False):
        eid = edge.getID()
        m = meta.get(eid)
        if m:
            kind, side = m["kind"], m["side"]
        else:
            kind, side = _classify_legacy(eid)
        lane_ids = []
        for lane in edge.getLanes():
            car = lane.allows("passenger")
            ped = lane.allows("pedestrian") and not car
            info.has_sidewalks |= ped
            li = LaneInfo(
                id=lane.getID(), edge=eid, index=lane.getIndex(), length=lane.getLength(), speed=lane.getSpeed(),
                width=lane.getWidth(), allows_car=car, allows_bike=lane.allows("bicycle"), allows_ped=ped,
                capacity=max(1.0, lane.getLength() / 7.5),
            )
            info.lanes[li.id] = li
            lane_ids.append(li.id)
        info.edges[eid] = EdgeInfo(
            id=eid, frm=edge.getFromNode().getID(), to=edge.getToNode().getID(), kind=kind,
            length=edge.getLength(), speed=edge.getSpeed(), lanes=lane_ids, side=side,
            street=(m or {}).get("street", ""), name=(m or {}).get("name", edge.getName() or ""), cls=(m or {}).get("cls", ""), part=(m or {}).get("part", "full"), dir=(m or {}).get("dir", ""), seg=(m or {}).get("seg", -1),
        )
        if kind == "entry" and side:
            info.entries.setdefault(side, []).append(eid)
        elif kind == "exit" and side:
            info.exits.setdefault(side, []).append(eid)
    for tls in net.getTrafficLights():
        tid = tls.getID()
        info.tls_ids.append(tid)
        links = []
        for in_lane, out_lane, idx in tls.getConnections():
            if not in_lane.allows("passenger") and not in_lane.allows("bicycle"):
                continue
            direction = "s"
            for c in in_lane.getOutgoing():
                if c.getToLane().getID() == out_lane.getID():
                    direction = c.getDirection()
            links.append(TlsLink(idx, in_lane.getID(), out_lane.getID(), direction))
            info.lane_to_tls[in_lane.getID()] = tid
            info.lane_from_tls[out_lane.getID()] = tid
            info.edge_to_tls[in_lane.getEdge().getID()] = tid
        links.sort(key=lambda l: l.index)
        info.tls_links[tid] = links
        for prog in tls.getPrograms().values():
            info.tls_programs[tid] = [(p.duration, p.state) for p in prog.getPhases()]
            break
    info.tls_ids.sort()
    for side in info.entries:
        info.entries[side].sort()
    for side in info.exits:
        info.exits[side].sort()
    return info
