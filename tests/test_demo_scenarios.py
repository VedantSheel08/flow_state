"""Demo scenario package: registration, reproducible matched demand, real incidents, manifest and lab API compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowstate.demo.scenarios import ALL_ENTRIES, ALL_EXITS, SPECS
from flowstate.rl.demand import BASE_SCENARIOS, HELD_OUT_SCENARIOS, SCENARIOS, TRAIN_SCENARIOS, VTYPES, intensity_multiplier, write_routes
from flowstate.rl.network import build_net, read_grid

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
MANIFEST = Path(__file__).resolve().parents[1] / "artifacts" / "demo" / "scenario_manifest.json"


@pytest.fixture(scope="module")
def net(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo-net")
    p = build_net(d)
    return d, p, read_grid(p)


def _routes(net, sc, seed, intensity=1.0):
    d, p, g = net
    return write_routes(d / f"{sc}-{seed}.rou.xml", g, p, sc, seed, None, intensity)


def test_demo_scenarios_are_registered_but_never_training_or_held_out():
    for sid in SPECS:
        assert sid in SCENARIOS and SCENARIOS[sid].horizon_s == 900.0
        assert sid not in TRAIN_SCENARIOS and sid not in HELD_OUT_SCENARIOS and sid not in BASE_SCENARIOS
    assert set(BASE_SCENARIOS) == {"balanced", "ns_heavy", "ew_heavy", "pulse", "rush_ramp"}


def test_original_scenarios_demand_is_unchanged(net):
    # golden hashes produced by the unmodified rl.demand.write_routes on task/ml-intelligence
    assert _routes(net, "balanced", 3000)["sha256"] == "38036db5ce7a9225"
    assert _routes(net, "rush_ramp", 3100, 1.18)["sha256"] == "7659d900ea3f2ec2"


@pytest.mark.parametrize("sc", list(SPECS))
def test_demand_is_deterministic_and_seeded(net, sc):
    a, b, c = _routes(net, sc, 4001), _routes(net, sc, 4001), _routes(net, sc, 4002)
    assert a["sha256"] == b["sha256"] and a["scheduled"] == b["scheduled"]
    assert a["sha256"] != c["sha256"]
    assert a["vehicles"] > 600
    assert max(a["scheduled"].values()) <= SPECS[sc].horizon_s
    # intensity multiplies the demand
    assert _routes(net, sc, 4001, 1.3)["vehicles"] > a["vehicles"]


@pytest.mark.parametrize("sc", list(SPECS))
def test_every_route_is_connected_and_ends_at_an_exit(net, sc):
    import re
    import sumolib

    d, p, g = net
    _routes(net, sc, 4001)
    text = (d / f"{sc}-4001.rou.xml").read_text()
    n = sumolib.net.readNet(str(p), withInternal=False)
    for edges in re.findall(r'<route edges="([^"]+)"', text):
        es = edges.split()
        for x, y in zip(es, es[1:]):
            assert n.getEdge(y) in n.getEdge(x).getOutgoing(), (sc, es)
        assert es[0] in ALL_ENTRIES + tuple(i.edge for i in SPECS[sc].incidents)
        assert es[-1] in ALL_EXITS or len(es) == 1  # incident vehicles use a single-edge route


def test_incident_vehicles_are_in_the_route_file_and_manifest(net):
    m = _routes(net, "demo_incident", 4001)
    assert m["incident_vehicles"] == 2 and len(m["incident_ids"]) == 2
    text = (net[0] / "demo_incident-4001.rou.xml").read_text()
    assert text.count("<stop ") == 2 and 'lane="B0B1_0"' in text and 'lane="B0B1_1"' in text
    for sc in ("demo_rush_hour", "demo_spillback"):
        assert _routes(net, sc, 4001)["incident_vehicles"] == 0


def test_spillback_scenario_is_directionally_and_turn_asymmetric(net):
    import re

    d, p, g = net
    _routes(net, "demo_spillback", 4001)
    routes = re.findall(r'<route edges="([^"]+)"', (d / "demo_spillback-4001.rou.xml").read_text())
    by_entry: dict[str, int] = {}
    for r in routes:
        by_entry[r.split()[0]] = by_entry.get(r.split()[0], 0) + 1
    assert by_entry["bottom1B0"] > 2 * by_entry["bottom0A0"]  # heavy northbound stream into B0-B1
    to_b1_top = sum(1 for r in routes if r.split()[0] == "bottom1B0" and r.split()[-1] == "B1top1")
    assert to_b1_top / by_entry["bottom1B0"] > 0.8  # almost all of it turns into the B0-B1 link


def test_seed_demand_multiplier_is_the_labs():
    assert 0.85 <= intensity_multiplier(4001) <= 1.15


@pytest.mark.parametrize("sc,controller", [("demo_rush_hour", "fixed_time"), ("demo_spillback", "queue_heuristic"), ("demo_incident", "fixed_time")])
def test_short_sumo_run_is_physical_and_incident_really_stops(sc, controller):
    from flowstate.demo.runner import run_demo_episode

    r = run_demo_episode(controller, sc, 4001, horizon=300, drain=0)
    m = r["metrics"]
    assert m["vehicles_scheduled"] > 150 and m["collisions"] == 0 and m["teleports"] == 0
    assert r["series"][-1]["inNet"] > 50  # demand is already high within 5 minutes
    d = r["demand"]
    assert d["unaccounted"] == 0 and d["scheduled"] == d["completed"] + d["onRoadAtEnd"] + d["neverInserted"]
    if sc == "demo_incident":
        assert len(r["incident"]) == 2
        for inc, spec in zip(sorted(r["incident"], key=lambda i: i["scheduled_s"]), sorted(SPECS[sc].incidents, key=lambda i: i.t_s)):
            assert inc["scheduled_s"] <= inc["stoppedFrom_s"] <= inc["scheduled_s"] + 60  # a real stop, close to its scheduled time
            assert inc["stoppedTo_s"] >= min(299, inc["stoppedFrom_s"] + 1)


def test_matched_demand_across_controllers_in_a_short_run():
    from flowstate.demo.runner import run_demo_episode

    a = run_demo_episode("fixed_time", "demo_spillback", 4001, horizon=120, drain=0, series_detail=False)
    b = run_demo_episode("queue_heuristic", "demo_spillback", 4001, horizon=120, drain=0, series_detail=False)
    assert a["demandSha"] == b["demandSha"] and a["demand"]["scheduled"] == b["demand"]["scheduled"]


def test_analysis_rules_on_a_synthetic_series():
    from flowstate.demo.analysis import _first

    ts = [0, 5, 10, 15, 20, 25]
    assert _first(ts, [False, True, True, True, False, True], hold=3) == 5
    assert _first(ts, [True, True, False, True, True, False], hold=3) is None
    assert _first(ts, [True] * 6, hold=2, start=10) == 10


@pytest.mark.skipif(not MANIFEST.exists(), reason="manifest not built")
def test_manifest_matches_specs_and_lab_api_shapes(net):
    from flowstate.lab.app import PairRequest, app
    from fastapi.testclient import TestClient

    doc = json.loads(MANIFEST.read_text())
    assert doc["schema"] == "flowstate.demo-scenarios/1" and doc["defaultScenario"] in SPECS
    assert [s["id"] for s in doc["scenarios"]] == list(SPECS) and doc["recommendedSequence"] == list(SPECS)
    for s in doc["scenarios"]:
        spec = SPECS[s["id"]]
        assert s["title"] == spec.title and s["description"] == spec.description
        req = PairRequest(**s["lab"]["pairRequest"])  # a valid body for POST /api/lab/replays
        assert req.scenario in SCENARIOS and req.seed == s["lab"]["seed"] and req.intensity == s["lab"]["intensity"]
        assert s["lab"]["liveStart"]["scenario"] == s["id"] and s["lab"]["liveStart"]["cmd"] == "start"
        assert s["lab"]["horizonS"] == spec.horizon_s and s["demand"]["matchedAcrossControllers"]
        assert s["lab"]["seed"] in s["seeds"]["benchmark"] and not set(s["seeds"]["design"]) & set(s["seeds"]["benchmark"])
        # the manifest's demand hash is reproducible from the code alone
        assert _routes(net, s["id"], s["lab"]["seed"], s["lab"]["intensity"])["sha256"] == s["demand"]["demandSha256"]
        kinds = {m["kind"] for m in s["markers"]["illustrative"]["queue_heuristic"]}
        assert {"spillback", "recovery"} <= kinds
        assert all(0 <= m["tSec"] <= 1500 for c in s["markers"]["illustrative"].values() for m in c)
        assert "teleports" in s["benchmark"]["byController"]["fixed_time"] and "collisions" in s["illustrative"]["byController"]["ml_forecast"]
    c = TestClient(app)
    assert c.get("/api/lab/demo-scenarios").json()["schema"] == doc["schema"]
    listed = {x["id"] for x in c.get("/api/lab/status").json()["scenarios"]}
    assert set(SPECS) <= listed
