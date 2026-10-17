"""Evaluation harness: classifier gates, queue and cycle detection, and one real-SUMO end-to-end run."""

from __future__ import annotations

import json

import pytest

from flowstate.evaluation import classify as K
from flowstate.evaluation.collector import Collector, _V, _sccs
from flowstate.evaluation import conditions as C


def _rows(n=300, density=50.0, storage=0.4, body=0.25, p50=10.0, p90=30.0, halted=0.4):
    return [{"t": float(i), "density": density, "storage": storage, "body": body, "p50": p50, "p90": p90, "halted": halted} for i in range(n)]


def _metrics(**kw):
    m = {
        "rows": _rows(), "dzRows": [], "queueApproaches": 4, "stopGoVehicles": 60, "corridorSpillSeconds": 0, "firstMinuteDensity": 50.0, "lastMinuteDensity": 50.0,
        "cycleSeconds": 0, "cycleJunctions": [], "cycleStreets": [], "greenBlockedEvidence": False, "greenBlockedJunctions": {}, "outflowRatio": None,
        "rescueDisabled": False, "timeToTeleport": "240", "collisionRecords": 0, "collisionEpisodes": 0, "collisionPairs": 0, "invalidRoutes": 0,
        "ledgerResidual": 0, "teleports": 0, "startDensity": {"atZero": 50.0, "minTwoCycles": 48.0}, "travelP50": 12.0, "travelSamples": 100,
    }
    m.update(kw)
    return m


def test_regular_rush_is_recognised_from_measurements_not_from_the_request():
    r = K.classify(_metrics(), requested="gridlock")
    assert r["achievedTier"] == "rush" and r["qualification"] == "qualified_other_tier"
    assert r["requestedTier"] == "gridlock" and "gridlock.density" in r["failedGates"]


def test_a_sparse_network_fails_every_tier_even_at_maximum_request():
    m = _metrics(rows=_rows(density=21.0, storage=0.15, body=0.1, p50=0.0, halted=0.6))
    r = K.classify(m, requested="gridlock")
    assert r["achievedTier"] is None and r["qualification"] == "target_unmet"


def test_collisions_make_an_otherwise_achieved_tier_unsafe():
    r = K.classify(_metrics(collisionRecords=2, collisionEpisodes=2, collisionPairs=2), requested="rush")
    assert r["achievedTier"] == "rush" and r["qualification"] == "unsafe" and "safety.collisions" in r["failedGates"]


def test_severe_needs_corridor_spillback_with_green_blocked_evidence():
    sev = dict(rows=_rows(density=95, storage=0.7, body=0.45, p50=2.0, p90=12.0, halted=0.7), travelP50=3.0, corridorSpillSeconds=200,
               startDensity={"atZero": 95.0, "minTwoCycles": 90.0}, firstMinuteDensity=96.0, lastMinuteDensity=94.0)
    assert K.classify(_metrics(**sev), "severe")["achievedTier"] == "severe"
    sev["corridorSpillSeconds"] = 60
    assert K.classify(_metrics(**sev), "severe")["achievedTier"] != "severe"


def test_gridlock_requires_a_persistent_blocked_cycle_disabled_rescue_and_low_outflow():
    dz = _rows(density=125, storage=0.9, body=0.55, p50=0.0, p90=1.0, halted=0.95)
    base = dict(rows=dz, dzRows=dz, travelP50Declared=0.2, travelSamplesDeclared=40, startDensity={"atZero": 125.0, "minTwoCycles": 120.0},
                cycleSeconds=200, cycleJunctions=["n1_1", "n2_1", "n1_2", "n2_2"], cycleStreets=["king", "main"], greenBlockedEvidence=True,
                greenBlockedJunctions={"n1_1": 30}, outflowRatio=0.04, rescueDisabled=True, timeToTeleport="-1")
    ok = K.classify(_metrics(**base), "gridlock")
    assert ok["achievedTier"] == "gridlock" and ok["qualification"] == "qualified"
    assert K.classify(_metrics(**{**base, "rescueDisabled": False}), "gridlock")["achievedTier"] != "gridlock"
    assert K.classify(_metrics(**{**base, "outflowRatio": 0.6}), "gridlock")["achievedTier"] != "gridlock"
    assert K.classify(_metrics(**{**base, "cycleSeconds": 60}), "gridlock")["achievedTier"] != "gridlock"
    # without a matched reference the outflow gate is undecided, which is not a pass
    assert K.classify(_metrics(**{**base, "outflowRatio": None}), "gridlock")["achievedTier"] != "gridlock"


def test_density_must_be_present_at_the_start_of_the_observation():
    m = _metrics(startDensity={"atZero": 20.0, "minTwoCycles": 15.0})
    assert "safety.steadyAtStart" in K.classify(m, "rush")["failedGates"]


def test_strongly_connected_components_find_the_blocked_cycle():
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a", "d"}, "d": {"e"}, "e": set()}
    comps = sorted(sorted(c) for c in _sccs(graph) if len(c) > 1)
    assert comps == [["a", "b", "c"]]


def test_queue_chain_stops_at_a_gap_a_moving_vehicle_or_a_far_head():
    v = lambda pos, speed: _V("l", pos, 4.75, 2.5, speed, f"v{pos}", True)  # noqa: E731
    L = 100.0
    # head at the stop line, two more bumper to bumper, then a 20 m hole
    q = Collector._chain([v(99.0, 0.0), v(92.0, 0.0), v(85.0, 0.0), v(60.0, 0.0)], L)
    assert [x.pos for x in q] == [99.0, 92.0, 85.0]
    assert Collector._chain([v(99.0, 5.0), v(92.0, 0.0)], L) == []  # the head is moving: no queue
    assert Collector._chain([v(60.0, 0.0)], L) == []  # a stopped vehicle far from the stop line is not a queue


def test_conditions_register_the_three_inherited_presets_and_every_experiment_is_a_condition():
    assert {"baseline_rush", "baseline_severe", "baseline_gridlock"} <= set(C.CONDITIONS)
    c = C.get("acc_pm_800_g")
    assert c.demand_overrides["ADMISSION"] == "accumulation" and c.demand_overrides["TARGET_ACTIVE"] == 800 and c.box_guard


def test_a_real_sumo_run_produces_a_complete_conserving_report(tmp_path):
    """Light traffic, real SUMO: every report section exists, the demand ledger balances and nothing collides."""
    from flowstate.evaluation.runner import run_condition

    cond = C.Condition("smoke", "rush", "light AM traffic for the test suite", params={"demand": 600, "traffic": 1.0, "peak": 1.2}, warmup=90.0)
    rep = run_condition(cond, 7, tmp_path, progress=False)
    assert (tmp_path / "smoke-7.json").exists()
    for key in ("summary", "classification", "ledger", "safety", "byGroup", "series", "manifest"):
        assert key in rep
    assert rep["ledger"]["background"]["residual"] == 0
    assert rep["safety"]["native"]["collisionRecordObservations"] == 0
    assert rep["classification"]["achievedTier"] is None  # light traffic is below regular rush: measured, not assumed
    assert rep["summary"]["arterialLaneKm"] == pytest.approx(6.273, abs=0.01)
    json.dumps(rep)
