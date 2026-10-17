"""Toronto civic data: observed peak-hour intersection volumes, as a *plausibility reference* for the simulated district.

Dataset: City of Toronto Open Data, "Traffic Volumes at Intersections for All Modes" (multimodal Turning Movement Counts, TMC),
https://open.toronto.ca/dataset/traffic-volumes-at-intersections-for-all-modes/ , served by the portal's CKAN API.
Resource used: ``tmc_summary_data`` (one row per count: AM/PM peak-hour motor-vehicle and bicycle volumes, heavy-vehicle share,
pedestrians, approach totals). Field meanings are quoted from the dataset's own field notes.

What the comparison is for. FlowState's district is a *synthetic* four-street grid, not a model of any Toronto location. The
question this answers is narrower and checkable: *is the simulated rush-hour demand at its busiest intersection in the same range
as what the City counted at signalised downtown intersections in their peak hour, and are the modelled shares of cyclists and
heavy vehicles realistic?* It is a sanity band for parameters, not a calibration and not evidence that FlowState predicts any
real intersection.

Limits that matter: counts are one weekday per location (8 h before September 2023, 14 h after), downtown is defined here as a
bounding box, the "peak hour" is each count's own busiest hour, and a simulated 5-minute window scaled to an hour is not an
hourly count. Licence: the portal lists none in the dataset metadata; the City publishes its open data under the Open
Government Licence - Toronto (https://open.toronto.ca/open-data-licence/). Keep the attribution when redistributing the snapshot.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .store import ROOT

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action"
PACKAGE_ID = "traffic-volumes-at-intersections-for-all-modes"
SUMMARY_RESOURCE_ID = "1364bffa-29a3-4c39-af8a-925d8ca7bf1f"  # tmc_summary_data (resolve again via package_show if the portal retires it)
DATASET_PAGE = f"https://open.toronto.ca/dataset/{PACKAGE_ID}/"
LICENCE_URL = "https://open.toronto.ca/open-data-licence/"
SNAPSHOT = ROOT / "data" / "toronto" / "tmc_downtown_peak_context.json"
# lat_min, lat_max, lon_min, lon_max: roughly Queen's Quay to north of Bloor, Bathurst to the Don (an assumption; change and rebuild)
DOWNTOWN_BBOX = (43.640, 43.670, -79.410, -79.360)
SINCE = "2019-01-01"
FIELDS = ["count_id", "count_date", "location_name", "latitude", "longitude", "centreline_type", "centreline_id", "px", "count_duration", "total_vehicle",
          "total_bike", "total_heavy_pct", "total_pedestrian", "am_peak_start", "am_peak_vehicle", "am_peak_bike", "am_peak_heavy_pct", "pm_peak_start",
          "pm_peak_vehicle", "pm_peak_bike", "pm_peak_heavy_pct", "n_appr_vehicle", "e_appr_vehicle", "s_appr_vehicle", "w_appr_vehicle"]
HEADERS = {"User-Agent": "flowstate-hackathon/1.0 (traffic research)"}


def fetch_summary(client: httpx.Client | None = None, page: int = 10000) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """All rows of ``tmc_summary_data`` (paged ``datastore_search``) plus provenance: URL, time, record count and a hash of the pages."""
    own = client is None
    client = client or httpx.Client(timeout=90, headers=HEADERS)
    rows: list[dict[str, Any]] = []
    h = hashlib.sha256()
    try:
        meta = client.get(f"{CKAN}/package_show", params={"id": PACKAGE_ID}).json()["result"]
        offset, total = 0, None
        while total is None or offset < total:
            r = client.get(f"{CKAN}/datastore_search", params={"resource_id": SUMMARY_RESOURCE_ID, "limit": page, "offset": offset, "fields": ",".join(FIELDS)})
            r.raise_for_status()
            h.update(r.content)
            res = r.json()["result"]
            total = res["total"]
            rows += res["records"]
            offset += page
    finally:
        if own:
            client.close()
    return rows, {"retrievedAt": datetime.now(timezone.utc).isoformat(), "packageId": PACKAGE_ID, "resourceId": SUMMARY_RESOURCE_ID, "datasetPage": DATASET_PAGE,
                  "api": f"{CKAN}/datastore_search?resource_id={SUMMARY_RESOURCE_ID}&limit={page}&offset=<n>&fields=<fields>", "recordsTotal": len(rows),
                  "pagesSha256": h.hexdigest(), "datasetLastModified": meta.get("metadata_modified"), "licenceNote": f"portal lists no licence; City open data licence: {LICENCE_URL}"}


def downtown_signalised(rows: list[dict[str, Any]], bbox=DOWNTOWN_BBOX, since: str = SINCE) -> list[dict[str, Any]]:
    """Most recent count per signalised intersection inside ``bbox`` with both peak hours recorded."""
    la0, la1, lo0, lo1 = bbox
    best: dict[Any, dict[str, Any]] = {}
    for r in rows:
        try:
            ok = (la0 <= r["latitude"] <= la1 and lo0 <= r["longitude"] <= lo1 and r["px"] is not None and str(r["count_date"]) >= since
                  and r["am_peak_vehicle"] and r["pm_peak_vehicle"] and str(r.get("centreline_type")) == "2")
        except (TypeError, KeyError):
            continue
        if ok:
            k = r["centreline_id"]
            if k not in best or str(r["count_date"]) > str(best[k]["count_date"]):
                best[k] = r
    return sorted(best.values(), key=lambda r: -max(r["am_peak_vehicle"], r["pm_peak_vehicle"]))


def _q(xs: list[float]) -> dict[str, float]:
    xs = sorted(xs)
    n = len(xs)

    def at(p: float) -> float:
        i = p * (n - 1)
        lo, hi = int(i), min(n - 1, int(i) + 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)

    return {"n": n, "min": xs[0], "p10": at(0.10), "p25": at(0.25), "p50": at(0.50), "p75": at(0.75), "p90": at(0.90), "max": xs[-1], "mean": statistics.fmean(xs)}


def analyse(sites: list[dict[str, Any]]) -> dict[str, Any]:
    peak = [max(r["am_peak_vehicle"], r["pm_peak_vehicle"]) for r in sites]
    bike_share = [100.0 * (r["am_peak_bike"] + r["pm_peak_bike"]) / (r["am_peak_vehicle"] + r["pm_peak_vehicle"] + r["am_peak_bike"] + r["pm_peak_bike"]) for r in sites
                  if r.get("am_peak_bike") is not None and r.get("pm_peak_bike") is not None]
    heavy = [100.0 * (r["am_peak_heavy_pct"] + r["pm_peak_heavy_pct"]) / 2 for r in sites if r.get("am_peak_heavy_pct") is not None and r.get("pm_peak_heavy_pct") is not None]
    return {
        "intersections": len(sites),
        "peakHourMotorVehicles": {"am": _q([r["am_peak_vehicle"] for r in sites]), "pm": _q([r["pm_peak_vehicle"] for r in sites]), "busierOfAmPm": _q(peak),
                                  "unit": "vehicles per hour entering the intersection, busiest continuous hour of the count (AM window 6:30-10:30, PM 2:00-8:00)"},
        "bicycleShareOfPeakTrafficPct": _q(bike_share) if bike_share else None,
        "heavyVehiclePctOfMotorVehicles": _q(heavy) if heavy else None,
        "pedestriansOverCountDuration": _q([r["total_pedestrian"] for r in sites if r.get("total_pedestrian") is not None]),
    }


def build_snapshot(path: Path | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch the live dataset and write a small, attributed, hash-stamped snapshot (the rows used and the statistics)."""
    rows, prov = fetch_summary(client)
    sites = downtown_signalised(rows)
    doc = {
        "schemaVersion": 1, "source": prov, "selection": {"bbox": dict(zip(("latMin", "latMax", "lonMin", "lonMax"), DOWNTOWN_BBOX)), "countDateSince": SINCE,
                                                      "rule": "most recent count per signalised intersection (px not null, centreline_type 2) with AM and PM peak volumes"},
        "statistics": analyse(sites),
        "intersections": [{k: r.get(k) for k in ("count_id", "count_date", "location_name", "latitude", "longitude", "px", "am_peak_vehicle", "pm_peak_vehicle", "am_peak_bike",
                                                  "pm_peak_bike", "am_peak_heavy_pct", "pm_peak_heavy_pct", "total_pedestrian", "count_duration")} for r in sites],
        "attribution": "Contains information licensed under the Open Government Licence - Toronto. City of Toronto, Transportation Services, Turning Movement Counts.",
        "limitations": ["One weekday count per location; the peak hour is each count's own busiest hour.", "Downtown is a bounding box, not a planning boundary.",
                        "FlowState's district is synthetic: this is a plausibility band for demand and mode shares, not a calibration.",
                        "Motor vehicles = cars, trucks and buses entering from all approaches; bicycles counted separately."],
    }
    out = Path(path) if path else SNAPSHOT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def load_snapshot(path: Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or SNAPSHOT).read_text(encoding="utf-8"))


def percentile_rank(value: float, sites: list[dict[str, Any]], key: str = "peak") -> float:
    """Share (0..100) of the observed intersections whose peak-hour volume is at or below ``value``."""
    vols = sorted(max(r["am_peak_vehicle"], r["pm_peak_vehicle"]) for r in sites) if key == "peak" else sorted(r[key] for r in sites)
    return 100.0 * sum(v <= value for v in vols) / len(vols)


def plausibility(export: dict[str, Any], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Place a simulated experiment against the observed peak-hour distribution.

    Uses the experiment's busiest-junction discharge (vehicles per hour, extrapolated from the observation window) and the
    cyclist / heavy-vehicle shares of the trips completed in it. Everything is labelled with how it was derived.
    """
    snap = snapshot or load_snapshot()
    sites = snap["intersections"]
    extra = (export.get("extra") or {})
    busy = (extra.get("busiestJunctions") or [])
    out: dict[str, Any] = {"observed": {"source": snap["source"]["datasetPage"], "intersections": snap["statistics"]["intersections"], "retrievedAt": snap["source"]["retrievedAt"]},
                           "caveat": "Plausibility band only: the district is synthetic, and a simulated window scaled to an hour is not an hourly count."}
    if busy:
        v = busy[0]["vehPerHour"]
        st = snap["statistics"]["peakHourMotorVehicles"]["busierOfAmPm"]
        out["busiestJunction"] = {"name": busy[0]["name"], "simulatedVehPerHour": v, "observedMedianPeakHour": st["p50"], "observedP25": st["p25"], "observedP75": st["p75"],
                                  "percentileRankInObserved": round(percentile_rank(v, sites), 1), "ratioToObservedMedian": round(v / st["p50"], 2),
                                  "reading": ("below the observed interquartile range" if v < st["p25"] else "within the observed interquartile range" if v <= st["p75"] else "above the observed interquartile range")}
    modes = extra.get("tripsByMode") or {}
    motor = sum(n for m, n in modes.items() if m in ("car", "truck", "bus"))
    if motor:
        bikes = modes.get("bike", 0)
        heavy = modes.get("truck", 0) + modes.get("bus", 0)
        b = snap["statistics"]["bicycleShareOfPeakTrafficPct"]
        h = snap["statistics"]["heavyVehiclePctOfMotorVehicles"]
        out["bicycleSharePct"] = {"simulated": round(100.0 * bikes / (motor + bikes), 1), "observedMedian": round(b["p50"], 1), "observedP25": round(b["p25"], 1), "observedP75": round(b["p75"], 1)} if b else None
        out["heavyVehiclePct"] = {"simulated": round(100.0 * heavy / motor, 1), "observedMedian": round(h["p50"], 1), "observedP25": round(h["p25"], 1), "observedP75": round(h["p75"], 1)} if h else None
    return out
