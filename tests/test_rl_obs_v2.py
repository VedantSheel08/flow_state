"""Observation v2 and local rewards on real SUMO steps."""

import numpy as np
import pytest

pytest.importorskip("gymnasium")
pytest.importorskip("stable_baselines3")
pytest.importorskip("libsumo")

from flowstate.rl import config as C  # noqa: E402
from flowstate.rl.env import OBS_V2_PER_JUNCTION, SignalControlEnv, build_agent_observations  # noqa: E402


@pytest.fixture
def env():
    e = SignalControlEnv(("ns_heavy",), horizon_s=400.0, label="pytest-v2", obs_version=2)
    yield e
    e.close()


def test_v2_observation_is_per_junction_bounded_and_matches_the_space(env):
    obs, _ = env.reset(seed=1, options={"scenario": "ns_heavy", "demand_seed": 5})
    assert obs.shape == env.observation_space.shape == (OBS_V2_PER_JUNCTION * len(C.CONTROLLED),)
    for _ in range(40):
        obs, *_ = env.step([0, 0, 0, 0])
        assert env.observation_space.contains(obs)
    rows = build_agent_observations(env.sim)
    assert rows.shape == (len(C.CONTROLLED), OBS_V2_PER_JUNCTION) and np.abs(rows).max() <= 1.0


def test_demand_imbalance_feature_points_at_the_busier_stage(env):
    env.reset(seed=1, options={"scenario": "ns_heavy", "demand_seed": 5})
    for _ in range(30):
        env.step([0, 0, 0, 0])
    rows = build_agent_observations(env.sim)
    for jid, row in zip(C.CONTROLLED, rows):
        cur, nxt, diff = row[22], row[23], row[24]  # demand block starts after approaches (16) and signal (6)
        assert 0.0 <= cur <= 1.0 and 0.0 <= nxt <= 1.0
        if cur < 1.0 and nxt < 1.0:  # unclipped: the imbalance is exactly next minus current demand
            assert diff == pytest.approx(nxt - cur, abs=1e-5), jid


def test_local_rewards_are_costs_and_reported_each_step(env):
    env.reset(seed=1, options={"scenario": "ns_heavy", "demand_seed": 5})
    for _ in range(40):
        _, _, _, _, info = env.step([0, 0, 0, 0])
    jr = info["junction_rewards"]
    assert jr.shape == (len(C.CONTROLLED),) and (jr <= 0.0).all() and jr.min() < 0.0  # congestion has built up somewhere
