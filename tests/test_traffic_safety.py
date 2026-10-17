"""Safety and realism fixes found by the collision / teleport investigation (docs/TRAFFIC_REALISM.md)."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from flowstate.api.service import NETWORKS_DIR, WORK_DIR
from flowstate.evaluation import conditions as C
from flowstate.performance.congestion import ENGINE_OPTIONS, engine_options, list_presets, resolve
from flowstate.scenarios.catalog import create
from flowstate.sim.demand import DemandGenerator
from flowstate.sim.engine import Engine, RunConfig
from flowstate.sim.vtypes import build_vtypes


def test_jam_presets_carry_engine_options_and_the_rest_do_not():
    by_id = {p["id"]: p for p in list_presets()}
    assert {k for k, p in by_id.items() if p["engine"]} == set(ENGINE_OPTIONS) == {"rush_hour", "severe", "gridlock_stress"}
    for key in ENGINE_OPTIONS:
        o = engine_options(key)
        assert o["signal_program"] == "safe" and o["box_guard"]["margin"] > 0  # protected lefts + keep-the-box-clear
        assert o["demand_overrides"]["ADMISSION"] == "accumulation"  # the surplus waits in the counted external queue
        assert by_id[key]["warmup"] >= 600  # long enough for the district to reach the declared accumulation
    assert engine_options("light") == {} and engine_options(None) == {}
    # the deadlock rescue is off only for the gridlock test, which declares it
    assert engine_options("gridlock_stress")["sumo_options"]["--time-to-teleport"] == "-1"
    assert "--time-to-teleport" not in engine_options("severe")["sumo_options"] and "--time-to-teleport" not in engine_options("rush_hour")["sumo_options"]
    assert engine_options("severe")["demand_overrides"]["CRUISE_SHARE"] == 0.8 and not engine_options("rush_hour")["outlets"]
    assert engine_options("gridlock_stress")["outlets"] == {"lanes": 1, "speed_kmh": 10}  # only the gridlock test has a downstream restriction
    # the options are copies: editing one run's options never changes the next run's
    engine_options("severe")["demand_overrides"]["TARGET_ACTIVE"] = 1
    assert ENGINE_OPTIONS["severe"]["demand_overrides"]["TARGET_ACTIVE"] == 800


def test_preset_resolution_carries_each_presets_demand_direction_only_for_rush_hour():
    for preset, period, warmup in (("rush_hour", "am", 600), ("severe", "am", 600), ("gridlock_stress", "pm", 1000)):
        params, w, sel = resolve({}, preset, "rush_hour")
        assert params["period"] == period and w == warmup and sel["id"] == preset
    assert "period" not in resolve({}, "light", "rush_hour")[0]
    assert "period" not in resolve({}, "severe", "accident")[0]  # other scenarios keep their own demand model
    assert resolve({"period": "pm"}, "severe", "rush_hour")[0]["period"] == "pm"  # an explicit request still wins


def test_shipped_conditions_use_the_same_options_as_the_api_presets():
    for cid, preset in (("rush", "rush_hour"), ("severe", "severe"), ("gridlock", "gridlock_stress")):
        c, o = C.get(cid), engine_options(preset)
        assert c.signal_program == o["signal_program"] and c.box_guard == o["box_guard"]
        assert c.demand_overrides["TARGET_ACTIVE"] == o["demand_overrides"]["TARGET_ACTIVE"]
    g = C.get("gridlock")  # the recovery sequence is scheduled after the classified window
    assert g.outlets["relieve_at"] == 300.0 and g.demand_overrides["TARGET_SCHEDULE"] == [[300.0, 560]]


def test_vtype_overrides_replace_only_named_attributes_and_keep_the_seeded_draw():
    base, _ = build_vtypes(42)
    over, _ = build_vtypes(42, overrides={"passenger": {"jmTimegapMinor": "2.5"}})
    assert 'jmTimegapMinor="2.5"' in over and "jmTimegapMinor" not in base
    strip = lambda x: x.replace(' jmTimegapMinor="2.5"', "")  # noqa: E731
    assert strip(over) == base  # same drivers, one attribute added


def test_accumulation_target_follows_its_schedule():
    g = object.__new__(DemandGenerator)
    g.TARGET_ACTIVE, g.TARGET_SCHEDULE = 1400, [[300.0, 560], [600.0, 300]]
    assert [g.target_active(t) for t in (-10, 0, 299.9, 300, 599, 600, 900)] == [1400, 1400, 1400, 560, 560, 300, 300]
    g.TARGET_SCHEDULE = None
    assert g.target_active(500) == 1400


def test_a_route_sumo_refuses_is_counted_not_fatal_and_not_added():
    sc = create("rush_hour", {})
    eng = Engine(sc, RunConfig("rush_hour", sc.params, 1, "fixed", label="refuse"), NETWORKS_DIR, WORK_DIR / "test_refuse")
    calls = []

    def refuse(*a, **kw):
        calls.append(a)
        raise RuntimeError("Vehicle 'v1' has no valid route. No connection between edge 'a' and edge 'b'.")

    eng.T = SimpleNamespace(vehicle=SimpleNamespace(add=refuse))
    assert eng.add_vehicle("v1", "r1", "sedan@0") is False
    assert eng.rejected_adds == 1 and eng.added == 0 and "v1" not in eng.vtype
    eng.T = SimpleNamespace(vehicle=SimpleNamespace(add=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("something else"))))
    with pytest.raises(RuntimeError):  # only the 'no valid route' refusal is absorbed
        eng.add_vehicle("v2", "r1", "sedan@0")


def test_relief_restores_lane_permissions_and_speed_limits_and_meters_the_accumulation():
    sc = create("rush_hour", {"duration": 5, "period": "pm"})
    o = engine_options("gridlock_stress")
    cfg = RunConfig("rush_hour", sc.params, 3, "fixed", warmup=5.0, label="relief", signal_program=o["signal_program"],
                    demand_overrides=o["demand_overrides"], outlets=o["outlets"], sumo_options=o["sumo_options"])
    eng = Engine(sc, cfg, NETWORKS_DIR, WORK_DIR / "test_relief")
    eng.start()
    try:
        exits = [e for e in eng.net.edges.values() if e.kind == "exit" and len(eng.net.car_lanes(e.id)) > 1]
        assert exits and eng.outlet_log
        e = exits[0]
        lanes = eng.net.car_lanes(e.id)
        assert list(eng.T.lane.getAllowed(lanes[1])) == [] or "passenger" not in eng.T.lane.getAllowed(lanes[1])  # the second lane is closed
        assert eng.T.lane.getMaxSpeed(lanes[0]) == pytest.approx(10 / 3.6, rel=1e-3)
        for _ in range(8):
            eng.step()
        r = eng.relieve()
        assert r["outletsRestored"] and r["accumulationTarget"] == 560  # 40 % of 1400
        assert "passenger" in eng.T.lane.getAllowed(lanes[1]) or list(eng.T.lane.getAllowed(lanes[1])) == []
        assert eng.T.lane.getMaxSpeed(lanes[0]) > 10 / 3.6 * 2  # the road's own limit is back
        assert eng.demand.target_active(eng.t) == 560
        assert eng.relieve()["outletsRestored"] is False  # idempotent
    finally:
        eng.close()


def test_relieve_control_without_a_run_is_a_client_error():
    from fastapi.testclient import TestClient

    from flowstate.api.app import app
    with TestClient(app) as client:
        r = client.post("/api/runs/current/control", json={"action": "relieve"})
        assert 400 <= r.status_code < 500


def test_signal_primitives_never_shorten_or_stretch_a_clearance_interval():
    sc = create("rush_hour", {"duration": 5})
    eng = Engine(sc, RunConfig("rush_hour", sc.params, 5, "fixed", warmup=0.0, label="prims"), NETWORKS_DIR, WORK_DIR / "test_prims")
    eng.start()
    try:
        tid = eng.net.tls_ids[0]
        seen = {"green_end": 0, "clearance_refused": 0, "extended": 0.0}
        for _ in range(1200):  # five simulated minutes: every phase kind occurs
            eng.step()
            info = eng.signals.tls[tid]
            s = info.state
            if "y" in s.lower() or not any(c in "Gg" for c in s):
                assert eng.signals.end_phase(tid, eng.t) is False and eng.signals.extend_green(tid, 5.0, eng.t) == 0.0
                seen["clearance_refused"] += 1
            elif seen["green_end"] == 0 and eng.t - info.phase_start > 6.0:
                before = eng.T.trafficlight.getNextSwitch(tid)
                seen["extended"] = eng.signals.extend_green(tid, 4.0, eng.t)
                assert seen["extended"] == 4.0 and eng.T.trafficlight.getNextSwitch(tid) == pytest.approx(before + 4.0)
                assert eng.signals.extend_green(tid, 50.0, eng.t) <= 11.0  # the 15 s per-phase limit
                seen["green_end"] = 1
        assert seen["clearance_refused"] > 0 and seen["extended"] == 4.0
    finally:
        eng.close()


def test_prepared_identity_covers_the_engine_options(networks):
    from flowstate.performance.identity import identity
    plain = identity("rush_hour", {"duration": 5}, 1, networks, warmup=0.0)
    a = identity("rush_hour", {"duration": 5}, 1, networks, warmup=0.0, engine=engine_options("severe"))
    b = identity("rush_hour", {"duration": 5}, 1, networks, warmup=0.0, engine=engine_options("gridlock_stress"))
    assert len({plain["key"], a["key"], b["key"]}) == 3  # a prepared state is never reused for different engine options
    assert identity("rush_hour", {"duration": 5}, 1, networks, warmup=0.0, engine={})["key"] == plain["key"]


def test_prepared_worker_runs_with_a_jam_presets_engine_options(networks):
    from flowstate.performance.prepared import PreparedRun
    with PreparedRun("rush_hour", {"duration": 5, "period": "pm"}, 3, networks, warmup=10, engine=engine_options("gridlock_stress")) as run:
        run.claim()
        out = run.call("advance", 8)
        assert out["frame"] is not None and not out["finished"]
