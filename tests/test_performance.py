"""Prepared live-state and measurement contracts, with genuine SUMO stepping."""
import json
from pathlib import Path
import shutil
import time

import pytest

from flowstate.performance.identity import identity
from flowstate.performance.prepared import PreparedRun
from flowstate.performance.resources import sample
from flowstate.performance.congestion import resolve, list_presets
from flowstate.sim.safety import SafetyObservations


def test_native_resource_measurement():
    resource = sample()
    assert resource["available"]
    assert resource["cpuSeconds"] >= 0
    assert resource["peakRssBytes"] > 0


def test_resource_identity_detects_calibration_data_changes(tmp_path):
    from flowstate.performance.identity import resource_digests
    data = tmp_path / "counts.json"
    data.write_text('{"count":1}', encoding="utf-8")
    original = resource_digests(tmp_path)
    data.write_text('{"count":2}', encoding="utf-8")
    assert resource_digests(tmp_path) != original


def test_prepared_identity_changes_for_seed_config_and_same_size_input(networks, tmp_path):
    for name in ("district.net.xml", "district.city.json", "district.stops.add.xml"):
        shutil.copyfile(networks / name, tmp_path / name)
    def key(seed=42, warmup=1, params=None):
        return identity("rush_hour", params or {"duration": 5}, seed, tmp_path, warmup=warmup)["key"]
    original = key()
    assert key() == original
    assert key(seed=43) != original
    assert key(warmup=2) != original
    assert key(params={"duration": 10}) != original
    path = tmp_path / "district.city.json"
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace("King West", "King Wesx"), encoding="utf-8")
    assert key() != original


def test_network_cache_reuses_content_and_invalidates_metadata_at_same_path(networks, tmp_path):
    from flowstate.performance.network_cache import load_network
    for name in ("district.net.xml", "district.city.json"):
        shutil.copyfile(networks / name, tmp_path / name)
    path = str(tmp_path / "district.net.xml")
    first = load_network(path, "district")
    assert load_network(path, "district") is first
    metadata = tmp_path / "district.city.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    data["title"] = "Changed title"
    metadata.write_text(json.dumps(data), encoding="utf-8")
    second = load_network(path, "district")
    assert second is not first
    assert second.city["title"] == "Changed title"


@pytest.mark.parametrize("seed", [True, -1, 2**31, 1.5])
def test_prepared_rejects_invalid_seeds(networks, seed):
    with pytest.raises(ValueError):
        PreparedRun("rush_hour", {}, seed, networks)


def test_prepared_run_is_frozen_then_continues_live_and_reproduces(networks, tmp_path):
    reports = []
    for _ in range(2):
        start = time.perf_counter()
        with PreparedRun("rush_hour", {"duration": 5}, 42, networks, warmup=90) as run:
            preparation = time.perf_counter() - start
            ready = run.ready["frame"]
            assert ready["t"] == 0
            assert ready["v"]
            start = time.perf_counter()
            run.claim()
            claim = time.perf_counter() - start
            # No wall-clock-driven stepping occurs while prepared or claimed.
            assert run.call("frame") == ready
            later = run.call("advance", 40)
            assert later["frame"]["t"] == 10
            assert later["frame"]["v"] != ready["v"]
            assert run.call("params", {"traffic": 1.1}) == {"traffic": 1.1}
            assert run.call("results")["t"] == 10
            with pytest.raises(RuntimeError, match="unknown operation"):
                run.call("invalid")
            assert run.call("frame") == later["frame"]
            with pytest.raises(RuntimeError, match="consumed"):
                run.claim()
            reports.append({"prepareSeconds": preparation, "claimSeconds": claim,
                            "ready": ready, "later": later["frame"]})
    assert reports[0]["ready"] == reports[1]["ready"]
    assert reports[0]["later"] == reports[1]["later"]
    # Compare the prepared process to an ordinary freshly warmed engine too.
    from flowstate.scenarios.catalog import create
    from flowstate.sim.engine import Engine, RunConfig
    scenario = create("rush_hour", {"duration": 5})
    fresh = Engine(scenario, RunConfig("rush_hour", scenario.params, 42, warmup=90), networks, tmp_path / "fresh")
    try:
        fresh.start()
        while fresh.t < 10:
            fresh.step()
        assert fresh.frame() == reports[0]["later"]
    finally:
        fresh.close()
    (tmp_path / "prepared-timings.json").write_text(json.dumps(reports), encoding="utf-8")


def test_prepared_expiry_closes_the_worker(networks):
    with PreparedRun("rush_hour", {"duration": 5}, 42, networks, warmup=0) as run:
        with pytest.raises(RuntimeError, match="expired"):
            run.claim(max_age=-1)
        assert run.closed
        assert not run.process.is_alive()


def test_presets_are_valid_existing_scenario_inputs_and_honest_labels():
    from flowstate.scenarios.catalog import create
    for preset in list_presets():
        params, warmup, description = resolve({}, preset["id"], "rush_hour")
        assert create("rush_hour", params).duration == 300
        assert warmup == 180
        assert not description["calibrated"] and not description["safetyValidated"]
    original = {"duration": 10, "demand": 1800}
    params, _, _ = resolve(original, "normal", "rush_hour")
    assert params["duration"] == 10 and params["demand"] == 1800
    assert original == {"duration": 10, "demand": 1800}
    with pytest.raises(ValueError):
        resolve({}, "imaginary", "rush_hour")


def test_safety_separates_repeated_records_and_pair_episodes():
    from traci._simulation import Collision
    monitor = SafetyObservations()
    collision = Collision("a", "b", "sedan", "truck", 2, 0, "junction", ":lane", 1)
    monitor.observe(0, [collision])
    monitor.observe(.25, [collision])
    monitor.observe(.5, [])
    monitor.observe(.75, [collision])
    result = monitor.summary()
    assert result["collisionRecordObservations"] == 3
    assert result["uniqueCollisionPairs"] == 1
    assert result["collisionEpisodes"] == 2


def test_collision_geometry_separates_contact_from_gap():
    from flowstate.performance.collision_audit import rectangle, overlap_depth
    first = rectangle("4,0", "0,0", 2)
    assert overlap_depth(first, rectangle("6,0", "2,0", 2)) > 0
    assert overlap_depth(first, rectangle("9,0", "5,0", 2)) < 0


def test_api_lists_five_minute_defaults_and_provisional_presets():
    from flowstate.api.app import app
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        specs = client.get("/api/scenarios").json()
        assert all(next(p for p in s["params"] if p["key"] == "duration")["default"] == 5 for s in specs)
        presets = client.get("/api/congestion-presets").json()
        assert len(presets) == 5
        assert all(not p["calibrated"] for p in presets)
        assert client.post("/api/runs", json={"scenario": "rush_hour", "congestion": "unknown"}).status_code == 400


def test_socket_edge_batching_preserves_positions_metrics_and_exact_edge_values(networks, tmp_path, monkeypatch):
    from flowstate.scenarios.catalog import create
    from flowstate.sim.engine import Engine, RunConfig
    from flowstate.performance.benchmark import enable_edge_subscriptions
    monkeypatch.setenv("FLOWSTATE_BACKEND", "traci")
    outputs = []
    for mode in ("poll", "subscribe"):
        scenario = create("rush_hour", {"duration": 5})
        eng = Engine(scenario, RunConfig("rush_hour", scenario.params, 42, warmup=30), networks, tmp_path / mode)
        try:
            eng.start()
            if mode == "subscribe":
                enable_edge_subscriptions(eng)
            while eng.t < 10:
                eng.step()
            eng._poll_edges()
            outputs.append((eng.frame(), eng.kpi, eng.edge_stats))
            for edge, observed in eng.edge_stats.items():
                assert observed == (eng.T.edge.getLastStepVehicleNumber(edge),
                                    eng.T.edge.getLastStepHaltingNumber(edge), eng.T.edge.getLastStepMeanSpeed(edge))
        finally:
            eng.close()
    assert outputs[0] == outputs[1]
