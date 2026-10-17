"""Per-junction agent layer: observation v2, local rewards, reward blending."""

import numpy as np
import pytest

pytest.importorskip("gymnasium")
pytest.importorskip("stable_baselines3")

import gymnasium as gym  # noqa: E402
from gymnasium import spaces  # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

from flowstate.rl.agents import JunctionVecEnv  # noqa: E402

D, J = 33, 4


class Stub(gym.Env):
    observation_space = spaces.Box(-1, 1, (D * J,), np.float32)
    action_space = spaces.MultiDiscrete([2] * J)

    def __init__(self, horizon=3):
        super().__init__()
        self.t, self.horizon = 0, horizon

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.t = 0
        return np.zeros(D * J, np.float32), {}

    def step(self, action):
        self.t += 1
        obs = np.full(D * J, self.t / 10, np.float32)
        return obs, -1.0, False, self.t >= self.horizon, {"junction_rewards": np.array([-0.0, -2.0, -4.0, -6.0], np.float32)}


def test_every_junction_becomes_an_agent_with_a_blended_reward():
    v = JunctionVecEnv(DummyVecEnv([Stub, Stub]), alpha=0.5)
    assert v.num_envs == 2 * J and v.observation_space.shape == (D,) and v.action_space.n == 2
    assert v.reset().shape == (2 * J, D)
    v.step_async(np.zeros(2 * J, dtype=int))
    obs, rew, done, infos = v.step_wait()
    assert obs.shape == (2 * J, D) and len(infos) == 2 * J
    # 0.5 * local + 0.5 * global(-1)
    assert np.allclose(rew[:J], [-0.5, -1.5, -2.5, -3.5])


def test_alpha_one_is_purely_local_and_alpha_zero_purely_global():
    for alpha, expect in ((1.0, [0.0, -2.0, -4.0, -6.0]), (0.0, [-1.0] * 4)):
        v = JunctionVecEnv(DummyVecEnv([Stub]), alpha=alpha)
        v.reset()
        v.step_async(np.zeros(J, dtype=int))
        assert np.allclose(v.step_wait()[1], expect)


def test_truncation_keeps_the_terminal_observation_for_bootstrapping():
    v = JunctionVecEnv(DummyVecEnv([lambda: Stub(horizon=1)]), alpha=0.5)
    v.reset()
    v.step_async(np.zeros(J, dtype=int))
    obs, rew, done, infos = v.step_wait()
    assert done.all()
    for i in infos:
        assert i["TimeLimit.truncated"] is True and i["terminal_observation"].shape == (D,)
