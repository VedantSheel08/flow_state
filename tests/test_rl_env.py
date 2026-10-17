"""The Gymnasium environment on real SUMO steps: API contract, determinism, safety as SUMO actually displays it."""

import numpy as np
import pytest

pytest.importorskip("gymnasium")
pytest.importorskip("stable_baselines3")
pytest.importorskip("libsumo")

import libsumo as T  # noqa: E402
from stable_baselines3.common.env_checker import check_env  # noqa: E402

from flowstate.rl import config as C  # noqa: E402
from flowstate.rl.env import OBS_PER_JUNCTION, SignalControlEnv  # noqa: E402

OPTS = {"scenario": "balanced", "demand_seed": 11}


@pytest.fixture
def env():
    e = SignalControlEnv(("balanced",), horizon_s=300.0, label="pytest")
    yield e
    e.close()


def test_environment_passes_the_gymnasium_checker(env):
    check_env(env, warn=True, skip_render_check=True)


def test_spaces_and_observation_are_compact_and_bounded(env):
    obs, info = env.reset(seed=1, options=OPTS)
    assert env.action_space.nvec.tolist() == [2, 2, 2, 2]
    assert obs.shape == env.observation_space.shape == (OBS_PER_JUNCTION * 4,) and OBS_PER_JUNCTION == 23
    assert obs.dtype == np.float32 and np.all((obs >= 0) & (obs <= 1))
    assert info["scenario"] == "balanced" and len(info["demand_sha"]) == 16
    for _ in range(60):  # one 300 s episode
        obs, r, term, trunc, info = env.step(env.action_space.sample())
        assert np.all((obs >= 0) & (obs <= 1)) and np.isfinite(r)
        assert -12.0 < r < 1.0
    assert trunc and not term and info["episode_metrics"]["sim_end_s"] == 300.0
    assert np.isfinite(info["episode_return"])


def test_same_seed_gives_identical_demand_observations_and_outcome(env):
    def episode():
        o, i = env.reset(seed=3, options=OPTS)
        trace = [o.copy()]
        for k in range(60):
            o, r, *_ , info = env.step(np.array([k % 2, 0, 1, (k // 3) % 2]))
            trace.append(o.copy())
        return i["demand_sha"], np.stack(trace), info["episode_metrics"]

    sha1, tr1, m1 = episode()
    sha2, tr2, m2 = episode()
    assert sha1 == sha2 and np.array_equal(tr1, tr2)
    assert {k: v for k, v in m1.items() if k != "wall_s"} == {k: v for k, v in m2.items() if k != "wall_s"}
    _, other = env.reset(seed=3, options={**OPTS, "demand_seed": 12})
    assert other["demand_sha"] != sha1, "a different demand seed must give different demand"


def test_demand_does_not_depend_on_what_the_agent_does(env):
    """Same (scenario, seed) -> same vehicles whatever the actions: the agent cannot shrink its own workload."""
    seen = set()
    for policy in (lambda k: [0, 0, 0, 0], lambda k: [1, 1, 1, 1], lambda k: [k % 2] * 4):
        _, info = env.reset(seed=5, options=OPTS)
        for k in range(30):
            env.step(np.array(policy(k)))
        seen.add((info["demand_sha"], env.sim.manifest["vehicles"]))
    assert len(seen) == 1


def test_sumo_really_shows_full_yellow_and_all_red_and_minimum_green(env):
    """Measure the signal heads from SUMO itself while an agent that asks for a switch every interval drives the junctions."""
    env.reset(seed=2, options={"scenario": "ns_heavy", "demand_seed": 4})
    sim = env.sim
    shown = {j: [] for j in C.CONTROLLED}
    for _ in range(100):  # 500 s
        for jid, a in zip(C.CONTROLLED, [1, 1, 1, 1]):
            sim.request(jid)
        for _ in range(int(C.DECISION_S)):
            sim.advance(1.0)
            for jid in C.CONTROLLED:
                shown[jid].append(T.trafficlight.getRedYellowGreenState(jid))
    for jid, states in shown.items():
        j = sim.grid.junctions[jid]
        kinds = ["g" if s in j.greens else "y" if s in j.yellows else "r" if set(s) == {"r"} else "?" for s in states]
        assert "?" not in kinds, f"{jid} showed a state that is neither a green stage, a clearance nor all-red"
        runs = []
        for k, stage in zip(kinds, states):
            if runs and runs[-1][0] == k and (k != "g" or runs[-1][2] == stage):
                runs[-1][1] += 1
            else:
                runs.append([k, 1, stage])
        inner = runs[1:-1]
        assert any(r[0] == "y" for r in inner), "the agent asked for switches; yellow must appear"
        for kind, length, _ in inner:
            if kind == "y":
                assert length == C.YELLOW_S
            elif kind == "r":
                assert length == C.ALL_RED_S
            else:
                assert length >= C.MIN_GREEN_S, f"{jid}: green of {length} s is shorter than the pedestrian-safe minimum"
    assert sim.metrics()["requests_refused"] > 0


def test_episode_metrics_report_every_scheduled_vehicle():
    e = SignalControlEnv(("pulse",), horizon_s=300.0, drain_s=600.0, label="pytest-drain")
    try:
        e.reset(seed=1, options={"scenario": "pulse", "demand_seed": 8})
        done = False
        while not done:
            *_, trunc, info = e.step(np.zeros(4, dtype=int))
            done = trunc
        m = info["episode_metrics"]
        assert m["vehicles_completed"] + m["vehicles_unfinished"] == m["vehicles_scheduled"]
        assert m["sim_end_s"] <= 900.0 and m["collisions"] == 0
    finally:
        e.close()
