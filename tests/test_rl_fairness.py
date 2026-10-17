"""Fair comparison and reward-hacking guards, on real SUMO runs."""

import numpy as np
import pytest

pytest.importorskip("gymnasium")
pytest.importorskip("libsumo")

from flowstate.rl.controllers import FixedTime, QueueResponsive  # noqa: E402
from flowstate.rl.env import SignalControlEnv  # noqa: E402
from flowstate.rl.evaluate import run_episode  # noqa: E402
from flowstate.rl.sim import SignalSim  # noqa: E402

DETERMINISTIC = ("mean_wait_s", "mean_travel_s", "throughput_veh", "queue_total_mean_veh", "spillback_pct", "teleports", "collisions",
                 "vehicles_completed", "signal_switches", "demand_sha")


def episode(ctl, scenario="ns_heavy", seed=7, **kw):
    return run_episode(ctl, scenario, seed, horizon_s=300.0, drain_s=600.0, **kw)


@pytest.fixture
def sim():
    s = SignalSim("pytest-fair")
    yield s
    s.close()


def test_every_strategy_sees_exactly_the_same_vehicles(sim):
    runs = [episode(c, sim=sim) for c in (FixedTime(30), QueueResponsive(), FixedTime(15))]
    assert len({r["demand_sha"] for r in runs}) == 1
    assert len({r["vehicles_scheduled"] for r in runs}) == 1
    for r in runs:  # nobody is dropped: every scheduled vehicle is completed or reported as unfinished
        assert r["vehicles_completed"] + r["vehicles_unfinished"] == r["vehicles_scheduled"]
        assert r["completed_fraction"] > 0.95 and r["collisions"] == 0


def test_a_repeated_run_gives_identical_measurements(sim):
    a, b = episode(QueueResponsive(), sim=sim), episode(QueueResponsive(), sim=sim)
    assert {k: a[k] for k in DETERMINISTIC} == {k: b[k] for k in DETERMINISTIC}
    assert a["series"] == b["series"]


def test_different_strategies_really_do_different_things(sim):
    a, b = episode(FixedTime(30), sim=sim), episode(QueueResponsive(), sim=sim)
    assert a["signal_switches"] != b["signal_switches"] and a["mean_wait_s"] != b["mean_wait_s"]


def test_doing_nothing_does_not_pay():
    """An agent that never asks for a change (the safety layer still forces one every 60 s) must score worse than a
    reasonable plan on the same demand, and the shortfall must show up in the independent metrics too."""
    totals = {}
    for name, act in (("hold", lambda k: [0, 0, 0, 0]), ("alternate", lambda k: [1 if k % 6 == 0 else 0] * 4)):
        env = SignalControlEnv(("ns_heavy",), horizon_s=600.0, drain_s=0.0, label="pytest-hack")
        try:
            env.reset(seed=1, options={"scenario": "ns_heavy", "demand_seed": 21, "intensity": 1.18})
            done, ret, k = False, 0.0, 0
            while not done:
                _, r, _, done, info = env.step(np.array(act(k)))
                ret += r
                k += 1
            totals[name] = (ret, info["episode_metrics"])
        finally:
            env.close()
    assert totals["alternate"][0] > totals["hold"][0], "a policy that ignores the traffic must not outscore a cycling one"
    assert totals["alternate"][1]["mean_wait_s"] <= totals["hold"][1]["mean_wait_s"]
    assert totals["hold"][1]["vehicles_scheduled"] == totals["alternate"][1]["vehicles_scheduled"]


def test_vehicles_that_cannot_enter_are_counted_as_waiting_in_the_metrics(sim):
    """Insertion delay is part of waiting and of travel time, so blocking the entries cannot flatter a controller."""
    sim.reset("balanced", 3, 300.0, 0.0, 1.6)  # very heavy: entry streets back up
    ctl = FixedTime(60.0)
    while not sim.done:
        for jid, a in zip(("A0", "A1", "B0", "B1"), ctl.decide(sim)):
            if a:
                sim.request(jid)
        sim.advance(5.0)
    m = sim.metrics()
    assert m["vehicles_unfinished"] > 0 and m["unfinished_mean_wait_s"] > 0, "vehicles still on the road at the end are reported, not hidden"
    assert m["vehicles_completed"] + m["vehicles_unfinished"] == m["vehicles_scheduled"]
