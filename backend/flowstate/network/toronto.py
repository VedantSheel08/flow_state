"""Downtown Toronto (King West / Entertainment District) network spec.

Registered under the ``district`` / ``district_bike`` ids the rest of the
product uses.

Street lines, classes and one-way directions are measured from a committed
OpenStreetMap extract (``data/osm/toronto_king_west.json``, refreshed with
``tools/fetch_osm.py``). The extract is projected to metres and rotated by the
bearing of the downtown street grid (~16 deg, "Toronto north") so King, Queen,
Front... run east-west on screen, the same orientation as a Toronto street map
drawn to the grid. The compiled network keeps the product's cross-sections
(see ``city.py``) and its procedural city blocks; what comes from OSM is the
street pattern: real spacing, T-junctions, one-way pairs and names.

Junction control follows the class rules in ``city.py`` (arterial crossings
are signalised) with overrides for the side streets that are stop-controlled
in reality.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .city import NetworkSpec, Poi, Street, _grid_lines

OSM_PATH = Path(__file__).resolve().parents[3] / "data" / "osm" / "toronto_king_west.json"
# Buildings, open spaces and trees around the streets (drawn only; see load_context)
CONTEXT_PATH = OSM_PATH.with_name("toronto_king_west_context.json")
REF = (43.6466, -79.3920)  # projection origin (lat, lon), near King & John

# OSM highway class -> product street class
HIGHWAY_CLASS = {"primary": "arterial", "secondary": "arterial", "tertiary": "collector", "residential": "local", "unclassified": "local"}

# (id, axis, OSM names, span (cross street ids), renames)
STREETS: list[tuple[str, str, tuple[str, ...], tuple[str | None, str | None], tuple[tuple[str, str], ...]]] = [
    ("peter", "ns", ("Blue Jays Way", "Peter Street"), (None, None), (("king", "Peter Street"),)),
    ("windsor", "ns", ("Windsor Street",), ("front", "wellington"), ()),
    ("widmer", "ns", ("Widmer Street",), ("king", "richmond"), ()),
    ("john", "ns", ("John Street",), (None, None), ()),
    ("duncan", "ns", ("Ed Mirvish Way", "Duncan Street"), ("king", "queen"), (("pearl", "Duncan Street"),)),
    ("simcoe", "ns", ("Simcoe Street",), (None, None), ()),
    ("front", "ew", ("Front Street West",), (None, None), ()),
    ("wellington", "ew", ("Wellington Street West",), (None, None), ()),
    ("mercer", "ew", ("Mercer Street",), ("peter", "john"), ()),
    ("king", "ew", ("King Street West",), (None, None), ()),
    ("pearl", "ew", ("Pearl Street",), ("john", None), ()),
    ("adelaide", "ew", ("Adelaide Street West",), (None, None), ()),
    ("richmond", "ew", ("Richmond Street West",), (None, None), ()),
    ("queen", "ew", ("Queen Street West",), (None, None), ()),
]

# Side streets meeting an arterial at a stop sign rather than a signal.
STOP_CONTROLLED = [("widmer", "king"), ("widmer", "adelaide"), ("widmer", "richmond"), ("windsor", "front")]

# Peak-period "No left turn" signs: side streets onto the two-way arterials (and the King St pilot corridor).
NO_LEFT = [(side, art) for side in ("peter", "john") for art in ("front", "king", "queen")]

# Window (rotated metres around REF) used to measure the streets; excludes far-off jogs.
CORE = (-120.0, -420.0, 560.0, 380.0)


def _load() -> dict[str, Any]:
    return json.loads(OSM_PATH.read_text(encoding="utf-8"))


def _project(lat: float, lon: float) -> tuple[float, float]:
    x = (lon - REF[1]) * 111_320.0 * math.cos(math.radians(REF[0]))
    y = (lat - REF[0]) * 110_540.0
    return x, y


def measure(data: dict[str, Any]) -> dict[str, Any]:
    """Grid rotation plus, per OSM street name, the rotated offset, class and one-way direction."""
    nodes = {e["id"]: _project(e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}
    ways = [e for e in data["elements"] if e["type"] == "way" and e.get("tags", {}).get("name")]

    # grid bearing: length-weighted direction of the long east-west arterials
    sx = sy = 0.0
    for w in ways:
        if w["tags"]["name"] not in ("King Street West", "Queen Street West"):
            continue
        pts = [nodes[n] for n in w["nodes"]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            a = math.atan2(y1 - y0, x1 - x0) * 2  # doubled: direction-agnostic mean
            L = math.hypot(x1 - x0, y1 - y0)
            sx += L * math.cos(a)
            sy += L * math.sin(a)
    bearing = math.degrees(math.atan2(sy, sx) / 2)
    th = math.radians(-bearing)
    c, s = math.cos(th), math.sin(th)

    def rot(p: tuple[float, float]) -> tuple[float, float]:
        return p[0] * c - p[1] * s, p[0] * s + p[1] * c

    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"off": [], "cls": defaultdict(float), "dir": defaultdict(float), "len": 0.0})
    for w in ways:
        t = w["tags"]
        pts = [rot(nodes[n]) for n in w["nodes"]]
        st = stats[t["name"]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            if not (CORE[0] <= mx <= CORE[2] and CORE[1] <= my <= CORE[3]):
                continue
            L = math.hypot(x1 - x0, y1 - y0)
            ew = abs(x1 - x0) >= abs(y1 - y0)
            st["off"].append((my if ew else mx, L))
            st["cls"][HIGHWAY_CLASS.get(t.get("highway", ""), "local")] += L
            st["len"] += L
            if t.get("oneway") in ("yes", "1", "-1"):
                fwd = (x1 - x0) if ew else (y1 - y0)
                if t["oneway"] == "-1":
                    fwd = -fwd
                st["dir"][("e" if fwd > 0 else "w") if ew else ("n" if fwd > 0 else "s")] += L
    out = {}
    for name, st in stats.items():
        if not st["off"]:
            continue
        offs = sorted(st["off"])
        half, acc, med = sum(L for _, L in offs) / 2, 0.0, offs[0][0]
        for v, L in offs:
            acc += L
            if acc >= half:
                med = v
                break
        cls = max(st["cls"].items(), key=lambda kv: kv[1])[0]
        oneway = None
        if st["dir"]:
            d, L = max(st["dir"].items(), key=lambda kv: kv[1])
            if L >= 0.6 * st["len"]:  # mostly one-way in the window
                oneway = d
        out[name] = {"offset": med, "cls": cls, "oneway": oneway, "length": st["len"]}
    return {"bearing": bearing, "rot": (c, s), "streets": out}


def _levels_height(t: dict[str, str]) -> float:
    for key, scale in (("height", 1.0), ("building:levels", 3.4)):
        try:
            return round(float(t[key].split()[0].replace(",", ".")) * scale, 1)
        except (KeyError, ValueError, IndexError):
            continue
    return 10.0


def _area_kind(t: dict[str, str]) -> tuple[str, str] | None:
    """(feature kind, use) for a context area, or None for areas the map does not draw (only greenery is)."""
    if t.get("leisure") in ("park", "garden", "playground", "pitch") or t.get("landuse") in ("grass", "recreation_ground"):
        return "green", t.get("leisure") or "grass"
    return None


def _building_use(t: dict[str, str]) -> str:
    b = t.get("building", "")
    if b in ("residential", "apartments", "house", "terrace", "detached", "semidetached_house", "dormitory"):
        return "residential"
    if b in ("school", "university", "college") or t.get("amenity") == "school":
        return "school"
    if b == "hospital":
        return "hospital"
    return "commercial"


def load_context(m: dict[str, Any], ox: float, oy: float) -> dict[str, Any]:
    """Real buildings, open spaces and trees from the context extract, in network coordinates
    (rotated to the street grid and shifted to the Peter & Front origin). Map context only."""
    if not CONTEXT_PATH.exists():
        return {}
    data = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    c, s = m["rot"]

    def to_net(lat: float, lon: float) -> list[float]:
        x, y = _project(lat, lon)
        return [round(x * c - y * s - ox, 1), round(x * s + y * c - oy, 1)]

    nodes = {e["id"]: to_net(e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}

    def ring(way: dict[str, Any]) -> list[list[float]] | None:
        pts = [nodes[n] for n in way["nodes"] if n in nodes]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        return pts if len(pts) >= 3 else None

    buildings: list[dict[str, Any]] = []
    areas: list[dict[str, Any]] = []
    trees: list[list[float]] = []
    for e in data["elements"]:
        t = e.get("tags") or {}
        if not t:
            continue
        if e["type"] == "node":
            if t.get("natural") == "tree":
                trees.append([*nodes[e["id"]], 3.0])
            continue
        rings = [ring(e)] if e["type"] == "way" else [ring(ways[mb["ref"]]) for mb in e.get("members", []) if mb["type"] == "way" and mb.get("role") == "outer" and mb["ref"] in ways]
        for pts in filter(None, rings):
            if "building" in t:
                buildings.append({"shape": pts, "h": _levels_height(t), "use": _building_use(t), "tone": round((e["id"] % 997) / 997, 3)})
            elif kind := _area_kind(t):
                areas.append({"kind": kind[0], "use": kind[1], "shape": pts})
    return {"buildings": buildings, "areas": areas, "trees": trees}


def _cell(cols: list[float], rows: list[float], x: float, y: float) -> tuple[int, int]:
    """Block cell (i, j) that contains the point, in the extended grid of distinct street lines."""
    return sum(1 for c in cols if c < x), sum(1 for r in rows if r < y)


def build_specs() -> tuple[NetworkSpec, NetworkSpec]:
    data = _load()
    m = measure(data)
    lines: dict[str, Street] = {}
    for sid, axis, names, span, renames in STREETS:
        found = [m["streets"][n] for n in names if n in m["streets"]]
        if not found:
            raise ValueError(f"{names[0]} missing from {OSM_PATH.name}")
        L = sum(f["length"] for f in found)
        pos = sum(f["offset"] * f["length"] for f in found) / L
        main = max(found, key=lambda f: f["length"])
        lines[sid] = Street(sid, names[0], main["cls"], pos, span, main["oneway"] if len(found) == 1 else None, renames)
    # origin at Peter & Front (south-west corner of the core), positions rounded to 0.5 m
    ox, oy = lines["peter"].pos, lines["front"].pos
    ns = [Street(s.id, s.name, s.cls, round((s.pos - ox) * 2) / 2, s.span, s.oneway, s.renames) for s in lines.values() if s.id in {d[0] for d in STREETS if d[1] == "ns"}]
    ew = [Street(s.id, s.name, s.cls, round((s.pos - oy) * 2) / 2, s.span, s.oneway, s.renames) for s in lines.values() if s.id in {d[0] for d in STREETS if d[1] == "ew"}]
    for lst in (ns, ew):
        lst.sort(key=lambda s: s.pos)
        # streets on the same line a few metres apart (Windsor continues Widmer) share one position
        for k in range(1, len(lst)):
            if lst[k].pos - lst[k - 1].pos < 6.0:
                lst[k] = Street(lst[k].id, lst[k].name, lst[k].cls, lst[k - 1].pos, lst[k].span, lst[k].oneway, lst[k].renames)
    P = {s.id: s.pos for s in ns + ew}
    # distinct street lines, merged the way city._grid_lines merges them (Windsor/Widmer share one)
    cols = [ln["pos"] for ln in _grid_lines(ns, ew)]
    rows = [ln["pos"] for ln in _grid_lines(ew, ns)]

    def cell(x_id: str | None, y_id: str | None, dx: float = 10.0, dy: float = 10.0) -> tuple[int, int]:
        """Cell just north-east of the (x street, y street) corner; None = outside the first line."""
        x = P[x_id] + dx if x_id else min(cols) - dx
        y = P[y_id] + dy if y_id else min(rows) - dy
        return _cell(cols, rows, x, y)

    interior = [(i, j) for i in range(1, len(cols)) for j in range(1, len(rows))]
    pois = [
        Poi("stadium", "stadium", "Rogers Centre", cell("peter", None), "N"),
        Poi("park", "park", "David Pecaut Square", cell("john", "wellington"), "N", area=(0.30, 0.30, 0.66, 1.0)),
        Poi("school", "school", "Ogden Junior Public School", cell("peter", "queen"), "S"),
        Poi("police", "police", "Toronto Police 52 Division", cell("duncan", "queen"), "S"),
        Poi("hospital", "hospital", "Mount Sinai Hospital", cell("duncan", "richmond"), "W",
            note="Stand-in: the nearest real hospitals are on University Ave, about 0.8 km north-east; placed here so ambulances start inside the map."),
    ]
    poi_cells = {p.cell for p in pois}
    core = [c for c in interior if c not in poi_cells]
    source = {
        "kind": "openstreetmap",
        "file": f"data/osm/{OSM_PATH.name}",
        "bbox": data.get("bbox"),
        "origin": REF,
        "gridBearing": round(m["bearing"], 2),
        "attribution": "© OpenStreetMap contributors (ODbL)",
    }
    down = NetworkSpec(
        id="district",
        title="Downtown Toronto · King West",
        description=(
            "Real street pattern of Toronto's Entertainment District, Front St to Queen St and Peter St to Simcoe St, "
            "imported from OpenStreetMap: the Richmond/Adelaide one-way pair, King and Queen streetcar arterials, "
            "side streets that end at T-junctions and stop-controlled lanes such as Widmer and Mercer."
        ),
        ns=ns,
        ew=ew,
        fringe=140.0,
        pois=pois,
        core_cells=core,
        bus_lines=[
            {"id": "501", "name": "501 Queen", "street": "queen", "color": "#C8102E"},
            {"id": "504", "name": "504 King", "street": "king", "color": "#DA291C"},
        ],
        seed=7,
        controls={k: "priority_stop" for k in STOP_CONTROLLED},
        focus=("john", "king"),
        no_left=NO_LEFT,
        source=source,
        context=load_context(m, ox, oy),
    )
    bike = down.with_class(
        "king",
        "arterial_bike",
        "district_bike",
        "Downtown Toronto · King St bike lanes",
        "King Street West road diet: one general traffic lane per direction converted into a protected bicycle lane.",
    )
    return down, bike


DOWNTOWN, DOWNTOWN_BIKE = build_specs()
