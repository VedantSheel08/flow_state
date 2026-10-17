"""Download the OpenStreetMap extract behind the ``downtown`` network.

The extract is committed (``data/osm/toronto_king_west.json``) so network
builds stay offline and reproducible; rerun this only to refresh it.

    .venv/Scripts/python tools/fetch_osm.py
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# south, west, north, east: Queen St W to Front St W, Spadina Ave to Simcoe St (+ margin)
BBOX = (43.6405, -79.4000, 43.6528, -79.3845)
OUT = Path(__file__).resolve().parents[1] / "data" / "osm" / "toronto_king_west.json"
ENDPOINT = "https://overpass-api.de/api/interpreter"

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


def main() -> int:
    s, w, n, e = BBOX
    q = QUERY.format(s=s, w=w, n=n, e=e)
    req = urllib.request.Request(ENDPOINT, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "flowstate-network-builder"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.load(resp)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data["bbox"] = list(BBOX)
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    ways = sum(1 for el in data["elements"] if el["type"] == "way")
    print(f"wrote {OUT} ({ways} ways, {len(data['elements'])} elements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
