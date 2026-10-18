"""Intelligence Lab: measurement integrity, observation pipeline, bounded ML control, grounding, API."""

from __future__ import annotations

import numpy as np
import pytest

from flowstate.lab.ask import deterministic, grounded
from flowstate.lab.control import ForecastAware
from flowstate.lab.forecast import H30, H60, rows_from
from flowstate.lab.sim import FEATURES, LabSim
from flowstate.rl import config as C
from flowstate.rl.controllers import QueueResponsive, make
from flowstate.rl.evaluate import run_episode
from flowstate.rl.sim import SignalSim

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _drive(sim, ctl, n_decisions):
    for _ in range(n_decisions):
        sim.observe() if isinstance(sim, LabSim) else None
        for jid, a in zip(C.CONTROLLED, ctl.decide(sim)):
            if a:
                sim.request(jid)
        sim.advance(C.DECISION_S)


def test_labsim_measures_exactly_like_signalsim():
    """The lab's extra subscription, CO2 and frames must not change any inherited measurement."""
    base = run_episode(make("queue_heuristic")[0], "balanced", 3000, horizon_s=180, drain_s=60, sim=SignalSim("t-base"))
    lab = LabSim("t-lab", record=True)
    try:
        lab.reset("balanced", 3000, 180, 60)
        lab.controllers_see_emergency = True  # the lab hides ambulances from controllers by default; the plain RL simulator does not, so match it here
        ctl = make("queue_heuristic")[0]
        while not lab.done:
            _drive(lab, ctl, 1)
        m = lab.metrics()
    finally:
        lab.close()
    for k in ("mean_wait_s", "mean_travel_s", "throughput_veh", "teleports", "collisions", "queue_max_veh", "demand_sha"):
        assert m[k] == base[k], k
    assert m["co2_kg"] > 0
    assert len(lab.frames) == int(m["sim_end_s"])
    f = lab.frames[100]
    assert len(f["s"]) == 4 and all(len(v) == 7 for v in f["v"])


def test_traffic_state_matches_sumo_and_targets_align():
    from flowstate.rl.sim import T

    sim = LabSim("t-obs")
    try:
        sim.reset("ns_heavy", 3001, 300, 0, 1.18)
        ctl = make("fixed_time")[0]
        _drive(sim, ctl, 30)
        X = sim.observe()
        assert X.shape == (16, len(FEATURES))
        for i, a in enumerate(sim.topo.approaches):
            assert X[i, 0] == T.edge.getLastStepHaltingNumber(a.edge)
        assert sum(1 for a in sim.topo.approaches if a.is_entry) == 8
        assert sum(1 for k in sim.topo.feeds if k is not None) == 8  # internal links feed a neighbour approach
    finally:
        sim.close()
    ep = {"X": np.arange(20 * 16 * len(FEATURES), dtype=float).reshape(20, 16, len(FEATURES))}
    f, y, a = rows_from(ep)
    assert len(y) == (20 - H60) * 16
    assert y[0, 0] == ep["X"][H30, 0, 0] and y[0, 1] == ep["X"][H60, 0, 0] and y[17, 0] == ep["X"][H30 + 1, 1, 0]


def test_forecast_aware_is_bounded_and_falls_back_without_forecast():
    sim = LabSim("t-ctl")
    try:
        sim.reset("pulse", 3002, 300, 0, 1.18)
        base, ml = QueueResponsive(), ForecastAware(beta=1.0, bound=4.0, spill=9.0)
        _drive(sim, base, 20)
        sim.observe()
        sim.forecast = None
        assert ml.decide(sim) == base.decide(sim)  # no forecast -> exactly the verified heuristic
        sim.forecast = {"p30": np.full(16, 1000.0), "p60": np.zeros(16), "q": np.zeros(16), "cap": np.full(16, 50.0)}
        for jid in C.CONTROLLED:
            plain = base._demand(sim, jid, 0)[0]
            assert ml._demand(sim, jid, 0)[0] - plain <= 4.0 + 1e-9  # adjustment never exceeds the bound
    finally:
        sim.close()


def test_grounding_rejects_invented_numbers():
    facts = {"a": {"mean_wait_s": 41.27, "pairs": 60}, "b": [3.5]}
    assert grounded("Mean wait was 41.3 s over 60 pairs, 3.5 fewer", facts)[0]
    ok, bad = grounded("ML cut waiting by 27.4%", facts)
    assert not ok and bad == ["27.4"]


def test_deterministic_answers_are_built_from_facts():
    F = {"pairedEvaluation": {"ml_vs_heuristic": {"mean_wait_s": {"mean_diff": 0.8, "ci95": [-0.4, 2.1], "pairs": 60}}}}
    text, req = deterministic("Did ML improve traffic?", F)
    assert "not clearly different" in text and req is None
    text, req = deterministic("Suggest a heavy rush hour scenario", F)
    assert req and req["scenario"] == "rush_ramp" and req["intensity"] == 1.18


def test_lab_api_status_geometry_and_offline_ask(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from flowstate.lab import app as A

    with TestClient(A.app) as c:
        s = c.get("/api/lab/status").json()
        assert {"fixed_time", "queue_heuristic", "ml_forecast"} <= set(s["controllers"])
        assert s["gemini"]["inControlLoop"] is False
        g = c.get("/api/lab/geometry").json()
        assert len(g["approaches"]) == 16 and len(g["heads"]) > 0
        r = c.post("/api/lab/ask", json={"question": "Did ML improve traffic?"}).json()
        assert r["source"] == "measurements" and r["answer"]
        assert c.get("/api/lab/replays/..%2Fsecret").status_code in (400, 404)
        assert c.post("/api/lab/replays", json={"left": "ml_forecast", "right": "ml_forecast"}).status_code == 400


def test_gemini_answer_accepted_only_when_grounded(monkeypatch):
    """Through the real GuardedGemini (cache, limits, breakers) with a scripted client: a grounded answer is used, an invented number is not."""
    from flowstate.intelligence.gemini import GuardedGemini
    from flowstate.lab import ask as K

    replies = []

    class Client:
        def generate_json(self, system, user, schema):
            assert "never" in system.lower() or "only numbers" in system.lower()
            return replies.pop(0)

    guard = GuardedGemini(api_key="test-key-not-real", client_factory=lambda m, t, k: Client())
    monkeypatch.setattr(K, "_guard", lambda: guard)
    monkeypatch.setattr(K, "facts", lambda r, l: {"scenarios": {"pulse": "Alternating pulses"}, "pairedEvaluation": {"ml_vs_heuristic": {"mean_wait_s": {"mean_diff": 1.68, "ci95": [-0.79, 4.24], "pairs": 60}}}})
    replies.append({"answer": "Across 60 pairs the difference was 1.68 s, CI -0.79 to 4.24, so not clear.", "scenario_request": {"scenario": "pulse", "intensity": 3.0}})
    r = K.answer("Did ML help?", None, None)
    assert r["source"] == "gemini" and r["scenarioRequest"] == {"scenario": "pulse", "intensity": 1.4, "left": "queue_heuristic", "right": "ml_forecast"}
    replies.append({"answer": "ML cut waiting by 23.5 s.", "scenario_request": None})
    r = K.answer("Did ML help a lot?", None, None)
    assert r["source"] == "measurements" and r["rejectedNumbers"] == ["23.5"]
