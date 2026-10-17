"""Render geometry for the map, extracted from the compiled SUMO network.

Everything the frontend draws as infrastructure comes from here: lane centre
lines and widths, junction polygons, pedestrian crossings and walking areas
exactly as ``netconvert`` built them, plus per-lane turn movements, stop
control and signal link indices. The city sidecar (blocks, buildings, labels,
POIs) is merged in so the client needs a single request.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any


def _shape(s: str | None) -> list[float]:
    if not s:
        return []
    out: list[float] = []
    for pt in s.split():
        x, y = pt.split(",")[:2]
        out.extend((round(float(x), 2), round(float(y), 2)))
    return out


def _role(allow: str | None, disallow: str | None) -> str:
    if disallow == "all":
        return "median"
    classes = set((allow or "").split())
    if classes == {"pedestrian"}:
        return "sidewalk"
    if classes == {"bicycle"}:
        return "bike"
    if classes and classes <= {"bus"}:
        return "bus"
    return "car"


@lru_cache(maxsize=8)
def network_geometry(net_path: str, network_id: str) -> dict[str, Any]:
    root = ET.parse(net_path).getroot()
    city: dict[str, Any] = {}
    city_path = Path(net_path).with_name(f"{network_id}.city.json")
    if city_path.exists():
        city = json.loads(city_path.read_text(encoding="utf-8"))
    edge_meta = city.get("edges", {})

    junction_type = {j.get("id"): j.get("type") for j in root.iter("junction")}

    # connections: per from-lane movements and signal links
    lane_dirs: dict[str, set[str]] = {}
    lane_links: dict[str, list[int]] = {}
    lane_tls: dict[str, str] = {}
    lane_state: dict[str, str] = {}
    crossing_link: dict[str, tuple[str, int]] = {}
    for c in root.iter("connection"):
        frm, to = c.get("from", ""), c.get("to", "")
        if frm.startswith(":"):
            continue
        if to.startswith(":") and "_c" in to:
            continue
        if to.startswith(":"):  # walkingarea connection of a sidewalk
            continue
        lane = f"{frm}_{c.get('fromLane')}"
        lane_dirs.setdefault(lane, set()).add(c.get("dir", "s"))
        lane_state[lane] = c.get("state", "")
        if c.get("tl"):
            lane_tls[lane] = c.get("tl")
            lane_links.setdefault(lane, []).append(int(c.get("linkIndex")))
    for c in root.iter("connection"):
        to = c.get("to", "")
        if c.get("tl") and to.startswith(":") and "_c" in to:
            crossing_link[to] = (c.get("tl"), int(c.get("linkIndex")))

    edges: list[dict[str, Any]] = []
    crossings: list[dict[str, Any]] = []
    walkingareas: list[dict[str, Any]] = []
    for e in root.iter("edge"):
        eid = e.get("id")
        fn = e.get("function")
        if fn == "crossing":
            lane = e.find("lane")
            tl = crossing_link.get(eid)
            crossings.append({
                "id": eid, "w": float(lane.get("width", "4")), "shape": _shape(lane.get("shape")),
                "tls": tl[0] if tl else None, "link": tl[1] if tl else None,
                "junction": eid[1:].rsplit("_c", 1)[0],
            })
            continue
        if fn == "walkingarea":
            lane = e.find("lane")
            walkingareas.append({"id": eid, "shape": _shape(lane.get("shape"))})
            continue
        if fn == "internal":
            continue
        meta = edge_meta.get(eid, {})
        to_type = junction_type.get(e.get("to"), "")
        lanes = []
        for ln in e.iter("lane"):
            lid = ln.get("id")
            role = _role(ln.get("allow"), ln.get("disallow"))
            state = lane_state.get(lid, "")
            if lid in lane_tls:
                control = "tls"
            elif to_type == "allway_stop" or state == "w":
                control = "stop"
            elif state == "s":
                control = "stop"
            elif state in ("m", "="):
                control = "yield"
            else:
                control = None
            lanes.append({
                "id": lid, "i": int(ln.get("index")), "w": float(ln.get("width", "3.2")), "role": role,
                "shape": _shape(ln.get("shape")), "len": round(float(ln.get("length")), 2),
                "dirs": "".join(sorted(lane_dirs.get(lid, set()))),
                "control": control if role in ("car", "bike", "bus") and to_type not in ("dead_end", "priority") else None,
                "tls": lane_tls.get(lid), "links": lane_links.get(lid, []),
            })
        edges.append({
            "id": eid, "from": e.get("from"), "to": e.get("to"), "name": e.get("name") or meta.get("name", ""),
            "street": meta.get("street", ""), "cls": meta.get("cls", e.get("type", "")), "kind": meta.get("kind", "street"),
            "part": meta.get("part", "full"), "lanes": lanes,
        })

    junctions = []
    names = {k: v for k, v in city.get("junctions", {}).items()}
    for j in root.iter("junction"):
        if j.get("type") == "internal":
            continue
        jid = j.get("id")
        junctions.append({
            "id": jid, "type": j.get("type"), "x": float(j.get("x")), "y": float(j.get("y")),
            "shape": _shape(j.get("shape")), "name": names.get(jid, {}).get("name"),
        })

    tls = []
    for t in root.iter("tlLogic"):
        phases = [{"d": float(p.get("duration")), "s": p.get("state")} for p in t.iter("phase")]
        tls.append({"id": t.get("id"), "phases": phases})

    return {
        "id": network_id,
        "title": city.get("title", network_id),
        "description": city.get("description", ""),
        "bounds": city.get("bounds"),
        "roadBounds": city.get("roadBounds"),
        "focus": city.get("focus"),
        "edges": edges,
        "junctions": junctions,
        "crossings": crossings,
        "walkingareas": walkingareas,
        "tls": tls,
        "city": {k: city.get(k, []) for k in ("streets", "blocks", "buildings", "trees", "features", "pois", "labels", "busStops", "busLines")},
    }
