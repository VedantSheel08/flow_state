"""Separate native collision records, pairs, road users and geometric overlap.

Reconstruction uses rounded SUMO collision-output coordinates and generated
vehicle widths. It is supporting evidence, not an independent physics solver.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def rectangle(front, back, width):
    fx, fy = map(float, front.split(","))
    bx, by = map(float, back.split(","))
    length = math.hypot(fx - bx, fy - by)
    if length == 0:
        raise ValueError("zero length vehicle segment")
    nx, ny = -(fy - by) / length * width / 2, (fx - bx) / length * width / 2
    return [(fx + nx, fy + ny), (fx - nx, fy - ny), (bx - nx, by - ny), (bx + nx, by + ny)]


def overlap_depth(first, second):
    depths = []
    for polygon in (first, second):
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            axis = (-dy / length, dx / length)
            projections = [[x * axis[0] + y * axis[1] for x, y in poly] for poly in (first, second)]
            depths.append(min(max(p) for p in projections) - max(min(p) for p in projections))
    return min(depths)


def audit(work, network):
    widths = {e.get("id"): float(e.get("width", "1.8")) for e in ET.parse(work / "vtypes.add.xml").getroot() if e.tag == "vType"}
    links = list(ET.parse(network).getroot().iter("connection"))
    records = []
    for e in ET.parse(work / "collisions.xml").getroot():
        record = dict(e.attrib)
        record["connections"] = [dict(link.attrib) for link in links if link.get("via") == e.get("lane")]
        types = [e.get("colliderType", ""), e.get("victimType", "")]
        record["roadUsers"] = "pedestrian conflict" if "walker" in types or "person" in e.get("type", "") else "vehicle conflict (includes bicycles)"
        if all(t in widths for t in types) and all(e.get(k) for k in ("colliderFront", "colliderBack", "victimFront", "victimBack")):
            depth = overlap_depth(rectangle(e.get("colliderFront"), e.get("colliderBack"), widths[types[0]]),
                                  rectangle(e.get("victimFront"), e.get("victimBack"), widths[types[1]]))
            record["reconstructedOverlapMeters"] = round(depth, 4)
            record["geometryEvidence"] = "body overlap" if depth > .03 else "separated bodies" if depth < -.03 else "near contact; rounded coordinates are inconclusive"
        records.append(record)
    return {"nativeCollisionRecords": len(records), "uniquePairs": len({tuple(sorted((r["collider"], r["victim"]))) for r in records}),
            "byType": dict(Counter(r["type"] for r in records)), "records": records}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(audit(args.work, args.network), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
