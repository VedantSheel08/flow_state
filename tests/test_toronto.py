"""Toronto open-data context: selection logic on synthetic rows, the pinned snapshot, and the live portal's provenance.

The live test talks to the real City of Toronto CKAN API; it is skipped (and reported skipped) only if the portal cannot be reached.
"""

import json
from pathlib import Path

import httpx
import pytest

from flowstate.experiments import toronto as TR


def row(cid, name, lat, lon, date, am, pm, px=1, ctype="2", ambike=50, pmbike=60, amheavy=0.03, pmheavy=0.04, ped=1000):
    return dict(count_id=cid, count_date=date, location_name=name, latitude=lat, longitude=lon, centreline_type=ctype, centreline_id=cid // 10, px=px, count_duration="14",
                total_vehicle=(am or 0) + (pm or 0), total_bike=ambike + pmbike, total_heavy_pct=0.03, total_pedestrian=ped, am_peak_start="x", am_peak_vehicle=am, am_peak_bike=ambike,
                am_peak_heavy_pct=amheavy, pm_peak_start="x", pm_peak_vehicle=pm, pm_peak_bike=pmbike, pm_peak_heavy_pct=pmheavy, n_appr_vehicle=1, e_appr_vehicle=1, s_appr_vehicle=1, w_appr_vehicle=1)


def test_selection_keeps_the_latest_signalised_downtown_count_per_intersection():
    rows = [
        row(11, "A / B", 43.650, -79.38, "2020-05-01", 1000, 1200),
        row(12, "A / B", 43.650, -79.38, "2024-05-01", 1100, 1500),  # same centreline id (12//10 == 11//10 == 1): the later count wins
        row(21, "C / D", 43.700, -79.38, "2024-05-01", 900, 900),  # outside the bounding box
        row(31, "E / F", 43.652, -79.39, "2024-05-01", 800, 900, px=None),  # not signalised
        row(41, "G / H", 43.655, -79.37, "2024-05-01", 700, 800, ctype="1"),  # midblock, not an intersection
        row(51, "I / J", 43.655, -79.37, "2017-05-01", 700, 800),  # too old
        row(61, "K / L", 43.655, -79.37, "2024-05-01", None, 800),  # no AM peak recorded
        row(71, "M / N", 43.645, -79.40, "2025-01-01", 1700, 1300),
    ]
    sites = TR.downtown_signalised(rows)
    assert [s["location_name"] for s in sites] == ["M / N", "A / B"]  # busiest first
    assert sites[1]["count_id"] == 12 and sites[1]["am_peak_vehicle"] == 1100


def test_statistics_and_percentile_rank_are_what_they_say():
    sites = [row(i * 10, f"S{i}", 43.65, -79.38, "2024-01-01", 500 + 100 * i, 400 + 100 * i, ambike=10 * i, pmbike=10 * i) for i in range(1, 11)]
    st = TR.analyse(sites)
    assert st["intersections"] == 10
    busier = st["peakHourMotorVehicles"]["busierOfAmPm"]
    assert busier["min"] == 600 and busier["max"] == 1500 and busier["p50"] == pytest.approx(1050)
    assert TR.percentile_rank(1050, sites) == pytest.approx(50.0) and TR.percentile_rank(10, sites) == 0.0 and TR.percentile_rank(99999, sites) == 100.0
    assert st["heavyVehiclePctOfMotorVehicles"]["p50"] == pytest.approx(3.5)  # fractions in the dataset are shown as percent


def test_plausibility_places_a_simulation_against_the_observed_band():
    snap = {"source": {"datasetPage": "https://x", "retrievedAt": "t"}, "statistics": TR.analyse([row(i * 10, f"S{i}", 43.65, -79.38, "2024-01-01", 600 + 100 * i, 500 + 100 * i) for i in range(1, 11)]),
            "intersections": [row(i * 10, f"S{i}", 43.65, -79.38, "2024-01-01", 600 + 100 * i, 500 + 100 * i) for i in range(1, 11)]}
    export = {"extra": {"busiestJunctions": [{"tls": "n1_1", "name": "Main Street & King Street", "vehPerHour": 900}], "tripsByMode": {"car": 180, "truck": 8, "bus": 2, "bike": 10}}}
    p = TR.plausibility(export, snap)
    assert p["busiestJunction"]["simulatedVehPerHour"] == 900 and p["busiestJunction"]["percentileRankInObserved"] == 30.0
    assert p["busiestJunction"]["reading"] == "below the observed interquartile range"
    assert p["bicycleSharePct"]["simulated"] == pytest.approx(100 * 10 / 200, abs=0.1) and p["heavyVehiclePct"]["simulated"] == pytest.approx(100 * 10 / 190, abs=0.1)
    assert "synthetic" in p["caveat"]
    assert "busiestJunction" not in TR.plausibility({"extra": {}}, snap)  # nothing is invented when the experiment did not record it


SNAP = Path(TR.SNAPSHOT)


def test_the_committed_snapshot_is_attributed_hash_stamped_and_plausible():
    snap = TR.load_snapshot()
    src = snap["source"]
    assert src["resourceId"] == TR.SUMMARY_RESOURCE_ID and src["packageId"] == TR.PACKAGE_ID and len(src["pagesSha256"]) == 64 and src["recordsTotal"] > 25000
    assert "Open Government Licence" in snap["attribution"] and snap["limitations"]
    st = snap["statistics"]
    assert st["intersections"] == len(snap["intersections"]) >= 150
    q = st["peakHourMotorVehicles"]["busierOfAmPm"]
    assert 500 < q["p25"] < q["p50"] < q["p75"] < q["max"] < 6000  # veh/h entering an intersection in its busiest hour
    b, h = st["bicycleShareOfPeakTrafficPct"], st["heavyVehiclePctOfMotorVehicles"]
    assert 2 < b["p50"] < 30 and 1 < h["p50"] < 10
    names = [i["location_name"] for i in snap["intersections"]]
    assert any("Spadina" in n for n in names) or any("University" in n for n in names)  # real downtown arterials are present


def test_the_live_portal_serves_the_resource_the_snapshot_cites():
    try:
        with httpx.Client(timeout=40, headers=TR.HEADERS) as c:
            pkg = c.get(f"{TR.CKAN}/package_show", params={"id": TR.PACKAGE_ID}).json()["result"]
            res = c.get(f"{TR.CKAN}/datastore_search", params={"resource_id": TR.SUMMARY_RESOURCE_ID, "limit": 1}).json()["result"]
    except httpx.HTTPError as exc:
        pytest.skip(f"Toronto open data portal unreachable: {exc}")
    assert any(r["id"] == TR.SUMMARY_RESOURCE_ID for r in pkg["resources"]) and "Turning Movement" in pkg["title"]
    fields = {f["id"] for f in res["fields"]}
    assert set(TR.FIELDS) <= fields and res["total"] > 25000
    snap = TR.load_snapshot()
    assert abs(res["total"] - snap["source"]["recordsTotal"]) < 2000  # the portal grows slowly; a large gap means the snapshot is stale or the dataset changed shape


BENCH = Path(__file__).resolve().parents[1] / "data" / "benchmarks" / "v2"


@pytest.mark.skipif(not (BENCH / "toronto-rush-preset" / "manifest.json").exists(), reason="benchmark dataset not generated")
def test_the_simulated_rush_hour_is_placed_against_the_observed_intersection_volumes():
    """Real SUMO runs of the contract rush preset, compared with the City's counts (committed benchmark data)."""
    manifest = json.loads((BENCH / "toronto-rush-preset" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scenario"] == "rush_hour" and len(manifest["seeds"]) >= 3
    snap = TR.load_snapshot()
    for e in manifest["experiments"]:
        doc = json.loads((BENCH / "toronto-rush-preset" / e["file"]).read_text(encoding="utf-8"))
        p = TR.plausibility(doc, snap)
        b = p["busiestJunction"]
        assert 200 < b["simulatedVehPerHour"] < 4000 and 0 <= b["percentileRankInObserved"] <= 100 and b["ratioToObservedMedian"] > 0
        assert p["bicycleSharePct"]["simulated"] >= 0 and p["heavyVehiclePct"]["observedMedian"] > 0
