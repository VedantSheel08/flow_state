"""Per-junction agents on top of the multi-junction environment.

``JunctionVecEnv`` presents every junction of every simulation as its own environment of a vectorised env: one
parameter-shared policy sees a single junction's observation block (``build_agent_observations``) and answers hold or
switch, and PPO computes advantages per junction. The reward each junction learns from is

    r_j = alpha * (its own approaches' queue, waiting and blocked-exit cost) + (1 - alpha) * (network-wide reward)

so a junction is credited mostly for what it controls without being allowed to ignore its neighbours (the global part
also carries the teleport and collision penalties). Everything still runs through ``SafeSignal``: the policy only asks.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from gymnasium import spaces
from stable_baselines3.common.vec_env.base_vec_env import VecEnv


class JunctionVecEnv(VecEnv):
    def __init__(self, venv: VecEnv, alpha: float, n_junctions: int = 4):
        self.venv, self.alpha, self.nj = venv, float(alpha), n_junctions
        self.dim = venv.observation_space.shape[0] // n_junctions
        super().__init__(venv.num_envs * n_junctions, spaces.Box(-1.0, 1.0, (self.dim,), np.float32), spaces.Discrete(2))

    def reset(self):
        return self.venv.reset().reshape(-1, self.dim)

    def step_async(self, actions: np.ndarray) -> None:
        self.venv.step_async(np.asarray(actions).reshape(self.venv.num_envs, self.nj))

    def step_wait(self):
        obs, rew, done, infos = self.venv.step_wait()
        local = np.stack([i["junction_rewards"] for i in infos])  # (envs, junctions)
        reward = (self.alpha * local + (1.0 - self.alpha) * rew[:, None]).reshape(-1).astype(np.float32)
        out: list[dict[str, Any]] = []
        for e, info in enumerate(infos):
            term = info.get("terminal_observation")
            for j in range(self.nj):
                d: dict[str, Any] = {"TimeLimit.truncated": bool(info.get("TimeLimit.truncated", False))}
                if done[e] and term is not None:
                    d["terminal_observation"] = np.asarray(term).reshape(self.nj, self.dim)[j]
                if j == 0 and "episode" in info:  # network-level episode return/length for the training log
                    d["episode"] = info["episode"]
                out.append(d)
        return obs.reshape(-1, self.dim), reward, np.repeat(done, self.nj), out

    def close(self) -> None:
        self.venv.close()

    def get_attr(self, attr_name, indices=None):
        if attr_name == "render_mode":  # queried by VecEnv.__init__
            return [None] * self.num_envs
        raise NotImplementedError

    def set_attr(self, attr_name, value, indices=None):
        raise NotImplementedError

    def env_method(self, method_name, *args, indices=None, **kwargs):
        raise NotImplementedError

    def env_is_wrapped(self, wrapper_class, indices=None):
        return [False] * self.num_envs
