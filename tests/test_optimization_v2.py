"""Optimization v2: pressure control keeps the safety rules, the forecast can only bring a switch forward, and fallbacks work."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from flowstate.lab import control, optimize
from flowstate.lab.forecast import Forecaster
from flowstate.lab.sim import LabSim
from flowstate.rl import config as C

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "optimization"))
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _run(sim, ctl, n):
    for _ in range(n):
        sim.observe()
        for jid, a in zip(C.CONTROLLED, ctl.decide(sim)):
            if a:
                sim.request(jid)
        sim.advance(C.DECISION_S)


def test_downstream_map_follows_the_grid():
    sim = LabSim("t-opt-map")
    try:
        sim.reset("balanced", 4000, 60, 0)
        m = optimize.DownstreamMap(sim)
        assert len(m.down) == 16 and all(len(d) == 3 for d in m.down)  # straight, left, right; no U-turns
        for i, a in enumerate(sim.topo.approaches):  # the straight movement matches the lab topology's own "feeds"
            straight = sim.topo.index.get(next(((b.jid, b.direction) for b in sim.topo.approaches if b.edge == a.out_edge), ("", "")))
            assert sim.topo.feeds[i] == straight and straight in m.down[i]
    finally:
        sim.close()


def test_forecast_never_holds_a_green_and_safety_is_audited():
    """The arrival term only touches the waiting stage, so with the downstream term off the forecast can only switch earlier."""
    from bench import audit

    sim = LabSim("t-opt-fc")
    try:
        sim.reset("pulse", 4001, 600, 0, 1.18)
        ctl, info = control.make_controller("ml_pressure@6,1,0,0.5", sim)
        assert info["mlInControl"] and sim.forecaster is not None
        ctl.reset(sim)
        _run(sim, ctl, 60)
        st = ctl.stats()
        assert st["decisions"] > 0 and st["heldLonger"] == 0
        sim.observe()
        f = sim.forecast
        base, with_fc = ctl._decide(sim, False), ctl._decide(sim, True)
        assert all(a >= b for a, b in zip(with_fc, base))  # the forecast can only turn a hold into a switch
        greens = [sim.topo.index[(j, d)] for j in C.CONTROLLED for d in sim.grid.junctions[j].served[sim.signals[j].stage]]
        f["p30"] = f["p30"].copy()
        f["p30"][greens] = 1e6  # an absurd forecast on every green approach must change nothing
        assert ctl._decide(sim, True) == with_fc
        assert audit(sim) == {"green_short": 0, "green_long": 0, "yellow_short": 0, "all_red_short": 0}
        assert sim.metrics()["collisions"] == 0
    finally:
        sim.close()


def test_audit_detects_a_violation():
    from bench import audit

    class S:
        history = [(10.0, "yellow", 0), (12.0, "all_red", 0), (14.0, "green", 1), (90.0, "yellow", 1), (94.0, "all_red", 1), (96.0, "green", 0)]

    class Sim:
        signals = {"A0": S()}
        t = 120.0

    a = audit(Sim())
    assert a["yellow_short"] == 1 and a["green_long"] == 1 and a["all_red_short"] == 0


def test_ml_pressure_falls_back_to_the_heuristic_without_a_forecaster(monkeypatch):
    monkeypatch.setattr(Forecaster, "load_default", classmethod(lambda cls: None))
    sim = LabSim("t-opt-fb")
    try:
        sim.reset("balanced", 4002, 60, 0)
        ctl, info = control.make_controller("ml_pressure", sim)
        assert info["fallback"] and info["used"] == "queue_heuristic" and ctl.id == "queue_heuristic"
        det, dinfo = control.make_controller("pressure", sim)  # the deterministic controller needs no forecaster
        assert not dinfo["mlInControl"] and isinstance(det, optimize.PressureControl)
    finally:
        sim.close()


def test_pressure_reduces_to_the_heuristic_when_downstream_is_ignored():
    """gamma = 0 and no forecast: exactly the plain queue heuristic's decisions."""
    from flowstate.rl.controllers import QueueResponsive

    sim = LabSim("t-opt-eq")
    try:
        sim.reset("ns_heavy", 4003, 400, 0, 1.18)
        p = optimize.PressureControl(margin=6, hold_s=20, weight=0.25, gamma=0.0)
        h = QueueResponsive(6, 20, 0.25)
        p.reset(sim)
        for _ in range(70):
            sim.observe()
            a, b = p.decide(sim), h.decide(sim)
            assert a == b
            for jid, x in zip(C.CONTROLLED, a):
                if x:
                    sim.request(jid)
            sim.advance(C.DECISION_S)
        assert np.isfinite(sim.metrics()["mean_wait_s"])
    finally:
        sim.close()


def test_controllers_are_exposed_through_the_lab_api():
    from fastapi.testclient import TestClient

    from flowstate.lab import app as A
    from flowstate.lab.app import PairRequest

    with TestClient(A.app) as c:
        assert {"pressure", "ml_pressure"} <= set(c.get("/api/lab/status").json()["controllers"])
    assert PairRequest(left="queue_heuristic", right="pressure").right == "pressure"
    assert PairRequest(left="pressure", right="ml_pressure").left == "pressure"
    sel = optimize.selected()  # the bare ids resolve to the parameters chosen on development seeds
    assert optimize.parse("pressure").MARGIN == sel["pressure"]["margin"] and optimize.parse("ml_pressure").beta == sel["ml_pressure"]["beta"]
