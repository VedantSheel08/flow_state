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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

CURB_RADIUS = 7.0


def half_width(cls: str) -> float:
    return sum(l.width for l in CLASSES[cls]["lanes"])


@dataclass
class Street:
    id: str
    name: str
    cls: str
    pos: float  # x for north-south streets, y for east-west streets


@dataclass
class Poi:
    id: str
    kind: str  # hospital | police | stadium | school | park | transit
    name: str
    cell: tuple[int, int]  # block cell (i, j) in the extended grid
    entrance: str  # side of the block that faces the entrance: N/E/S/W


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

    def with_class(self, street_id: str, cls: str, new_id: str, title: str, description: str) -> "NetworkSpec":
        def swap(lst: list[Street]) -> list[Street]:
            return [Street(s.id, s.name, cls if s.id == street_id else s.cls, s.pos) for s in lst]

        return NetworkSpec(new_id, title, description, swap(self.ns), swap(self.ew), self.fringe, self.pois, self.core_cells, self.bus_lines, self.seed)


# ---------------------------------------------------------------------------
# Network catalogue
# ---------------------------------------------------------------------------
DISTRICT = NetworkSpec(
    id="district",
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

DISTRICT_BIKE = DISTRICT.with_class(
    "king",
    "arterial_bike",
    "district_bike",
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

NETWORKS: dict[str, NetworkSpec] = {n.id: n for n in (DISTRICT, DISTRICT_BIKE)}


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


def _edges_for_street(spec: NetworkSpec, street: Street, axis: str) -> list[dict[str, Any]]:
    """Return directed edge records along a street, including fringe stubs."""
    if axis == "ew":
        j = spec.ew.index(street)
        chain = [f"fw{j}"] + [_node_id(i, j) for i in range(len(spec.ns))] + [f"fe{j}"]
        fwd, back = "e", "w"
        sides = ("W", "E")
    else:
        i = spec.ns.index(street)
        chain = [f"fs{i}"] + [_node_id(i, j) for j in range(len(spec.ew))] + [f"fn{i}"]
        fwd, back = "n", "s"
        sides = ("S", "N")
    out = []
    n = len(chain) - 1
    for k in range(n):
        a, b = chain[k], chain[k + 1]
        kind = "entry" if k == 0 else ("exit" if k == n - 1 else "street")
        out.append({"id": f"{street.id}_{fwd}{k}", "from": a, "to": b, "street": street, "dir": fwd, "kind": kind, "side": sides[0] if kind == "entry" else (sides[1] if kind == "exit" else None), "seg": k})
    for k in range(n):
        a, b = chain[n - k], chain[n - k - 1]
        kind = "entry" if k == 0 else ("exit" if k == n - 1 else "street")
        out.append({"id": f"{street.id}_{back}{k}", "from": a, "to": b, "street": street, "dir": back, "kind": kind, "side": sides[1] if kind == "entry" else (sides[0] if kind == "exit" else None), "seg": n - 1 - k})
    return out


def _write_plain(spec: NetworkSpec, prefix: Path) -> list[dict[str, Any]]:
    xs = [s.pos for s in spec.ns]
    ys = [s.pos for s in spec.ew]
    nodes = ET.Element("nodes")
    for i, sx in enumerate(spec.ns):
        for j, sy in enumerate(spec.ew):
            jt = _junction_type(sx.cls, sy.cls)
            attrs = {"id": _node_id(i, j), "x": f"{sx.pos:.2f}", "y": f"{sy.pos:.2f}", "type": jt, "radius": f"{CURB_RADIUS:.1f}"}
            if jt == "traffic_light":
                attrs["tlType"] = "static"
            ET.SubElement(nodes, "node", attrs)
    for j, s in enumerate(spec.ew):
        ET.SubElement(nodes, "node", {"id": f"fw{j}", "x": f"{xs[0] - spec.fringe:.2f}", "y": f"{s.pos:.2f}", "type": "dead_end"})
        ET.SubElement(nodes, "node", {"id": f"fe{j}", "x": f"{xs[-1] + spec.fringe:.2f}", "y": f"{s.pos:.2f}", "type": "dead_end"})
    for i, s in enumerate(spec.ns):
        ET.SubElement(nodes, "node", {"id": f"fs{i}", "x": f"{s.pos:.2f}", "y": f"{ys[0] - spec.fringe:.2f}", "type": "dead_end"})
        ET.SubElement(nodes, "node", {"id": f"fn{i}", "x": f"{s.pos:.2f}", "y": f"{ys[-1] + spec.fringe:.2f}", "type": "dead_end"})
    coords: dict[str, tuple[float, float]] = {}
    for n in nodes.iter("node"):
        coords[n.get("id")] = (float(n.get("x")), float(n.get("y")))
    edges = ET.Element("edges")
    records: list[dict[str, Any]] = []
    for axis, streets in (("ew", spec.ew), ("ns", spec.ns)):
        for st in streets:
            cls = CLASSES[st.cls]
            for rec in _edges_for_street(spec, st, axis):
                parts = [dict(rec, part="full")]
                if cls.get("bay") and rec["kind"] in ("entry", "street"):
                    (x0, y0), (x1, y1) = coords[rec["from"]], coords[rec["to"]]
                    L = math.hypot(x1 - x0, y1 - y0)
                    f = 1.0 - cls["bay"] / L
                    mid = f"{rec['id']}_x"
                    nd = ET.SubElement(nodes, "node", {"id": mid, "x": f"{x0 + (x1 - x0) * f:.2f}", "y": f"{y0 + (y1 - y0) * f:.2f}", "type": "priority"})
                    coords[mid] = (float(nd.get("x")), float(nd.get("y")))
                    parts = [
                        dict(rec, to=mid, part="main"),
                        dict(rec, id=rec["id"] + "b", **{"from": mid}, part="bay", side=None),
                    ]
                for r in parts:
                    e = ET.SubElement(edges, "edge", {
                        "id": r["id"], "from": r["from"], "to": r["to"], "priority": str(cls["priority"]),
                        "numLanes": str(len(cls["lanes"])), "speed": f"{cls['speed']:.2f}", "name": st.name, "type": st.cls,
                    })
                    for idx, ld in enumerate(cls["lanes"]):
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
    left turns from the through lanes (protected-left operation)."""
    import sumolib

    from .. import sumo_env  # noqa: F401

    net = sumolib.net.readNet(str(pass1))
    bays = {r["id"] for r in records if r.get("part") == "bay"}
    con_path = prefix.with_name(prefix.name + ".con.xml")
    tree = ET.parse(con_path)
    root = tree.getroot()
    for c in list(root.iter("connection")):
        frm, to = c.get("from"), c.get("to")
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
    net_path = out_dir / f"{spec.id}.net.xml"
    _run([
        binary("netconvert"),
        "--node-files", str(prefix.with_name("plain.nod.xml")),
        "--edge-files", str(prefix.with_name("plain.edg.xml")),
        "--connection-files", str(prefix.with_name("plain.con.xml")),
        *_COMMON,
        "-o", str(net_path),
    ])
    layout = build_layout(spec, records, net_path)
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


def build_layout(spec: NetworkSpec, records: list[dict[str, Any]], net_path: Path) -> dict[str, Any]:
    rng = random.Random(spec.seed)
    xs = [s.pos for s in spec.ns]
    ys = [s.pos for s in spec.ew]
    hw_x = [half_width(s.cls) for s in spec.ns]
    hw_y = [half_width(s.cls) for s in spec.ew]
    margin = 70.0
    bx0, bx1 = xs[0] - spec.fringe - margin, xs[-1] + spec.fringe + margin
    by0, by1 = ys[0] - spec.fringe - margin, ys[-1] + spec.fringe + margin

    # column/row boundaries of the extended grid
    col_bounds = [(bx0, xs[0] - hw_x[0])] + [(xs[i] + hw_x[i], xs[i + 1] - hw_x[i + 1]) for i in range(len(xs) - 1)] + [(xs[-1] + hw_x[-1], bx1)]
    row_bounds = [(by0, ys[0] - hw_y[0])] + [(ys[j] + hw_y[j], ys[j + 1] - hw_y[j + 1]) for j in range(len(ys) - 1)] + [(ys[-1] + hw_y[-1], by1)]

    edges_by_id = {r["id"]: r for r in records}
    poi_by_cell = {p.cell: p for p in spec.pois}

    def frontage(i: int, j: int) -> dict[str, list[str]]:
        """Street edges that bound block cell (i, j), per side."""
        out: dict[str, list[str]] = {"S": [], "N": [], "W": [], "E": []}
        for side, street_list, idx, along, lo in (
            ("S", spec.ew, j - 1, "ew", i),
            ("N", spec.ew, j, "ew", i),
            ("W", spec.ns, i - 1, "ns", j),
            ("E", spec.ns, i, "ns", j),
        ):
            if not (0 <= idx < len(street_list)):
                continue
            st = street_list[idx]
            # segment index along the street that spans this cell
            for rec in records:
                if rec["street"].id != st.id:
                    continue
                if rec["dir"] in ("e", "n") and rec["seg"] == lo:
                    out[side].append(rec["id"])
                elif rec["dir"] in ("w", "s") and rec["seg"] == lo:
                    out[side].append(rec["id"])
        return out

    blocks: list[dict[str, Any]] = []
    buildings: list[dict[str, Any]] = []
    trees: list[list[float]] = []
    features: list[dict[str, Any]] = []
    pois_out: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    core = set(spec.core_cells)
    ncols, nrows = len(col_bounds), len(row_bounds)
    for i, (x0, x1) in enumerate(col_bounds):
        for j, (y0, y1) in enumerate(row_bounds):
            outer = i in (0, ncols - 1) or j in (0, nrows - 1)
            poi = poi_by_cell.get((i, j))
            use = poi.kind if poi else ("commercial" if (i, j) in core else "residential")
            r_corner = CURB_RADIUS + 1.0
            blocks.append({"id": f"b{i}_{j}", "cell": [i, j], "use": use, "shape": _round_rect(x0, y0, x1, y1, r_corner), "outer": outer})
            fr = frontage(i, j)
            zone_edges = sorted({e for lst in fr.values() for e in lst if edges_by_id[e]["kind"] == "street"})
            if zone_edges:
                zones.append({"id": f"b{i}_{j}", "use": use, "edges": zone_edges, "core": (i, j) in core, "cell": [i, j]})
            inner = (x0 + 2.0, y0 + 2.0, x1 - 2.0, y1 - 2.0)
            if poi:
                info = _poi_layout(poi, inner, fr, rng, buildings, features, trees)
                pois_out.append({"id": poi.id, "kind": poi.kind, "name": poi.name, "cell": [i, j], **info})
            else:
                _block_buildings(inner, use, (i, j) in core, outer, rng, buildings, trees)
            _street_trees((x0, y0, x1, y1), rng, trees, use)

    # street labels (one per interior segment, centred)
    labels: list[dict[str, Any]] = []
    for st in spec.ew:
        for i in range(len(xs) + 1):
            xa = xs[i - 1] if i > 0 else xs[0] - spec.fringe
            xb = xs[i] if i < len(xs) else xs[-1] + spec.fringe
            labels.append({"text": st.name, "x": round((xa + xb) / 2, 1), "y": st.pos, "angle": 0, "cls": st.cls})
    for st in spec.ns:
        for j in range(len(ys) + 1):
            ya = ys[j - 1] if j > 0 else ys[0] - spec.fringe
            yb = ys[j] if j < len(ys) else ys[-1] + spec.fringe
            labels.append({"text": st.name, "x": st.pos, "y": round((ya + yb) / 2, 1), "angle": 90, "cls": st.cls})

    edges_meta = {
        r["id"]: {"kind": r["kind"], "side": r["side"], "street": r["street"].id, "name": r["street"].name, "cls": r["street"].cls, "dir": r["dir"], "part": r["part"], "seg": r["seg"]}
        for r in records
    }
    junctions = {}
    for i, sx in enumerate(spec.ns):
        for j, sy in enumerate(spec.ew):
            junctions[_node_id(i, j)] = {"name": f"{sx.name} & {sy.name}", "control": _junction_type(sx.cls, sy.cls), "x": sx.pos, "y": sy.pos}

    # default camera: the busiest arterial intersection near the centre
    focus = None
    for i, sx in enumerate(spec.ns):
        for j, sy in enumerate(spec.ew):
            if sx.cls.startswith("arterial") and sy.cls.startswith("arterial"):
                focus = focus or [sx.pos, sy.pos]
    focus = focus or [(xs[0] + xs[-1]) / 2, (ys[0] + ys[-1]) / 2]

    return {
        "id": spec.id,
        "title": spec.title,
        "description": spec.description,
        "bounds": [round(bx0, 1), round(by0, 1), round(bx1, 1), round(by1, 1)],
        "roadBounds": [xs[0] - spec.fringe, ys[0] - spec.fringe, xs[-1] + spec.fringe, ys[-1] + spec.fringe],
        "focus": focus,
        "streets": [{"id": s.id, "name": s.name, "cls": s.cls, "axis": ax, "pos": s.pos} for ax, lst in (("ns", spec.ns), ("ew", spec.ew)) for s in lst],
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


def _poi_layout(poi: Poi, rect, frontage, rng, buildings, features, trees) -> dict[str, Any]:
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
