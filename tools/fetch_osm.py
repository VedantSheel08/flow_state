"""Download the OpenStreetMap extracts behind the ``downtown`` network.

Two extracts are committed so network builds stay offline and reproducible;
rerun this only to refresh them.

* ``toronto_king_west.json``: the street lines the simulated network is built from.
* ``toronto_king_west_context.json``: buildings, parks, land use and trees drawn
  between and around those streets (map context only, never simulated).

    .venv/Scripts/python tools/fetch_osm.py            # both
    .venv/Scripts/python tools/fetch_osm.py context    # context only
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OSM_DIR = Path(__file__).resolve().parents[1] / "data" / "osm"
ENDPOINT = "https://overpass-api.de/api/interpreter"

# south, west, north, east: Queen St W to Front St W, Spadina Ave to Simcoe St (+ margin)
BBOX = (43.6405, -79.4000, 43.6528, -79.3845)
QUERY = """
[out:json][timeout:60];
(
  way["highway"~"^(primary|secondary|tertiary|residential|unclassified)$"]({s},{w},{n},{e});
  node["highway"="traffic_signals"]({s},{w},{n},{e});
  node["highway"="stop"]({s},{w},{n},{e});
);
out body;
>;
out skel qt;
"""

# The whole drawn map (network bounds plus ~150 m) in the rotated grid frame, back in lat/lon.
CONTEXT_BBOX = (43.6394, -79.3992, 43.6547, -79.3795)
CONTEXT_QUERY = """
[out:json][timeout:120];
(
  way["building"]({s},{w},{n},{e});
  relation["building"]({s},{w},{n},{e});
  way["leisure"~"^(park|garden|pitch|playground)$"]({s},{w},{n},{e});
  way["landuse"~"^(grass|recreation_ground|commercial|retail|residential)$"]({s},{w},{n},{e});
  way["amenity"~"^(school|hospital|parking)$"]({s},{w},{n},{e});
  way["place"="square"]({s},{w},{n},{e});
  way["highway"="pedestrian"]["area"="yes"]({s},{w},{n},{e});
  node["natural"="tree"]({s},{w},{n},{e});
);
out body;
>;
out skel qt;
"""

EXTRACTS = {
    "roads": ("toronto_king_west.json", BBOX, QUERY),
    "context": ("toronto_king_west_context.json", CONTEXT_BBOX, CONTEXT_QUERY),
}


def fetch(name: str) -> None:
    file, bbox, query = EXTRACTS[name]
    s, w, n, e = bbox
    q = query.format(s=s, w=w, n=n, e=e)
    req = urllib.request.Request(ENDPOINT, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "flowstate-network-builder"})
    with urllib.request.urlopen(req, timeout=150) as resp:
        data = json.load(resp)
    out = OSM_DIR / file
    out.parent.mkdir(parents=True, exist_ok=True)
    data["bbox"] = list(bbox)
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    ways = sum(1 for el in data["elements"] if el["type"] == "way")
    print(f"wrote {out} ({ways} ways, {len(data['elements'])} elements)")


def main() -> int:
    for name in sys.argv[1:] or list(EXTRACTS):
        fetch(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
