"""PPO training with parallel SUMO workers, validation-based model selection and full experiment logging.

Seed discipline (all disjoint, so nothing the final report measures was seen in training or selection):

* training demand seeds     1000-1999 (random draw per episode, random intensity 0.7-1.2, scenarios without ``rush_ramp``)
* validation demand seeds   2000-2007 (fixed set, used for checkpoint selection)
* final test demand seeds   3000+     (touched only by the comparison report)

The selected policy is the checkpoint with the lowest mean waiting time on the validation set, not the last one.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .controllers import PolicyController
from .demand import TRAIN_SCENARIOS
from .env import SignalControlEnv
from .evaluate import run_episode

SELECTION_SEEDS = tuple(range(2000, 2004))  # checkpoint selection during training
GATE_SEEDS = tuple(range(2004, 2016))  # publication gate: never used to pick a checkpoint
VALIDATION_SEEDS = SELECTION_SEEDS + GATE_SEEDS[:4]  # legacy name (runs 1-3 gated on 2000-2007)
VALIDATION_INTENSITY = 1.0


@dataclass
class TrainConfig:
    seed: int = 1
    total_steps: int = 300_000
    n_envs: int = 6
    n_steps: int = 90  # half an episode per environment per rollout: more updates per simulated hour
    batch_size: int = 270
    n_epochs: int = 10
    learning_rate: float = 3e-4
    final_lr_fraction: float = 0.15
    gamma: float = 0.99
    gae_lambda: float = 0.95
    ent_coef: float = 0.01
    clip_range: float = 0.2
    net_arch: tuple[int, int] = (128, 128)
    validate_every: int = 30_000
    validation_scenarios: tuple[str, ...] = TRAIN_SCENARIOS
    validation_seeds: tuple[int, ...] = VALIDATION_SEEDS[:2]  # runs 1-3; per-junction runs pass SELECTION_SEEDS
    intensity_range: tuple[float, float] = (0.7, 1.2)
    agent_mode: bool = False  # per-junction agents, observation v2, blended reward (flowstate.rl.agents)
    blend: float = 0.5  # weight of the junction's own reward in agent mode
    shared_policy: bool = False  # parameter-shared per-junction network (flowstate.rl.policy) instead of one joint MLP
    norm_reward: bool = False  # running reward normalisation (returns here are ~ -80, which swamps the critic)
    out_dir: str = "artifacts/rl/run"


class EnvFactory:
    """Picklable environment constructor for SubprocVecEnv (Windows spawns fresh interpreters)."""

    def __init__(self, rank: int, cfg: TrainConfig):
        self.rank, self.cfg = rank, cfg

    def __call__(self):
        from stable_baselines3.common.monitor import Monitor

        env = SignalControlEnv(TRAIN_SCENARIOS, seed_pool=(1000, 1999), label=f"train{self.rank}", intensity_range=self.cfg.intensity_range,
                               obs_version=2 if self.cfg.agent_mode else 1)
        env = Monitor(env)
        env.reset(seed=self.cfg.seed * 1000 + self.rank)
        return env


def validate(model, cfg: TrainConfig, sim=None) -> dict[str, Any]:
    """Deterministic validation episodes (with drain) on the fixed validation seeds."""
    ctl = PolicyController(model)
    rows = []
    for sc in cfg.validation_scenarios:
        for seed in cfg.validation_seeds:
            rows.append(run_episode(ctl, sc, seed, intensity=VALIDATION_INTENSITY, drain_s=900.0, sim=sim, label="validation"))
    keys = ["mean_wait_s", "mean_travel_s", "queue_mean_veh", "spillback_pct", "teleports", "collisions", "completed_fraction"]
    return {k: float(np.mean([r[k] for r in rows])) for k in keys} | {"episodes": len(rows)}


def hardware() -> dict[str, Any]:
    import torch

    return {"cpu": platform.processor(), "platform": platform.platform(), "logical_cores": os.cpu_count(), "python": platform.python_version(),
            "torch": torch.__version__, "cuda": torch.cuda.is_available()}


def train(cfg: TrainConfig) -> dict[str, Any]:
    import stable_baselines3 as sb3
    import torch
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.logger import configure
    from stable_baselines3.common.vec_env import SubprocVecEnv

    out = Path(cfg.out_dir)
    (out / "checkpoints").mkdir(parents=True, exist_ok=True)
    torch.manual_seed(cfg.seed)
    torch.set_num_threads(2)
    venv = SubprocVecEnv([EnvFactory(i, cfg) for i in range(cfg.n_envs)], start_method="spawn")
    if cfg.agent_mode:
        from .agents import JunctionVecEnv

        venv = JunctionVecEnv(venv, cfg.blend)
    if cfg.norm_reward:
        from stable_baselines3.common.vec_env import VecNormalize

        venv = VecNormalize(venv, norm_obs=False, norm_reward=True, gamma=cfg.gamma)  # observations stay raw, so the saved policy needs no wrapper
    schedule = lambda progress: cfg.learning_rate * (cfg.final_lr_fraction + (1 - cfg.final_lr_fraction) * progress)  # noqa: E731
    if cfg.agent_mode:
        policy, pkw = "MlpPolicy", {"net_arch": {"pi": list(cfg.net_arch), "vf": list(cfg.net_arch)}}
    elif cfg.shared_policy:
        from .policy import SharedJunctionPolicy

        policy, pkw = SharedJunctionPolicy, {"hidden": cfg.net_arch[0]}
    else:
        policy, pkw = "MlpPolicy", {"net_arch": list(cfg.net_arch)}
    model = PPO(policy, venv, learning_rate=schedule, n_steps=cfg.n_steps, batch_size=cfg.batch_size, n_epochs=cfg.n_epochs,
                gamma=cfg.gamma, gae_lambda=cfg.gae_lambda, ent_coef=cfg.ent_coef, clip_range=cfg.clip_range, seed=cfg.seed, device="cpu",
                policy_kwargs=pkw, verbose=0)
    if cfg.agent_mode:  # start from long greens (about 18 % switch probability per decision) rather than a coin flip
        import torch as _t

        with _t.no_grad():
            model.policy.action_net.bias.copy_(_t.tensor([0.0, -1.5]))
    model.set_logger(configure(str(out), ["csv", "stdout"]))
    log_path = out / "validation.jsonl"
    log_path.write_text("")
    best = {"wait": float("inf"), "step": 0}
    t0 = time.time()
    # the validation episodes run in this (the trainer) process, which hosts no training simulation
    from .sim import SignalSim

    vsim = SignalSim("validation")

    class Validation(BaseCallback):
        def __init__(self):
            super().__init__()
            self.next_at = cfg.validate_every

        def _on_step(self) -> bool:
            if self.num_timesteps >= self.next_at:
                self.next_at += cfg.validate_every
                self._validate()
            return True

        def _validate(self) -> None:
            v0 = time.time()
            res = validate(self.model, cfg, vsim)
            res |= {"timesteps": self.num_timesteps, "train_seconds": round(time.time() - t0), "validation_seconds": round(time.time() - v0)}
            with log_path.open("a") as f:
                f.write(json.dumps(res) + "\n")
            self.model.save(str(out / "checkpoints" / f"step_{self.num_timesteps}"))
            if res["mean_wait_s"] < best["wait"]:
                best.update(wait=res["mean_wait_s"], step=self.num_timesteps)
                self.model.save(str(out / "best_validation"))
            print(f"[validation @ {self.num_timesteps}] wait {res['mean_wait_s']:.1f}s travel {res['mean_travel_s']:.1f}s "
                  f"tele {res['teleports']:.1f} coll {res['collisions']:.1f} (best {best['wait']:.1f} @ {best['step']})", flush=True)

    cb = Validation()
    cb.init_callback(model)
    model.learn(total_timesteps=cfg.total_steps, callback=cb, progress_bar=False)
    cb.num_timesteps = model.num_timesteps
    cb._validate()  # final point
    model.save(str(out / "final"))
    venv.close()
    vsim.close()
    record = {"config": asdict(cfg), "hardware": hardware(), "stable_baselines3": sb3.__version__,
              "train_wall_seconds": round(time.time() - t0), "timesteps": int(model.num_timesteps), "best_validation": best,
              "seed_pools": {"train": [1000, 1999], "selection": list(cfg.validation_seeds), "gate": list(GATE_SEEDS), "test": "3000+ (not used here)"}}
    (out / "training_run.json").write_text(json.dumps(record, indent=1))
    return record
