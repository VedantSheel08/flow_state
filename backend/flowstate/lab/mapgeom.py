"""Map description of the lab's 2x2 grid in main's ``NetworkGeom`` format, so main's own map renderer can draw it.

The simulation network (``flowstate.rl.network``) is never changed. For display only:

* a copy of it is passed through ``netconvert`` to add sidewalks and crossings (same road positions, lane shapes and coordinates);
* a *procedurally generated* city sidecar (blocks, buildings, trees, a park) fills the blocks between the roads. This is decoration with a
  fixed seed, not real map data: the grid is synthetic, so there is no OpenStreetMap data for it;
* main's ``network_geometry`` turns both into the structure the renderer reads;
* signal link indices are mapped back to the *simulation* network's, so a recorded signal state string lights the right heads.

``python -m flowstate.lab.cli mapgeom`` writes the result to ``artifacts/lab/grid-network.json``.
"""

from __future__ import annotations

import json
import math
import random
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .. import sumo_env
from ..network.geometry import network_geometry
from ..rl.network import build_net

NET_ID = "rl_grid"
STREETS = {("ns", 200.0): ("front", "Front Street"), ("ns", 400.0): ("church", "Church Street"), ("ew", 200.0): ("king", "King Street"), ("ew", 400.0): ("queen", "Queen Street")}
CELLS = (-70.0, 200.0, 400.0, 670.0)  # block edges along each axis (the outer cells run past the entry stubs)
ROAD_CLEAR_M = 9.0  # half a street (two 3.2 m lanes) plus its sidewalk and a gap


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[round(x0, 2), round(y0, 2)], [round(x1, 2), round(y0, 2)], [round(x1, 2), round(y1, 2)], [round(x0, 2), round(y1, 2)]]


def _city(root: ET.Element) -> dict[str, Any]:
    rng = random.Random(20260418)
    nodes = {j.get("id"): (float(j.get("x")), float(j.get("y")), j.get("type")) for j in root.iter("junction") if j.get("type") != "internal"}
    edges: dict[str, dict[str, Any]] = {}
    for e in root.iter("edge"):
        if e.get("function"):
            continue
        (x0, y0, _), (x1, y1, _) = nodes[e.get("from")], nodes[e.get("to")]
        axis, pos = ("ns", x0) if abs(x0 - x1) < 1e-6 else ("ew", y0)
        sid, name = STREETS.get((axis, round(pos, 1)), ("", ""))
        kind = "entry" if nodes[e.get("from")][2] == "dead_end" else "exit" if nodes[e.get("to")][2] == "dead_end" else "street"
        edges[e.get("id")] = {"name": name, "street": sid, "cls": "arterial", "kind": kind, "part": "full"}
    blocks, buildings, trees, features = [], [], [], []
    for i in range(3):
        for j in range(3):
            x0, x1 = CELLS[i] + (ROAD_CLEAR_M if i > 0 else 0), CELLS[i + 1] - (ROAD_CLEAR_M if i < 2 else 0)
            y0, y1 = CELLS[j] + (ROAD_CLEAR_M if j > 0 else 0), CELLS[j + 1] - (ROAD_CLEAR_M if j < 2 else 0)
            park = (i, j) == (1, 1)
            use = "park" if park else "commercial" if (i + j) % 2 else "residential"
            blocks.append({"id": f"b{i}{j}", "cell": [i, j], "use": use, "shape": _rect(x0, y0, x1, y1), "outer": i in (0, 2) or j in (0, 2)})
            if park:
                features.append({"kind": "path", "points": [[x0 + 6, (y0 + y1) / 2], [(x0 + x1) / 2, (y0 + y1) / 2 + 14], [x1 - 6, (y0 + y1) / 2]]})
                features.append({"kind": "path", "points": [[(x0 + x1) / 2, y0 + 6], [(x0 + x1) / 2 - 10, (y0 + y1) / 2], [(x0 + x1) / 2, y1 - 6]]})
                features.append({"kind": "pond", "x": (x0 + x1) / 2 + 28, "y": (y0 + y1) / 2 - 24, "rx": 14, "ry": 9})
                for _ in range(46):
                    trees.append([round(rng.uniform(x0 + 5, x1 - 5), 1), round(rng.uniform(y0 + 5, y1 - 5), 1), round(rng.uniform(2.6, 4.4), 1)])
                continue
            # lots: a grid of buildings with setbacks, a few gaps; commercial blocks are denser and taller
            cols, rows = (3, 3) if use == "commercial" else (4, 3) if (i + j) % 3 else (3, 4)
            w, h = (x1 - x0) / cols, (y1 - y0) / rows
            for c in range(cols):
                for r in range(rows):
                    if rng.random() < 0.1:
                        continue
                    sb = rng.uniform(4.0, 7.5)
                    bx0, bx1 = x0 + c * w + sb, x0 + (c + 1) * w - sb * rng.uniform(0.7, 1.1)
                    by0, by1 = y0 + r * h + sb, y0 + (r + 1) * h - sb * rng.uniform(0.7, 1.1)
                    if bx1 - bx0 < 8 or by1 - by0 < 8:
                        continue
                    buildings.append({"shape": _rect(bx0, by0, bx1, by1), "h": round(rng.uniform(14, 62) if use == "commercial" else rng.uniform(7, 19), 1), "use": use, "tone": round(rng.random(), 3)})
            # street trees along the block faces that front a road
            for t in range(int((x1 - x0) // 13)):
                trees.append([round(x0 + 6 + t * 13, 1), round(y0 + 2.2, 1), round(rng.uniform(2.2, 3.2), 1)])
                trees.append([round(x0 + 6 + t * 13, 1), round(y1 - 2.2, 1), round(rng.uniform(2.2, 3.2), 1)])
    labels = [
        {"text": "Front Street", "x": 200.0, "y": 90.0, "angle": 90.0, "cls": "arterial"}, {"text": "Church Street", "x": 400.0, "y": 90.0, "angle": 90.0, "cls": "arterial"},
        {"text": "King Street", "x": 90.0, "y": 200.0, "angle": 0.0, "cls": "arterial"}, {"text": "Queen Street", "x": 90.0, "y": 400.0, "angle": 0.0, "cls": "arterial"},
    ]
    return {"title": "Intelligence Lab grid", "description": "Synthetic 2x2 signalised grid. Blocks, buildings and trees are generated decoration, not map data.",
            "bounds": [-70.0, -70.0, 670.0, 670.0], "roadBounds": [0.0, 0.0, 600.0, 600.0], "focus": [300.0, 300.0], "edges": edges,
            "streets": [{"id": sid, "name": name, "cls": "arterial", "axis": axis, "pos": pos} for (axis, pos), (sid, name) in STREETS.items()],
            "blocks": blocks, "buildings": buildings, "trees": trees, "features": features, "pois": [], "labels": labels, "busStops": [], "busLines": []}


def _sim_links(sim_root: ET.Element) -> tuple[dict[str, list[int]], list[dict[str, Any]]]:
    links: dict[str, list[int]] = {}
    for c in sim_root.iter("connection"):
        if c.get("tl") and not c.get("from", "").startswith(":"):
            links.setdefault(f"{c.get('from')}_{c.get('fromLane')}", []).append(int(c.get("linkIndex")))
    tls = [{"id": t.get("id"), "phases": [{"d": float(p.get("duration")), "s": p.get("state")} for p in t.iter("phase")]} for t in sim_root.iter("tlLogic")]
    return links, tls


def build(out: Path | None = None) -> dict[str, Any]:
    work = Path(tempfile.gettempdir()) / "fs-mapgeom"
    work.mkdir(parents=True, exist_ok=True)
    sim_net = build_net(work)
    display = work / f"{NET_ID}_display.net.xml"
    r = subprocess.run([sumo_env.binary("netconvert"), "-s", str(sim_net), "--sidewalks.guess", "true", "--crossings.guess", "true", "--walkingareas", "true",
                        "--no-turnarounds", "true", "-o", str(display)], capture_output=True, text=True)
    if r.returncode or not display.exists():
        raise RuntimeError(f"netconvert failed: {r.stderr[-400:]}")
    city = _city(ET.parse(display).getroot())
    (work / f"{NET_ID}_display.city.json").write_text(json.dumps(city))
    geom = json.loads(json.dumps(network_geometry(str(display), f"{NET_ID}_display")))  # deep copy: the geometry function is cached
    geom["id"] = NET_ID
    links, tls = _sim_links(ET.parse(sim_net).getroot())
    for e in geom["edges"]:  # signal state strings recorded by the simulation index the *simulation* network's links
        # the display copy has a sidewalk as lane 0, so its k-th car lane is the simulation's lane k
        for k, ln in enumerate(sorted((l for l in e["lanes"] if l["role"] in ("car", "bus")), key=lambda l: l["i"])):
            sim_links = links.get(f"{e['id']}_{k}", [])
            ln["links"] = sim_links
            ln["tls"] = e["to"] if sim_links else None
            ln["control"] = "tls" if sim_links else ln["control"]
    for c in geom["crossings"]:  # pedestrian signals do not exist in the simulation: draw the zebra, not a signal
        c["tls"], c["link"] = None, None
    geom["tls"] = tls
    sim_b = [float(v) for v in ET.parse(sim_net).getroot().find("location").get("convBoundary").split(",")]
    disp_b = [float(v) for v in ET.parse(display).getroot().find("location").get("convBoundary").split(",")]
    if any(math.fabs(a - b) > 25 for a, b in zip(sim_b, disp_b)):  # sidewalks widen the boundary slightly; a shifted origin would misplace every car
        raise RuntimeError(f"display network is not in the simulation's coordinates: {sim_b} vs {disp_b}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(geom, separators=(",", ":")), encoding="utf-8", newline="\n")
    return geom
