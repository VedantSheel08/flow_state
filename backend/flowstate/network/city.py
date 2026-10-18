"""Authored street networks for FlowState.

Networks are described declaratively (streets, their cross-section class and
position, special land uses) and compiled with SUMO's ``netconvert`` into a
``.net.xml``. A sidecar ``<id>.city.json`` holds everything SUMO does not model
but the product needs: street names, edge metadata, intersection names, city
blocks, building footprints, points of interest and demand zones.

Cross-sections follow common North-American urban practice:

=============  ======================================================  =========
class          lanes per direction (right -> left)                     speed
=============  ======================================================  =========
arterial       sidewalk 3.0 m, curb lane 3.5 m, lane 3.3 m, painted   50 km/h
               median 3.1 m that opens into a ~35 m left-turn bay
arterial_bike  sidewalk 3.0 m, bike 2.0 m, curb 3.4 m, lane 3.2 m      50 km/h
collector      sidewalk 2.6 m, bike 1.8 m, lane 3.4 m                  40 km/h
local          sidewalk 2.4 m, lane 3.6 m (shared with cyclists)       40 km/h
=============  ======================================================  =========
"""

from __future__ import annotations

import json
import math
import random
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..sim.signals import retime_net
from ..sumo_env import binary

PED = "pedestrian"
BIKE = "bicycle"
MOTOR = "passenger bus truck emergency delivery taxi coach"


@dataclass(frozen=True)
class LaneDef:
    role: str  # sidewalk | bike | car | bay (painted median that opens into a left-turn bay)
    width: float
    allow: str


CLASSES: dict[str, dict[str, Any]] = {
    "arterial": {
        "speed": 13.89,
        "priority": 3,
        "lanes": [LaneDef("sidewalk", 3.0, PED), LaneDef("car", 3.5, MOTOR + " " + BIKE), LaneDef("car", 3.3, MOTOR), LaneDef("bay", 3.1, MOTOR)],
        "exclusive_left": True,
        "bay": 58.0,  # distance of the bay opening from the junction centre
    },
    "arterial_bike": {
        "speed": 13.89,
        "priority": 3,
        "lanes": [LaneDef("sidewalk", 3.0, PED), LaneDef("bike", 2.0, BIKE), LaneDef("car", 3.4, MOTOR), LaneDef("car", 3.2, MOTOR)],
        "exclusive_left": False,
    },
    "collector": {
        "speed": 11.11,
        "priority": 2,
        "lanes": [LaneDef("sidewalk", 2.6, PED), LaneDef("bike", 1.8, BIKE), LaneDef("car", 3.4, MOTOR)],
        "exclusive_left": False,
    },
    "local": {
        "speed": 11.11,
        "priority": 1,
        "lanes": [LaneDef("sidewalk", 2.4, PED), LaneDef("car", 3.6, MOTOR + " " + BIKE)],
        "exclusive_left": False,
    },
}

# One-way cross-sections (right -> left). The opposite curb keeps its sidewalk on a
# pedestrian-only reverse edge, so crossings and walking areas still form on both sides.
ONEWAY_LANES: dict[str, list[LaneDef]] = {
    "arterial": [LaneDef("sidewalk", 2.6, PED), LaneDef("bike", 1.8, BIKE), LaneDef("car", 3.3, MOTOR), LaneDef("car", 3.3, MOTOR), LaneDef("car", 3.2, MOTOR)],
    "collector": [LaneDef("sidewalk", 2.6, PED), LaneDef("bike", 1.8, BIKE), LaneDef("car", 3.4, MOTOR), LaneDef("car", 3.3, MOTOR)],
    "local": [LaneDef("sidewalk", 2.4, PED), LaneDef("car", 3.8, MOTOR + " " + BIKE)],
}

CURB_RADIUS = 7.0
BUILDING_SETBACK = 4.0  # real buildings stand this far behind the back of the sidewalk


def half_width(cls: str, oneway: bool = False) -> float:
    if oneway:
        lanes = ONEWAY_LANES[cls]
        return (sum(l.width for l in lanes) + lanes[0].width) / 2
    return sum(l.width for l in CLASSES[cls]["lanes"])


@dataclass
class Street:
    id: str
    name: str
    cls: str
    pos: float  # x for north-south streets, y for east-west streets
    # cross streets (ids) where the street starts / ends; None runs on to the map edge
    span: tuple[str | None, str | None] = (None, None)
    oneway: str | None = None  # travel direction of a one-way street: e | w | n | s
    renames: tuple[tuple[str, str], ...] = ()  # (cross street id, name from there on, towards increasing x / y)


@dataclass
class Poi:
    id: str
    kind: str  # hospital | police | stadium | school | park | transit
    name: str
    cell: tuple[int, int]  # block cell (i, j) in the extended grid
    entrance: str  # side of the block that faces the entrance: N/E/S/W
    area: tuple[float, float, float, float] | None = None  # part of the block (fractions x0, y0, x1, y1); the rest is built up
    note: str = ""


@dataclass
class NetworkSpec:
    id: str
    title: str
    description: str
    ns: list[Street]
    ew: list[Street]
    fringe: float = 160.0
    pois: list[Poi] = field(default_factory=list)
    core_cells: list[tuple[int, int]] = field(default_factory=list)
    bus_lines: list[dict[str, Any]] = field(default_factory=list)
    seed: int = 7
    controls: dict[tuple[str, str], str] = field(default_factory=dict)  # (ns street, ew street) -> junction type override
    focus: tuple[str, str] | None = None  # (ns street, ew street) of the default camera junction
    no_left: list[tuple[str, str]] = field(default_factory=list)  # (approach street, cross street): left turns prohibited
    source: dict[str, Any] = field(default_factory=dict)
    # real buildings / open spaces / trees ({"buildings", "areas", "trees"}) drawn instead of procedural blocks
    context: dict[str, Any] = field(default_factory=dict)

    def with_class(self, street_id: str, cls: str, new_id: str, title: str, description: str) -> "NetworkSpec":
        def swap(lst: list[Street]) -> list[Street]:
            return [replace(s, cls=cls) if s.id == street_id else s for s in lst]

        return replace(self, id=new_id, title=title, description=description, ns=swap(self.ns), ew=swap(self.ew))


# ---------------------------------------------------------------------------
# Network catalogue
# ---------------------------------------------------------------------------
# The original synthetic 4x4 grid, kept for reference (not built; see toronto.py for the live map).
RIVERSIDE = NetworkSpec(
    id="riverside",
    title="Riverside District",
    description="Downtown district: three signalised arterials, two cycling collectors and quiet local streets with all-way stops. Sidewalks and crosswalks throughout.",
    ns=[
        Street("elm", "Elm Street", "local", 0.0),
        Street("main", "Main Street", "arterial", 200.0),
        Street("church", "Church Street", "collector", 400.0),
        Street("park", "Park Avenue", "local", 600.0),
    ],
    ew=[
        Street("front", "Front Street", "collector", 0.0),
        Street("king", "King Street", "arterial", 160.0),
        Street("queen", "Queen Street", "arterial", 320.0),
        Street("college", "College Street", "local", 480.0),
    ],
    pois=[
        Poi("hospital", "hospital", "St. Mary's Hospital", (1, 2), "E"),
        Poi("police", "police", "Police Division 52", (2, 1), "S"),
        Poi("park", "park", "Victoria Park", (3, 2), "W"),
        Poi("stadium", "stadium", "Riverside Stadium", (4, 3), "W"),
        Poi("school", "school", "Riverside Public School", (2, 4), "S"),
    ],
    core_cells=[(1, 1), (2, 1), (2, 2), (2, 3), (3, 1), (1, 3), (3, 3)],
    bus_lines=[
        {"id": "501", "name": "501 Queen", "street": "queen", "color": "#C8102E"},
        {"id": "6", "name": "6 Main", "street": "main", "color": "#1F6FB2"},
    ],
)

RIVERSIDE_BIKE = RIVERSIDE.with_class(
    "king",
    "arterial_bike",
    "riverside_bike",
    "Riverside District · King St bike lanes",
    "King Street road diet: one general traffic lane per direction converted into a protected bicycle lane.",
)

GRID = NetworkSpec(
    id="grid",
    title="Test grid 3×3",
    description="Compact, fully signalised 3×3 arterial grid for controlled controller experiments.",
    ns=[Street("a", "A Avenue", "arterial", 0.0), Street("b", "B Avenue", "arterial", 220.0), Street("c", "C Avenue", "arterial", 440.0)],
    ew=[Street("first", "1st Street", "arterial", 0.0), Street("second", "2nd Street", "arterial", 220.0), Street("third", "3rd Street", "arterial", 440.0)],
    fringe=180.0,
    core_cells=[(1, 1), (2, 2), (1, 2), (2, 1)],
    seed=11,
)



# ---------------------------------------------------------------------------
# Plain XML generation
# ---------------------------------------------------------------------------
def _node_id(i: int, j: int) -> str:
    return f"n{i}_{j}"


def _junction_type(a: str, b: str) -> str:
    classes = {a, b}
    if any(c.startswith("arterial") for c in classes):
        return "traffic_light"
    if classes == {"collector"}:
        return "traffic_light"
    if "collector" in classes:
        return "priority_stop"
    return "allway_stop"


def _control(spec: NetworkSpec, sx: Street, sy: Street) -> str:
    return spec.controls.get((sx.id, sy.id)) or _junction_type(sx.cls, sy.cls)


def _extent(street: Street, cross: list[Street]) -> tuple[float, float]:
    """Range of the street along its own axis (positions of its end cross streets)."""
    pos = {s.id: s.pos for s in cross}
    lo, hi = street.span
    return (pos[lo] if lo else -math.inf, pos[hi] if hi else math.inf)


def _crosses(spec: NetworkSpec, i: int, j: int) -> bool:
    sx, sy = spec.ns[i], spec.ew[j]
    xlo, xhi = _extent(sy, spec.ns)
    ylo, yhi = _extent(sx, spec.ew)
    return xlo - 0.5 <= sx.pos <= xhi + 0.5 and ylo - 0.5 <= sy.pos <= yhi + 0.5


def _junctions(spec: NetworkSpec) -> list[tuple[int, int]]:
    return [(i, j) for i in range(len(spec.ns)) for j in range(len(spec.ew)) if _crosses(spec, i, j)]


def _segment_name(street: Street, cross: list[Street], at: float) -> str:
    """Name of the street from position ``at`` onwards (towards increasing x / y)."""
    pos = {s.id: s.pos for s in cross}
    name = street.name
    for cid, new in sorted(street.renames, key=lambda r: pos[r[0]]):
        if at >= pos[cid] - 0.5:
            name = new
    return name


def _chain(spec: NetworkSpec, street: Street, axis: str) -> list[str]:
    """Node ids along the street, west to east / south to north, fringe stubs included."""
    if axis == "ew":
        j = spec.ew.index(street)
        inner = [_node_id(i, j) for i in range(len(spec.ns)) if _crosses(spec, i, j)]
        head, tail = f"fw{j}", f"fe{j}"
    else:
        i = spec.ns.index(street)
        inner = [_node_id(i, j) for j in range(len(spec.ew)) if _crosses(spec, i, j)]
        head, tail = f"fs{i}", f"fn{i}"
    return ([head] if street.span[0] is None else []) + inner + ([tail] if street.span[1] is None else [])


def _edges_for_street(spec: NetworkSpec, street: Street, axis: str) -> list[dict[str, Any]]:
    """Return directed edge records along a street, including fringe stubs.

    A one-way street gets its travel direction plus a pedestrian-only reverse
    edge (``kind == "footway"``) that carries the far sidewalk."""
    chain = _chain(spec, street, axis)
    fwd, back = ("e", "w") if axis == "ew" else ("n", "s")
    sides = ("W", "E") if axis == "ew" else ("S", "N")
    open_lo, open_hi = street.span[0] is None, street.span[1] is None
    out = []
    n = len(chain) - 1

    def kind_of(first: bool, last: bool, enters: bool, leaves: bool) -> str:
        if first and enters:
            return "entry"
        if last and leaves:
            return "exit"
        return "street"

    for d in (fwd, back):
        foot = street.oneway is not None and street.oneway != d
        for k in range(n):
            if d == fwd:
                a, b, seg = chain[k], chain[k + 1], k
                kind = kind_of(k == 0, k == n - 1, open_lo, open_hi)
                side = sides[0] if kind == "entry" else (sides[1] if kind == "exit" else None)
            else:
                a, b, seg = chain[n - k], chain[n - k - 1], n - 1 - k
                kind = kind_of(k == 0, k == n - 1, open_hi, open_lo)
                side = sides[1] if kind == "entry" else (sides[0] if kind == "exit" else None)
            if foot:
                kind, side = "footway", None
            out.append({"id": f"{street.id}_{d}{k}", "from": a, "to": b, "street": street, "dir": d, "kind": kind, "side": side, "seg": seg})
    return out


def _write_plain(spec: NetworkSpec, prefix: Path) -> list[dict[str, Any]]:
    xs = [s.pos for s in spec.ns]
    ys = [s.pos for s in spec.ew]
    x_lo, x_hi, y_lo, y_hi = min(xs) - spec.fringe, max(xs) + spec.fringe, min(ys) - spec.fringe, max(ys) + spec.fringe
    nodes = ET.Element("nodes")
    for i, j in _junctions(spec):
        sx, sy = spec.ns[i], spec.ew[j]
        jt = _control(spec, sx, sy)
        attrs = {"id": _node_id(i, j), "x": f"{sx.pos:.2f}", "y": f"{sy.pos:.2f}", "type": jt, "radius": f"{CURB_RADIUS:.1f}"}
        if jt == "traffic_light":
            attrs["tlType"] = "static"
        ET.SubElement(nodes, "node", attrs)
    for j, s in enumerate(spec.ew):
        if s.span[0] is None:
            ET.SubElement(nodes, "node", {"id": f"fw{j}", "x": f"{x_lo:.2f}", "y": f"{s.pos:.2f}", "type": "dead_end"})
        if s.span[1] is None:
            ET.SubElement(nodes, "node", {"id": f"fe{j}", "x": f"{x_hi:.2f}", "y": f"{s.pos:.2f}", "type": "dead_end"})
    for i, s in enumerate(spec.ns):
        if s.span[0] is None:
            ET.SubElement(nodes, "node", {"id": f"fs{i}", "x": f"{s.pos:.2f}", "y": f"{y_lo:.2f}", "type": "dead_end"})
        if s.span[1] is None:
            ET.SubElement(nodes, "node", {"id": f"fn{i}", "x": f"{s.pos:.2f}", "y": f"{y_hi:.2f}", "type": "dead_end"})
    coords: dict[str, tuple[float, float]] = {}
    for n in nodes.iter("node"):
        coords[n.get("id")] = (float(n.get("x")), float(n.get("y")))
    edges = ET.Element("edges")
    records: list[dict[str, Any]] = []
    for axis, streets in (("ew", spec.ew), ("ns", spec.ns)):
        cross = spec.ns if axis == "ew" else spec.ew
        for st in streets:
            cls = CLASSES[st.cls]
            for rec in _edges_for_street(spec, st, axis):
                (x0, y0), (x1, y1) = coords[rec["from"]], coords[rec["to"]]
                lo = min(x0, x1) if axis == "ew" else min(y0, y1)
                name = _segment_name(st, cross, lo)
                parts = [dict(rec, part="full", name=name)]
                if st.oneway:
                    lanes = ONEWAY_LANES[st.cls] if rec["kind"] != "footway" else [ONEWAY_LANES[st.cls][0]]
                else:
                    lanes = cls["lanes"]
                if cls.get("bay") and not st.oneway and rec["kind"] in ("entry", "street"):
                    L = math.hypot(x1 - x0, y1 - y0)
                    # short downtown blocks: keep at least ~40 % of the block ahead of the bay opening
                    f = 1.0 - min(cls["bay"], 0.6 * L) / L
                    mid = f"{rec['id']}_x"
                    nd = ET.SubElement(nodes, "node", {"id": mid, "x": f"{x0 + (x1 - x0) * f:.2f}", "y": f"{y0 + (y1 - y0) * f:.2f}", "type": "priority"})
                    coords[mid] = (float(nd.get("x")), float(nd.get("y")))
                    parts = [
                        dict(rec, to=mid, part="main", name=name),
                        dict(rec, id=rec["id"] + "b", **{"from": mid}, part="bay", side=None, name=name),
                    ]
                for r in parts:
                    attrs = {
                        "id": r["id"], "from": r["from"], "to": r["to"], "priority": str(cls["priority"]),
                        "numLanes": str(len(lanes)), "speed": f"{cls['speed']:.2f}", "name": name, "type": st.cls,
                    }
                    if st.oneway:
                        attrs["shape"] = _oneway_shape(st, coords[r["from"]], coords[r["to"]], r["kind"] == "footway")
                    e = ET.SubElement(edges, "edge", attrs)
                    for idx, ld in enumerate(lanes):
                        attrs = {"index": str(idx), "width": f"{ld.width:.2f}"}
                        if ld.role == "bay" and r["part"] in ("main", "full"):
                            attrs["disallow"] = "all"
                        else:
                            attrs["allow"] = ld.allow
                        ET.SubElement(e, "lane", attrs)
                    records.append(r)
    ET.ElementTree(nodes).write(prefix.with_name(prefix.name + ".nod.xml"), encoding="UTF-8", xml_declaration=True)
    ET.ElementTree(edges).write(prefix.with_name(prefix.name + ".edg.xml"), encoding="UTF-8", xml_declaration=True)
    return records


_COMMON = [
    "--no-turnarounds", "true",
    "--rectangular-lane-cut", "true",
    "--junctions.corner-detail", "8",
    "--junctions.internal-link-detail", "12",
    "--default.junctions.radius", f"{CURB_RADIUS}",
    "--tls.default-type", "static",
    "--tls.yellow.time", "4",
    "--tls.allred.time", "2",
    "--tls.left-green.time", "8",
    "--tls.crossing-min.time", "6",
    "--tls.crossing-clearance.time", "5",
    "--walkingareas", "true",
    "--offset.disable-normalization", "true",
    "--xml-validation", "never",
]


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or "Error" in (proc.stderr or ""):
        raise RuntimeError(f"{Path(cmd[0]).name} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stderr


def _exclusive_left(spec: NetworkSpec, records: list[dict[str, Any]], pass1: Path, prefix: Path) -> None:
    """Make the left-turn bay of every arterial approach left-only and forbid
    left turns from the through lanes (protected-left operation); drop the
    left turns the spec prohibits."""
    import sumolib

    from .. import sumo_env  # noqa: F401

    net = sumolib.net.readNet(str(pass1))
    bays = {r["id"] for r in records if r.get("part") == "bay"}
    # turn prohibitions: approach edges (by street) into the junction with the named cross street
    node_streets = {_node_id(i, j): {spec.ns[i].id, spec.ew[j].id} for i, j in _junctions(spec)}
    banned = {r["id"] for r in records for a, b in spec.no_left
              if r["street"].id == a and r["kind"] != "footway" and b in node_streets.get(r["to"], set())}
    con_path = prefix.with_name(prefix.name + ".con.xml")
    tree = ET.parse(con_path)
    root = tree.getroot()
    for c in list(root.iter("connection")):
        frm, to = c.get("from"), c.get("to")
        if to is not None and frm in banned:
            lane = net.getEdge(frm).getLane(int(c.get("fromLane")))
            if any(cn.getTo().getID() == to and cn.getDirection() in ("l", "L") for cn in lane.getOutgoing()):
                root.remove(c)
                continue
        if to is None or frm not in bays:
            continue
        edge = net.getEdge(frm)
        car = [l.getIndex() for l in edge.getLanes() if l.allows("passenger")]
        lane = int(c.get("fromLane"))
        direction = None
        for conn in edge.getLane(lane).getOutgoing():
            if conn.getTo().getID() == to and conn.getToLane().getIndex() == int(c.get("toLane")):
                direction = conn.getDirection()
        if direction is None:
            continue
        is_left = direction in ("l", "L")
        if (lane == car[-1]) != is_left:
            root.remove(c)
    tree.write(con_path, encoding="UTF-8", xml_declaration=True)


def _oneway_shape(st: Street, a: tuple[float, float], b: tuple[float, float], footway: bool) -> str:
    """Geometry line of one half of a one-way street: the line between the carriageway and
    the far sidewalk. The traffic edge spreads its lanes to the right of it and the reverse
    footway spreads its sidewalk to the other side, so the street stays centred on its axis.

    Placing both on that line (instead of pairing them with spreadType "roadCenter") lets
    netconvert build the far sidewalk's walking areas at the corners; paired, it runs them
    across the junction to its centre."""
    lanes = ONEWAY_LANES[st.cls]
    carriage, far = sum(l.width for l in lanes), lanes[0].width
    off = (far - carriage) / 2  # to the right of the traffic direction
    if footway:
        off = -off  # same line, measured to the right of the footway's own direction
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    nx, ny = dy / L * off, -dx / L * off
    return f"{a[0] + nx:.2f},{a[1] + ny:.2f} {b[0] + nx:.2f},{b[1] + ny:.2f}"


def _restore_footway_width(spec: NetworkSpec, records: list[dict[str, Any]], prefix: Path) -> None:
    """netconvert's plain output folds a single-lane edge's lane into the edge
    element and drops its width; put the sidewalk width back for pass 2."""
    width = {r["id"]: ONEWAY_LANES[r["street"].cls][0].width for r in records if r["kind"] == "footway"}
    if not width:
        return
    path = prefix.with_name(prefix.name + ".edg.xml")
    tree = ET.parse(path)
    for e in tree.getroot().iter("edge"):
        w = width.get(e.get("id", ""))
        if w is not None:
            e.set("width", f"{w:.2f}")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def build_network(spec: NetworkSpec, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / f"_{spec.id}"
    work.mkdir(exist_ok=True)
    prefix = work / "plain"
    records = _write_plain(spec, prefix)
    pass1 = work / "pass1.net.xml"
    _run([
        binary("netconvert"),
        "--node-files", str(prefix.with_name("plain.nod.xml")),
        "--edge-files", str(prefix.with_name("plain.edg.xml")),
        "--crossings.guess", "true",
        *_COMMON,
        "--plain-output-prefix", str(work / "p1"),
        "-o", str(pass1),
    ])
    # pass 2: exclusive left lanes, rebuild signal programs from the new connections
    for ext in ("nod", "edg", "con"):
        (work / f"p1.{ext}.xml").replace(prefix.with_name(f"plain.{ext}.xml"))
    _exclusive_left(spec, records, pass1, prefix)
    _restore_footway_width(spec, records, prefix)
    net_path = out_dir / f"{spec.id}.net.xml"
    _run([
        binary("netconvert"),
        "--node-files", str(prefix.with_name("plain.nod.xml")),
        "--edge-files", str(prefix.with_name("plain.edg.xml")),
        "--connection-files", str(prefix.with_name("plain.con.xml")),
        *_COMMON,
        "-o", str(net_path),
    ])
    # netconvert only supplies the phase structure; engineer cycle, splits, clearances and offsets (sim/signals.py)
    signals = retime_net(net_path)
    layout = build_layout(spec, records, net_path)
    layout["signals"] = signals
    (out_dir / f"{spec.id}.city.json").write_text(json.dumps(layout, separators=(",", ":")), encoding="utf-8")
    _write_bus_stops(spec, layout, out_dir / f"{spec.id}.stops.add.xml")
    return net_path


def build_all(out_dir: Path) -> list[Path]:
    return [build_network(spec, out_dir) for spec in NETWORKS.values()]


# ---------------------------------------------------------------------------
# City layout: blocks, buildings, POIs, labels, zones
# ---------------------------------------------------------------------------
def _round_rect(x0: float, y0: float, x1: float, y1: float, r: float, n: int = 5) -> list[list[float]]:
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts: list[list[float]] = []
    corners = [(x1 - r, y0 + r, -math.pi / 2), (x1 - r, y1 - r, 0.0), (x0 + r, y1 - r, math.pi / 2), (x0 + r, y0 + r, math.pi)]
    for cx, cy, a0 in corners:
        for k in range(n + 1):
            a = a0 + (math.pi / 2) * k / n
            pts.append([round(cx + r * math.cos(a), 2), round(cy + r * math.sin(a), 2)])
    return pts


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[round(x0, 2), round(y0, 2)], [round(x1, 2), round(y0, 2)], [round(x1, 2), round(y1, 2)], [round(x0, 2), round(y1, 2)]]


def _clip_convex(pts: list[list[float]], clip: list[list[float]]) -> list[list[float]]:
    """Sutherland-Hodgman clip of a polygon to a convex, counter-clockwise polygon."""
    out = pts
    for e0, e1 in zip(clip, clip[1:] + clip[:1]):
        src, out = out, []
        if not src:
            break
        ex, ey = e1[0] - e0[0], e1[1] - e0[1]
        side = lambda p: ex * (p[1] - e0[1]) - ey * (p[0] - e0[0])  # noqa: E731  (>= 0: left of the edge, inside)
        for a, c in zip(src, src[1:] + src[:1]):
            sa, sc = side(a), side(c)
            if sc >= 0:
                if sa < 0:
                    out.append(_lerp(a, c, sa / (sa - sc)))
                out.append(c)
            elif sa >= 0:
                out.append(_lerp(a, c, sa / (sa - sc)))
    return [[round(x, 1), round(y, 1)] for x, y in out]


def _lerp(a: list[float], c: list[float], t: float) -> list[float]:
    return [a[0] + (c[0] - a[0]) * t, a[1] + (c[1] - a[1]) * t]


def _area(pts: list[list[float]]) -> float:
    return abs(sum(a[0] * c[1] - c[0] * a[1] for a, c in zip(pts, pts[1:] + pts[:1]))) / 2


def _bbox(pts: list[list[float]]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


Plot = tuple[tuple[float, float, float, float], list[list[float]], tuple[float, float, float, float] | None]  # block, buildable ground, POI ground


def _plot(x0: float, y0: float, x1: float, y1: float, poi_ground: tuple[float, float, float, float] | None) -> Plot:
    """A block, the ground real context may cover (BUILDING_SETBACK behind the sidewalk,
    corners rounded like the block) and the part a POI takes, if any."""
    s = BUILDING_SETBACK
    return (x0, y0, x1, y1), _round_rect(x0 + s, y0 + s, x1 - s, y1 - s, CURB_RADIUS + 1.0), poi_ground


def _place_context(context: dict[str, Any], plots: list[Plot], corridors: list[tuple[float, float, float, float]], limit: tuple[float, float, float, float],
                   buildings: list[dict[str, Any]], features: list[dict[str, Any]], trees: list[list[float]]) -> None:
    """Fit the real map context around the simulated streets without adding any road.

    Inside a block, a shape is clipped to the block's buildable ground (minus any POI
    ground it lands on); outside every block (the fringe past the street stubs and around
    the map) it is kept whole unless it comes within BUILDING_SETBACK of a simulated street."""
    s = BUILDING_SETBACK
    near = [(r[0] - s, r[1] - s, r[2] + s, r[3] + s) for r in corridors]

    def fit(shape: list[list[float]]) -> list[list[float]] | None:
        bx = _bbox(shape)
        cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        if not (limit[0] <= cx <= limit[2] and limit[1] <= cy <= limit[3]):
            return None
        plot = next((p for p in plots if p[0][0] <= cx <= p[0][2] and p[0][1] <= cy <= p[0][3]), None)
        if plot:
            _, ground, taken = plot
            if taken and taken[0] <= cx <= taken[2] and taken[1] <= cy <= taken[3]:
                return None
            shape = _clip_convex(shape, ground)
        elif any(_overlaps(bx, r) for r in near):
            return None
        return shape if len(shape) >= 3 and _area(shape) >= 12.0 else None

    for b in context.get("buildings", []):
        if shape := fit(b["shape"]):
            buildings.append({**b, "shape": shape})
    for a in context.get("areas", []):
        if shape := fit(a["shape"]):
            features.append({"kind": a["kind"], "use": a["use"], "shape": shape})
    for x, y, r in context.get("trees", []):
        if fit([[x - 2, y - 2], [x + 2, y - 2], [x + 2, y + 2], [x - 2, y + 2]]):
            trees.append([x, y, r])


def _grid_lines(streets: list[Street], cross: list[Street]) -> list[dict[str, Any]]:
    """Distinct street lines along one axis (streets that share a line, e.g. Windsor
    and Widmer, are one line), each with the streets on it and their extents."""
    lines: list[dict[str, Any]] = []
    for s in sorted(streets, key=lambda s: s.pos):
        if lines and s.pos - lines[-1]["pos"] < 6.0:
            lines[-1]["streets"].append(s)
        else:
            lines.append({"pos": s.pos, "streets": [s]})
    for ln in lines:
        ln["hw"] = max(half_width(s.cls, bool(s.oneway)) for s in ln["streets"])
        ln["extents"] = [_extent(s, cross) for s in ln["streets"]]
    return lines


def _merge_cells(cols: list[dict[str, Any]], rows: list[dict[str, Any]], col_mid: list[float], row_mid: list[float]) -> list[tuple[int, int, int, int]]:
    """Group grid cells into blocks: neighbouring cells merge where no street
    separates them. Returns (i0, j0, i1, j1) cell ranges (inclusive)."""
    ncols, nrows = len(cols) + 1, len(rows) + 1
    parent = {(i, j): (i, j) for i in range(ncols) for j in range(nrows)}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    def covered(line: dict[str, Any], at: float) -> bool:
        return any(lo - 0.5 <= at <= hi + 0.5 for lo, hi in line["extents"])

    for i in range(ncols):
        for j in range(nrows):
            if i + 1 < ncols and not covered(cols[i], row_mid[j]):
                parent[find((i + 1, j))] = find((i, j))
            if j + 1 < nrows and not covered(rows[j], col_mid[i]):
                parent[find((i, j + 1))] = find((i, j))
    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for c in parent:
        groups.setdefault(find(c), []).append(c)
    out = []
    for cells in groups.values():
        i0, i1 = min(c[0] for c in cells), max(c[0] for c in cells)
        j0, j1 = min(c[1] for c in cells), max(c[1] for c in cells)
        if len(cells) == (i1 - i0 + 1) * (j1 - j0 + 1):
            out.append((i0, j0, i1, j1))
        else:  # not a rectangle: keep the cells as separate blocks
            out.extend((i, j, i, j) for i, j in cells)
    return sorted(out, key=lambda b: (b[0], b[1]))


def _onward_options(records: list[dict[str, Any]], net_path: Path) -> dict[str, tuple[int, int]]:
    """Per edge, how well connected it is for a vehicle based there: (fewest of the distinct inner edges it can
    be reached from and continued onto, the number it can continue onto). Bays count as their block."""
    import sumolib

    from .. import sumo_env  # noqa: F401

    net = sumolib.net.readNet(str(net_path))
    kinds = {r["id"]: r["kind"] for r in records}
    out: dict[str, int] = {}
    for r in records:
        last = net.getEdge(r["id"] + "b" if r["part"] == "main" else r["id"])
        first = net.getEdge(r["id"][:-1] if r["part"] == "bay" else r["id"])
        nxt = {c.getTo().getID() for lane in last.getLanes() if lane.allows("passenger") for c in lane.getOutgoing()}
        prev = {e.getID() for e in first.getIncoming() if kinds.get(e.getID()) == "street"}
        onward = len({e for e in nxt if kinds.get(e) in ("street", "entry")})
        out[r["id"]] = (min(onward, len(prev)), onward)
    return out


def build_layout(spec: NetworkSpec, records: list[dict[str, Any]], net_path: Path) -> dict[str, Any]:
    rng = random.Random(spec.seed)
    onward = _onward_options(records, net_path)
    xs = [s.pos for s in spec.ns]
    ys = [s.pos for s in spec.ew]
    x_lo, x_hi, y_lo, y_hi = min(xs) - spec.fringe, max(xs) + spec.fringe, min(ys) - spec.fringe, max(ys) + spec.fringe
    margin = 70.0
    bx0, bx1 = x_lo - margin, x_hi + margin
    by0, by1 = y_lo - margin, y_hi + margin

    cols = _grid_lines(spec.ns, spec.ew)
    rows = _grid_lines(spec.ew, spec.ns)
    col_edges = [bx0] + [c["pos"] for c in cols] + [bx1]
    row_edges = [by0] + [r["pos"] for r in rows] + [by1]
    col_mid = [(col_edges[i] + col_edges[i + 1]) / 2 for i in range(len(cols) + 1)]
    row_mid = [(row_edges[j] + row_edges[j + 1]) / 2 for j in range(len(rows) + 1)]
    ncols, nrows = len(cols) + 1, len(rows) + 1

    def bounds(i0: int, j0: int, i1: int, j1: int) -> tuple[float, float, float, float]:
        x0 = cols[i0 - 1]["pos"] + cols[i0 - 1]["hw"] if i0 > 0 else bx0
        x1 = cols[i1]["pos"] - cols[i1]["hw"] if i1 < len(cols) else bx1
        y0 = rows[j0 - 1]["pos"] + rows[j0 - 1]["hw"] if j0 > 0 else by0
        y1 = rows[j1]["pos"] - rows[j1]["hw"] if j1 < len(rows) else by1
        return x0, y0, x1, y1

    coords: dict[str, tuple[float, float]] = {}
    for i, j in _junctions(spec):
        coords[_node_id(i, j)] = (spec.ns[i].pos, spec.ew[j].pos)
    for j, s in enumerate(spec.ew):
        coords[f"fw{j}"], coords[f"fe{j}"] = (x_lo, s.pos), (x_hi, s.pos)
    for i, s in enumerate(spec.ns):
        coords[f"fs{i}"], coords[f"fn{i}"] = (s.pos, y_lo), (s.pos, y_hi)
    edge_span: dict[str, tuple[float, float]] = {}
    for r in records:
        if r["part"] == "bay":
            continue
        a = coords[r["from"]]
        b = coords[r["to"] if r["part"] == "full" else next(x["to"] for x in records if x["id"] == r["id"] + "b")]
        k = 0 if r["street"] in spec.ew else 1
        edge_span[r["id"]] = (min(a[k], b[k]), max(a[k], b[k]))
        if r["part"] == "main":
            edge_span[r["id"] + "b"] = edge_span[r["id"]]

    line_hw = {s.id: ln["hw"] for ln in cols + rows for s in ln["streets"]}

    def frontage(x0: float, y0: float, x1: float, y1: float) -> dict[str, list[str]]:
        """Vehicle edges along each side of a block (all segments within its extent)."""
        out: dict[str, list[str]] = {"S": [], "N": [], "W": [], "E": []}
        for r in records:
            if r["kind"] == "footway":
                continue
            st = r["street"]
            lo, hi = edge_span[r["id"]]
            mid = (lo + hi) / 2
            if st in spec.ew and x0 - 1 <= mid <= x1 + 1:
                hw = line_hw[st.id]
                if abs(st.pos + hw - y0) < 1.0:
                    out["S"].append(r["id"])
                elif abs(st.pos - hw - y1) < 1.0:
                    out["N"].append(r["id"])
            elif st in spec.ns and y0 - 1 <= mid <= y1 + 1:
                hw = line_hw[st.id]
                if abs(st.pos + hw - x0) < 1.0:
                    out["W"].append(r["id"])
                elif abs(st.pos - hw - x1) < 1.0:
                    out["E"].append(r["id"])
        return out

    edges_by_id = {r["id"]: r for r in records}
    blocks: list[dict[str, Any]] = []
    buildings: list[dict[str, Any]] = []
    trees: list[list[float]] = []
    features: list[dict[str, Any]] = []
    pois_out: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    core = set(spec.core_cells)
    real = bool(spec.context)
    plots: list[Plot] = []
    for i0, j0, i1, j1 in _merge_cells(cols, rows, col_mid, row_mid):
        cells = [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
        x0, y0, x1, y1 = bounds(i0, j0, i1, j1)
        outer = i0 == 0 or j0 == 0 or i1 == ncols - 1 or j1 == nrows - 1
        poi = next((p for p in spec.pois if p.cell in cells), None)
        is_core = any(c in core for c in cells)
        use = poi.kind if poi else ("commercial" if is_core else "residential")
        r_corner = CURB_RADIUS + 1.0
        bid = f"b{i0}_{j0}"
        blocks.append({"id": bid, "cell": [i0, j0], "cells": [list(c) for c in cells], "use": use, "shape": _round_rect(x0, y0, x1, y1, r_corner), "outer": outer})
        fr = frontage(x0, y0, x1, y1)
        zone_edges = sorted({e for lst in fr.values() for e in lst if edges_by_id[e]["kind"] == "street"})
        if zone_edges:
            zones.append({"id": bid, "use": use, "edges": zone_edges, "core": is_core, "cell": [i0, j0]})
        inner = (x0 + 2.0, y0 + 2.0, x1 - 2.0, y1 - 2.0)
        if poi:
            info = _poi_layout(poi, inner, fr, rng, buildings, features, trees, frontage_strips=not real)
            # responders start (and end) on the first access edge: lead with one that is reachable and has ways on
            info["access"] = sorted(info["access"], key=lambda e: tuple(-v for v in onward.get(e, (0, 0))))
            pois_out.append({"id": poi.id, "kind": poi.kind, "name": poi.name, "cell": [i0, j0], **({"note": poi.note} if poi.note else {}), **info})
            plots.append(_plot(x0, y0, x1, y1, _poi_ground(poi, inner)))
        elif real:
            plots.append(_plot(x0, y0, x1, y1, None))
        else:
            _block_buildings(inner, use, is_core, outer, rng, buildings, trees)
        if not real:
            _street_trees((x0, y0, x1, y1), rng, trees, use)

    if real:
        # simulated streets (fringe stubs included) that real context must never cover
        corridors = [(ln["pos"] - ln["hw"], max(lo - 15, y_lo), ln["pos"] + ln["hw"], min(hi + 15, y_hi)) for ln in cols for lo, hi in ln["extents"]]
        corridors += [(max(lo - 15, x_lo), ln["pos"] - ln["hw"], min(hi + 15, x_hi), ln["pos"] + ln["hw"]) for ln in rows for lo, hi in ln["extents"]]
        _place_context(spec.context, plots, corridors, (bx0 - 150, by0 - 150, bx1 + 150, by1 + 150), buildings, features, trees)

    # street labels (one per segment between junctions, centred)
    labels: list[dict[str, Any]] = []
    for axis, streets, cross in (("ew", spec.ew, spec.ns), ("ns", spec.ns, spec.ew)):
        for st in streets:
            chain = _chain(spec, st, axis)
            for a, b in zip(chain, chain[1:]):
                (xa, ya), (xb, yb) = coords[a], coords[b]
                name = _segment_name(st, cross, min(xa, xb) if axis == "ew" else min(ya, yb))
                labels.append({"text": name, "x": round((xa + xb) / 2, 1), "y": round((ya + yb) / 2, 1), "angle": 0 if axis == "ew" else 90, "cls": st.cls})

    edges_meta = {
        r["id"]: {"kind": r["kind"], "side": r["side"], "street": r["street"].id, "name": r.get("name") or r["street"].name, "cls": r["street"].cls, "dir": r["dir"], "part": r["part"], "seg": r["seg"],
                  **({"oneway": True} if r["street"].oneway else {})}
        for r in records
    }
    junctions = {}
    for i, j in _junctions(spec):
        sx, sy = spec.ns[i], spec.ew[j]
        junctions[_node_id(i, j)] = {"name": f"{_segment_name(sx, spec.ew, sy.pos)} & {_segment_name(sy, spec.ns, sx.pos)}", "control": _control(spec, sx, sy), "x": sx.pos, "y": sy.pos}

    # default camera: the named focus junction, else the first arterial x arterial intersection
    focus = None
    if spec.focus:
        sx = next(s for s in spec.ns if s.id == spec.focus[0])
        sy = next(s for s in spec.ew if s.id == spec.focus[1])
        focus = [sx.pos, sy.pos]
    for i, j in _junctions(spec):
        sx, sy = spec.ns[i], spec.ew[j]
        if sx.cls.startswith("arterial") and sy.cls.startswith("arterial"):
            focus = focus or [sx.pos, sy.pos]
    focus = focus or [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]

    return {
        "id": spec.id,
        "title": spec.title,
        "description": spec.description,
        "bounds": [round(bx0, 1), round(by0, 1), round(bx1, 1), round(by1, 1)],
        "roadBounds": [x_lo, y_lo, x_hi, y_hi],
        "focus": focus,
        "streets": [{"id": s.id, "name": s.name, "cls": s.cls, "axis": ax, "pos": s.pos, "span": list(s.span), "oneway": s.oneway}
                    for ax, lst in (("ns", spec.ns), ("ew", spec.ew)) for s in lst],
        "blocks": blocks,
        "buildings": buildings,
        "trees": trees,
        "features": features,
        "pois": pois_out,
        "labels": labels,
        "zones": zones,
        "edges": edges_meta,
        "junctions": junctions,
        "busLines": spec.bus_lines,
        **({"source": spec.source} if spec.source else {}),
    }


def _block_buildings(rect, use, core, outer, rng, buildings, trees) -> None:
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    if w < 20 or h < 20:
        return
    depth_lo, depth_hi = (16, 26) if core else (11, 17)
    floors_lo, floors_hi = (5, 22) if core else (2, 4)
    setback = 0.6 if core else 4.0
    if outer and not core:
        floors_hi = 3
    depth = min(rng.uniform(depth_lo, depth_hi), h / 2 - 2, w / 2 - 2)

    def strip(ax0, ay0, ax1, ay1, horizontal: bool, face: str) -> None:
        length = (ax1 - ax0) if horizontal else (ay1 - ay0)
        pos = 0.0
        while pos < length - 6:
            pw = rng.uniform(12, 30) if core else rng.uniform(9, 17)
            pw = min(pw, length - pos)
            if length - pos - pw < 7:
                pw = length - pos
            gap = 0.0 if core else rng.uniform(1.2, 3.2)
            sb = setback + (0 if core else rng.uniform(-1.0, 2.0))
            d = depth * rng.uniform(0.75, 1.0)
            if horizontal:
                bx0, bx1 = ax0 + pos + gap / 2, ax0 + pos + pw - gap / 2
                if face == "S":
                    by0, by1 = ay0 + sb, ay0 + sb + d
                else:
                    by0, by1 = ay1 - sb - d, ay1 - sb
            else:
                by0, by1 = ay0 + pos + gap / 2, ay0 + pos + pw - gap / 2
                if face == "W":
                    bx0, bx1 = ax0 + sb, ax0 + sb + d
                else:
                    bx0, bx1 = ax1 - sb - d, ax1 - sb
            if bx1 - bx0 > 4 and by1 - by0 > 4 and rng.random() > (0.04 if core else 0.1):
                floors = rng.randint(floors_lo, floors_hi)
                buildings.append({"shape": _rect(bx0, by0, bx1, by1), "h": floors * 3.4, "use": use, "tone": round(rng.random(), 3)})
            elif not core:
                trees.append([round((bx0 + bx1) / 2, 1), round((by0 + by1) / 2, 1), round(rng.uniform(2.5, 4.0), 1)])
            pos += pw

    strip(x0, y0, x1, y0 + depth + setback, True, "S")
    strip(x0, y1 - depth - setback, x1, y1, True, "N")
    strip(x0, y0 + depth + setback + 2, x0 + depth + setback, y1 - depth - setback - 2, False, "W")
    strip(x1 - depth - setback, y0 + depth + setback + 2, x1, y1 - depth - setback - 2, False, "E")
    # courtyard trees
    cx0, cy0, cx1, cy1 = x0 + depth + setback + 4, y0 + depth + setback + 4, x1 - depth - setback - 4, y1 - depth - setback - 4
    if cx1 - cx0 > 8 and cy1 - cy0 > 8:
        for _ in range(int((cx1 - cx0) * (cy1 - cy0) / (350 if core else 160))):
            trees.append([round(rng.uniform(cx0, cx1), 1), round(rng.uniform(cy0, cy1), 1), round(rng.uniform(2.5, 4.5), 1)])


def _street_trees(rect, rng, trees, use) -> None:
    """Street trees just inside the property line, every ~11 m."""
    if use in ("stadium",):
        return
    x0, y0, x1, y1 = rect
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
        length = math.hypot(bx - ax, by - ay)
        n = int(length // 11)
        for k in range(1, n):
            if rng.random() < 0.35:
                continue
            t = k / n
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            # nudge into the block
            px += 1.6 if ax == x0 and bx == x0 else (-1.6 if ax == x1 and bx == x1 else 0)
            py += 1.6 if ay == y0 and by == y0 else (-1.6 if ay == y1 and by == y1 else 0)
            trees.append([round(px, 1), round(py, 1), round(rng.uniform(2.2, 3.2), 1)])


def _poi_ground(poi: Poi, rect):
    """Part of the block the POI occupies (all of it unless ``poi.area`` names a share)."""
    if not poi.area:
        return rect
    bx0, by0, bx1, by1 = rect
    fx0, fy0, fx1, fy1 = poi.area
    return (bx0 + (bx1 - bx0) * fx0, by0 + (by1 - by0) * fy0, bx0 + (bx1 - bx0) * fx1, by0 + (by1 - by0) * fy1)


def _poi_layout(poi: Poi, rect, frontage, rng, buildings, features, trees, frontage_strips: bool = True) -> dict[str, Any]:
    if poi.area:
        # the POI takes part of the block; the remaining strips are built up as downtown frontage
        # (unless real buildings fill them, see _place_context)
        bx0, by0, bx1, by1 = rect
        sub = _poi_ground(poi, rect)
        if frontage_strips:
            for strip in ((bx0, by0, sub[0] - 3, by1), (sub[2] + 3, by0, bx1, by1), (sub[0], by0, sub[2], sub[1] - 3), (sub[0], sub[3] + 3, sub[2], by1)):
                _block_buildings(strip, "commercial", True, False, rng, buildings, trees)
        rect = sub
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w, h = x1 - x0, y1 - y0
    side = poi.entrance
    access = frontage.get(side) or next((v for v in frontage.values() if v), [])
    ex, ey = {"N": (cx, y1), "S": (cx, y0), "E": (x1, cy), "W": (x0, cy)}[side]
    if poi.kind == "hospital":
        buildings.append({"shape": _rect(x0 + 8, y0 + 12, x1 - 30, y1 - 12), "h": 30, "use": "hospital", "tone": 0.2})
        buildings.append({"shape": _rect(x1 - 30, cy - 18, x1 - 6, cy + 18), "h": 9, "use": "hospital", "tone": 0.4})
        features.append({"kind": "helipad", "x": round((x0 + x1 - 22) / 2, 1), "y": round(cy, 1), "r": 8})
        features.append({"kind": "parking", "shape": _rect(x1 - 30, y0 + 4, x1 - 4, cy - 22)})
    elif poi.kind == "police":
        buildings.append({"shape": _rect(cx - 30, y0 + 6, cx + 30, y0 + 34), "h": 14, "use": "police", "tone": 0.3})
        features.append({"kind": "parking", "shape": _rect(cx - 30, y0 + 38, cx + 30, y0 + 62)})
        _block_buildings((x0, y0 + 66, x1, y1), "commercial", True, False, rng, buildings, trees)
    elif poi.kind == "park":
        features.append({"kind": "park", "shape": _round_rect(x0, y0, x1, y1, 6)})
        features.append({"kind": "path", "points": [[x0, y0 + h * 0.2], [cx - w * 0.1, cy], [x1, y1 - h * 0.15]]})
        features.append({"kind": "path", "points": [[cx, y0], [cx + w * 0.05, cy], [cx - w * 0.05, y1]]})
        features.append({"kind": "pond", "x": round(cx + w * 0.22, 1), "y": round(cy - h * 0.18, 1), "rx": round(w * 0.14, 1), "ry": round(h * 0.11, 1)})
        for _ in range(int(w * h / 220)):
            tx, ty = rng.uniform(x0 + 4, x1 - 4), rng.uniform(y0 + 4, y1 - 4)
            if abs(tx - (cx + w * 0.22)) < w * 0.16 and abs(ty - (cy - h * 0.18)) < h * 0.13:
                continue
            trees.append([round(tx, 1), round(ty, 1), round(rng.uniform(3, 6), 1)])
    elif poi.kind == "stadium":
        features.append({"kind": "parking", "shape": _rect(x0 + 2, y0 + 2, x1 - 2, y1 - 2)})
        rx, ry = min(w, h) * 0.46, min(w, h) * 0.38
        features.append({"kind": "stadium", "x": round(cx, 1), "y": round(cy, 1), "rx": round(max(rx, w * 0.4), 1), "ry": round(ry, 1)})
    elif poi.kind == "school":
        buildings.append({"shape": _rect(x0 + 8, y0 + 8, x0 + 70, y0 + 30), "h": 10, "use": "school", "tone": 0.35})
        buildings.append({"shape": _rect(x0 + 8, y0 + 30, x0 + 30, y0 + 70), "h": 10, "use": "school", "tone": 0.35})
        features.append({"kind": "track", "x": round(x0 + w * 0.68, 1), "y": round(y0 + h * 0.55, 1), "rx": round(min(w * 0.26, 46), 1), "ry": round(min(h * 0.3, 26), 1)})
        features.append({"kind": "playground", "shape": _rect(x0 + 36, y0 + 36, x0 + 66, y0 + 62)})
    return {"x": round(cx, 1), "y": round(cy, 1), "entrance": [round(ex, 1), round(ey, 1)], "access": access}


def _write_bus_stops(spec: NetworkSpec, layout: dict[str, Any], path: Path) -> None:
    """Far-side bus stops on the curb lane of every street segment served by a line."""
    root = ET.Element("additional")
    stops: list[dict[str, Any]] = []
    meta = layout["edges"]
    for line in spec.bus_lines:
        for eid, m in meta.items():
            if m["street"] != line["street"] or m["kind"] != "street" or m["part"] == "bay":
                continue
            curb = 1  # lane 0 is the sidewalk
            if m["cls"] == "arterial_bike":
                curb = 2
            sid = f"stop_{eid}"
            if any(s["id"] == sid for s in stops):
                continue
            stops.append({"id": sid, "edge": eid, "lane": f"{eid}_{curb}", "line": line["id"]})
    import sumolib

    from .. import sumo_env  # noqa: F401

    net = sumolib.net.readNet(str(path.with_name(f"{spec.id}.net.xml")))
    for s in stops:
        L = net.getLane(s["lane"]).getLength()
        start = max(5.0, L * 0.18)
        ET.SubElement(root, "busStop", {"id": s["id"], "lane": s["lane"], "startPos": f"{start:.1f}", "endPos": f"{start + 16:.1f}", "lines": s["line"], "friendlyPos": "true"})
        s["start"], s["end"] = round(start, 1), round(start + 16, 1)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    layout["busStops"] = stops
    (path.with_name(f"{spec.id}.city.json")).write_text(json.dumps(layout, separators=(",", ":")), encoding="utf-8")


# ---------------------------------------------------------------------------
# Registry (the Toronto spec reuses the helpers above)
# ---------------------------------------------------------------------------
from .toronto import DOWNTOWN, DOWNTOWN_BIKE  # noqa: E402

NETWORKS: dict[str, NetworkSpec] = {n.id: n for n in (DOWNTOWN, DOWNTOWN_BIKE)}
LEGACY: dict[str, NetworkSpec] = {n.id: n for n in (RIVERSIDE, RIVERSIDE_BIKE)}
