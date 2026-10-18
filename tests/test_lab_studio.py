"""Studio backend: ambulances get no simulator priority, emergency-green controllers, ambulance metrics, perception (PPO shadow, camera), Gemini preview, story API."""

from __future__ import annotations

import json

import numpy as np
import pytest

from flowstate.lab import ask as K
from flowstate.lab import contract
from flowstate.lab.control import make_controller
from flowstate.lab.sim import LabSim
from flowstate.rl import config as C

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _run(sim: LabSim, ctl, seconds: float, watch=None):
    """Drive the controller for ``seconds`` of simulated time; ``watch(sim)`` is called at every 5 s decision."""
    end = sim.t + seconds
    while sim.t < end and not sim.done:
        sim.observe()
        for jid, a in zip(C.CONTROLLED, ctl.decide(sim)):
            if a:
                sim.request(jid)
        if watch:
            watch(sim)
        sim.advance(C.DECISION_S)


def _first_ambulance_time(sim: LabSim) -> float:
    return min(sim.sched[v] for v in sim.emergency_ids)


def test_ambulances_get_no_priority_unless_the_controller_is_an_ev_variant():
    sim = LabSim("t-ev")
    try:
        sim.reset("rush_ramp", 3100, 900, 0, 1.18)
        t0 = _first_ambulance_time(sim)
        flagged = {"blind": 0, "ev": 0}

        def watch(key):
            def f(s: LabSim) -> None:
                if s.ev_status:
                    flagged[key] += sum(1 for j in C.CONTROLLED for d in C.DIRECTIONS if s.approach(j, d).emergency)
            return f

        ctl, info = make_controller("queue_heuristic", sim)
        assert sim.controllers_see_emergency is False and info["emergencyGreen"] is False
        _run(sim, ctl, t0 + 120, watch("blind"))
        assert flagged["blind"] == 0  # the controller never learns an ambulance is there
        sim.reset("rush_ramp", 3100, 900, 0, 1.18)
        ctl, info = make_controller("queue_heuristic_ev", sim)
        assert sim.controllers_see_emergency is False and info["emergencyGreen"] is True  # the corridor wrapper handles ambulances; the base controller stays blind
        _run(sim, ctl, t0 + 120, watch("ev"))
        assert flagged["ev"] == 0
        acts = {e["action"] for e in ctl.corridor_log}
        assert ctl.corridor_log and acts <= {"hold", "switch", "waiting"} and "hold" in acts  # it held the green ahead of the ambulance
    finally:
        sim.close()


def test_controller_ids_and_flags():
    sim = LabSim("t-ids")
    try:
        for cid, ml, ev in (("fixed_time", False, False), ("queue_heuristic", False, False), ("queue_heuristic_ev", False, True), ("ml_forecast", True, False), ("ml_forecast_ev", True, True)):
            ctl, info = make_controller(cid, sim)
            assert sim.controllers_see_emergency is False and info["mlInControl"] is ml and info["emergencyGreen"] is ev, cid
            from flowstate.lab.control import WithEmergencyCorridor

            assert isinstance(ctl, WithEmergencyCorridor) is (ev and cid != "fixed_time"), cid
            if ml:
                assert ctl.id.endswith("_ev") == ev
    finally:
        sim.close()


def test_ambulance_metrics_count_every_unit_and_flag_unfinished():
    sim = LabSim("t-evm")
    try:
        sim.reset("rush_ramp", 3100, 900, 0, 1.18)
        ctl, _ = make_controller("queue_heuristic", sim)
        _run(sim, ctl, 260)  # stop early: some ambulances are still driving
        m = sim.metrics()
        due = sum(1 for v in sim.emergency_ids if sim.sched[v] <= sim.t)
        assert m["ev_total"] == due == len(m["ev_units"]) < len(sim.emergency_ids) and m["ev_unfinished"] == sum(1 for u in m["ev_units"] if not u["finished"])
        assert m["ev_unfinished"] > 0 and all(u["travel_s"] >= 0 for u in m["ev_units"])  # unfinished units are measured up to now, not dropped
        assert m["mean_wait_civilian_s"] > 0 and m["controllers_see_emergency"] is False
        stopped = sum(u["stopped_red_s"] + u["stopped_queue_s"] for u in m["ev_units"])
        assert stopped > 0 and all(u["stopped_red_s"] + u["stopped_queue_s"] <= u["travel_s"] + 1 for u in m["ev_units"])
    finally:
        sim.close()


def test_perception_views_are_consistent_with_sumo():
    from flowstate.lab.perception import OBS_NAMES, Perception, poses

    P = Perception()
    if P._cv2() is None:
        pytest.skip("opencv-python-headless is not installed")
    sim = LabSim("t-perc", record=True)
    try:
        sim.reset("rush_ramp", 3100, 900, 0, 1.18)
        ctl, _ = make_controller("ml_forecast_ev", sim)
        ctl.reset(sim)
        prev = cur = None
        views = {}
        for step in range(150):
            prev, cur = cur, poses(sim)
            if step % 5 == 0:
                sim.observe()
                act = ctl.decide(sim)
                for j, a in zip(C.CONTROLLED, act):
                    if a:
                        sim.request(j)
                if step == 145:
                    views = {"cam": P.camera_view(sim, "A0", cur, prev, sim.t), "ppo": P.ppo_view(sim, act), "fc": P.forecaster_view(sim)}
            sim.advance(1.0)
            sim.frames.clear()
        cam, ppo, fc = views["cam"], views["ppo"], views["fc"]
        assert cam["available"] and cam["synthetic"] and cam["frame"].startswith("data:image/jpeg;base64,") and cam["mask"].startswith("data:image/png;base64,")
        ideal = 4.5 * 1.8 * P.scale ** 2
        assert 0.6 * ideal < P._car_px < 2.2 * ideal and len(cam["approaches"]) == 4 and cam["truthVehiclesInView"] > 0
        assert all(len(d["poly"]) == 4 for d in cam["detections"]) and cam["countEstimate"] == len(cam["detections"])  # one oriented box per car
        assert cam["precision"] is None or cam["precision"] >= 0.9  # boxes that are not on a real car would show up here
        assert cam["recall"] is None or cam["recall"] >= 0.9
        assert abs(cam["countEstimate"] - cam["truthVehiclesInView"]) <= 4
        assert abs(sum(a["error"] for a in cam["approaches"])) <= 6  # queue estimate close to SUMO's own count
        assert len(fc["matrix"]) == 16 and len(fc["matrix"][0]) == len(fc["features"]) == 19
        assert len(OBS_NAMES) == 33
        if ppo["available"]:
            assert np.array(ppo["observation"]).shape == (4, 33) and np.allclose(np.sum(ppo["probabilities"], axis=1), 1.0, atol=1e-2)
            assert len(ppo["agrees"]) == 4 and ppo["validationPassed"] is False and "never controls" in ppo["role"]
        else:
            assert "observation" in ppo and ppo["reason"]  # degrades to observation-only with a reason
    finally:
        sim.close()


def test_gemini_preview_exposes_prompt_draft_and_grounding(monkeypatch):
    from flowstate.intelligence.gemini import GuardedGemini

    class Client:
        def generate_json(self, system, user, schema):
            return {"answer": "The forecast error is 2.23 vehicles and 99.9 percent of ambulances are fine.", "scenario_request": None}

    monkeypatch.setattr(K, "facts", lambda r, l: {"forecastBenchmark": {"30": {"overall": {"mae": 2.23}}}})
    monkeypatch.setattr(K, "_guard", lambda: GuardedGemini(api_key="k-not-real", client_factory=lambda m, t, k: Client()))
    r = K.answer("how accurate?", None, None)
    assert r["sentToGemini"] is True and r["prompt"]["system"] == K.SYSTEM and json.loads(r["prompt"]["user"])["FACTS"]["forecastBenchmark"]["30"]["overall"]["mae"] == 2.23
    assert r["geminiDraft"].startswith("The forecast error") and r["grounding"]["ungrounded"] == ["99.9"] and r["source"] == "measurements"
    monkeypatch.setattr(K, "_guard", lambda: GuardedGemini(api_key=""))
    r = K.answer("how accurate?", None, None)
    assert r["sentToGemini"] is False and r["geminiDraft"] is None and r["grounding"] is None  # nothing left the server


def test_report_carries_measured_emergency_state():
    rep = contract.build_report(t=10, queues=[0.0] * 16, source="live", controller={"id": "ml_forecast_ev", "mlInControl": True, "emergencyGreen": True},
                                emergency=[{"id": 57, "status": 2, "state": "stuck behind queued cars", "metresToNextSignal": 31.4}])
    assert rep["observed"]["emergencyVehicles"][0]["state"] == "stuck behind queued cars" and rep["interventions"]["controller"]["emergencyGreen"] is True
    assert contract.build_report(t=1, queues=[0.0] * 16)["observed"]["emergencyVehicles"] == []


def test_studio_api_meta_and_story(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from flowstate.lab import app as A
    from flowstate.lab import record as R

    with TestClient(A.app) as c:
        m = c.get("/api/studio/meta").json()
        assert m["gemini"]["inControlLoop"] is False and m["ppo"]["validationPassed"] is False and len(m["observation"]["names"]) == 33
        assert c.get("/api/studio/replays/..%2Fx/story").status_code in (400, 404)
        ev = next((i for i, s in R.index().items() if s["right"].endswith("_ev")), None)
        if ev is None:
            pytest.skip("no ambulance-green replay recorded locally (python -m flowstate.lab.cli record)")
        s = c.get(f"/api/studio/replays/{ev}/story").json()
        assert s["sides"]["right"]["emergencyGreen"] is True and s["sides"]["left"]["emergencyGreen"] is False
        for side in ("left", "right"):
            assert s["sides"][side]["ambulances"], side
            for a in s["sides"][side]["ambulances"]:
                last = -1
                for t0, t1, code in a["segments"]:
                    assert t0 > last and t1 >= t0 and code in (0, 1, 2, 3)  # ordered, non-overlapping, known status
                    last = t1
        r = c.post("/api/studio/gemini", json={"question": "What happens to ambulances?", "replayId": ev}).json()
        assert r["source"] == "measurements" and "no priority" in r["answer"] and r["prompt"]["chars"] > 0
        # the right side's lights were cleared for ambulances, so it spent less time stopped at reds than the left side
        t = s["sides"]
        assert (t["right"]["totals"]["ev_stopped_red_s"] or 0) <= (t["left"]["totals"]["ev_stopped_red_s"] or 0) + 1.0


def test_grid_map_geometry_matches_the_simulation():
    """The map description for main's renderer: same coordinates and signal links as the simulation, plus generated decoration."""
    import json
    import xml.etree.ElementTree as ET

    from flowstate.lab.mapgeom import build
    from flowstate.rl.network import build_net
    import tempfile
    from pathlib import Path

    g = build()
    assert g["id"] == "rl_grid" and len(g["edges"]) == 24 and g["roadBounds"] == [0.0, 0.0, 600.0, 600.0]
    roles = {ln["role"] for e in g["edges"] for ln in e["lanes"]}
    assert roles == {"car", "sidewalk"} and len(g["crossings"]) > 0  # display-only sidewalks and crossings
    assert len(g["city"]["buildings"]) > 40 and len(g["city"]["trees"]) > 100 and {b["use"] for b in g["city"]["blocks"]} == {"park", "residential", "commercial"}
    assert all(c["tls"] is None for c in g["crossings"])  # the simulation has no pedestrian signals
    # every simulation signal link of every junction appears on exactly one car lane, in the simulation's numbering
    sim = ET.parse(build_net(Path(tempfile.gettempdir()) / "fs-mapgeom")).getroot()
    expect: dict[str, set[int]] = {}
    for c in sim.iter("connection"):
        if c.get("tl") and not c.get("from", "").startswith(":"):
            expect.setdefault(c.get("tl"), set()).add(int(c.get("linkIndex")))
    got: dict[str, list[int]] = {}
    for e in g["edges"]:
        for ln in e["lanes"]:
            if ln["tls"]:
                got.setdefault(ln["tls"], []).extend(ln["links"])
    assert {k: sorted(v) for k, v in got.items()} == {k: sorted(v) for k, v in expect.items()}
    # the artifact shipped to the lab is this geometry
    shipped = json.loads((Path(__file__).resolve().parents[1] / "artifacts" / "lab" / "grid-network.json").read_text())
    assert shipped["city"]["buildings"] == g["city"]["buildings"] and shipped["bounds"] == g["bounds"]


def test_corridor_plan_follows_each_ambulances_route():
    """The junctions the corridor clears are exactly the ones on the ambulance's remaining route, with the stage that serves the approach it will take."""
    from flowstate.rl.sim import T

    sim = LabSim("t-corr")
    try:
        sim.reset("rush_ramp", 3100, 900, 0, 1.18)
        ctl, _ = make_controller("queue_heuristic", sim)
        _run(sim, ctl, _first_ambulance_time(sim) + 40)
        plan = sim.emergency_corridor(3, 450.0)
        assert plan and len(plan) <= len(C.CONTROLLED)
        for jid, (stage, vnum, dist) in plan.items():
            route = T.vehicle.getRoute(f"v{vnum}")
            idx = T.vehicle.getRouteIndex(f"v{vnum}")
            appr = sim.grid.junctions[jid].approach_edges
            edge = next(e for e in route[idx:] if e in appr.values())  # the ambulance really will arrive at this junction
            direction = next(d for d, e in appr.items() if e == edge)
            assert direction in sim.grid.junctions[jid].served[stage] and 0.0 <= dist <= 450.0
        assert sim.emergency_corridor(1, 450.0).keys() <= plan.keys()  # a shorter lookahead never adds junctions
    finally:
        sim.close()
