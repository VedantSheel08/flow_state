"""Gymnasium environment for connected signal control on the 2 x 2 grid (real SUMO steps through libsumo).

Decision interval: every ``DECISION_S`` (5 s) of simulated time the agent chooses, for each of the four
junctions, one of

    0  HOLD    keep the current green running for another interval (extends it)
    1  SWITCH  ask the safety layer for the next stage (granted only after the minimum green; the layer then
               runs the full yellow and all-red itself)

Whatever the agent does, :class:`~flowstate.rl.safety.SafeSignal` enforces minimum green (which includes pedestrian
WALK and clearance), full yellow and all-red, and a maximum green. Refused requests are counted, never executed.

Observation (per junction, all scaled to roughly 0..1, then concatenated):

* per approach N/E/S/W: queue / capacity, mean waiting time / 60 s, downstream occupancy, emergency vehicle present
* signal: one-hot green stage, in-transition flag, green elapsed / max green, transition time left / 6 s
* neighbours: mean queue ratio of each adjacent junction (padded to two)

Reward (per 5 s interval, measured over the *whole* network, every term scaled so a busy-but-healthy network is ~0.3):

    r = -( q + w + 2 s + p ) + 0.1 * thr - 1.0 * min(teleports + collisions, 3) - 0.25 * e

    q  halting vehicles on all approaches / (16 approaches x 10 vehicles)           queue length
    w  summed current waiting seconds of vehicles on approaches / (16 x 10 x 30 s)   waiting time
    s  fraction of the internal streets whose queue covers half the block             spillback
    p  vehicles waiting to be inserted / 16                                           insertion backlog
    thr arrivals during the interval / 5 vehicles                                     throughput
    e  stopped emergency vehicles

Each of q, w and p is clipped at 3 so one gridlocked interval cannot dominate the gradient.
Insertion backlog ``p`` is what stops the agent from "winning" by keeping vehicles out: a vehicle that cannot enter
because its entry street is jammed is counted as waiting, and demand is a fixed function of (scenario, seed).

Demand is a fixed function of (scenario, seed), independent of the agent, so the only way to score well is to
serve the vehicles that are really there.
"""

from __future__ import annotations

from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from . import config as C
from .demand import TRAIN_SCENARIOS
from .sim import SignalSim

QUEUE_REF = 10.0  # vehicles per approach regarded as a busy queue
WAIT_REF_S = 30.0  # seconds a queued vehicle has typically waited
OBS_PER_JUNCTION = 4 * 4 + 2 + 1 + 1 + 1 + 2  # approaches, stage one-hot, transition, elapsed, left, neighbours
# v2 (per-junction agent) block: approaches 16, signal 6 (stage one-hot 2, in-transition, elapsed, left, can-switch),
# demand 7 (current-stage demand, next-stage demand, their imbalance, exit blocked x2, emergency x2), context 4
# (two neighbours' queue ratios, network-wide queue ratio and waiting ratio)
OBS_V2_PER_JUNCTION = 16 + 6 + 7 + 4
APPROACH_WEIGHT = 0.25  # weight of moving vehicles in "demand", as in the queue-responsive heuristic
BLOCKED_OCC = 0.92


class SignalControlEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, scenarios: tuple[str, ...] = TRAIN_SCENARIOS, horizon_s: float = 900.0, drain_s: float = 0.0,
                 seed_pool: tuple[int, int] | None = (1000, 1999), label: str | None = None,
                 intensity_range: tuple[float, float] | None = None, obs_version: int = 1):
        super().__init__()
        self.obs_version = obs_version
        self.scenarios = tuple(scenarios)
        self.horizon_s = horizon_s
        self.drain_s = drain_s
        self.seed_pool = seed_pool
        self.intensity_range = intensity_range
        self.sim = SignalSim(label or "env")
        self.jids = list(C.CONTROLLED)
        self.action_space = spaces.MultiDiscrete([2] * len(self.jids))
        if obs_version == 1:
            self.observation_space = spaces.Box(0.0, 1.0, shape=(OBS_PER_JUNCTION * len(self.jids),), dtype=np.float32)
        else:
            self.observation_space = spaces.Box(-1.0, 1.0, shape=(OBS_V2_PER_JUNCTION * len(self.jids),), dtype=np.float32)
        self._rng = np.random.default_rng()
        self.episode_return = 0.0
        self.last_metrics: dict[str, Any] = {}

    # ------------------------------------------------------------------ gym API
    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        options = options or {}
        scenario = options.get("scenario") or str(self._rng.choice(self.scenarios))
        demand_seed = options.get("demand_seed")
        if demand_seed is None:
            lo, hi = self.seed_pool or (0, 10**6)
            demand_seed = int(self._rng.integers(lo, hi + 1))
        intensity = options.get("intensity")
        if intensity is None:
            intensity = float(self._rng.uniform(*self.intensity_range)) if self.intensity_range else 1.0
        self.sim.reset(scenario, int(demand_seed), self.horizon_s, self.drain_s, float(intensity))
        self.episode_return = 0.0
        self._prev = None
        return self._observe(), {"scenario": scenario, "demand_seed": int(demand_seed), "demand_sha": self.sim.manifest["sha256"], "intensity": float(intensity)}

    def step(self, action):
        action = np.asarray(action).reshape(-1)
        for jid, a in zip(self.jids, action):
            if int(a) == 1:
                self.sim.request(jid)
        stats = self.sim.advance(C.DECISION_S)
        reward = self._reward(stats)
        self.episode_return += reward
        truncated = self.sim.done
        info: dict[str, Any] = {"arrivals": stats["arrivals"], "junction_rewards": self._local_rewards()}
        if truncated:
            self.last_metrics = self.sim.metrics()
            info["episode_metrics"] = self.last_metrics
            info["episode_return"] = self.episode_return
        return self._observe(), float(reward), False, truncated, info

    def close(self) -> None:
        self.sim.close()

    # ------------------------------------------------------------------ internals
    def _reward(self, stats: dict[str, float]) -> float:
        s = self.sim
        n_app = 4 * len(self.jids)
        queue = sum(s.approach(j, d).queue for j in self.jids for d in C.DIRECTIONS)
        q = min(3.0, queue / (n_app * QUEUE_REF))
        w = min(3.0, s.waiting_seconds() / (n_app * QUEUE_REF * WAIT_REF_S))
        spill = s.spill_fraction()
        p = min(3.0, s.pending() / n_app)
        thr = min(1.0, stats["arrivals"] / 5.0)
        e = min(1.0, s.emergency_waiting())
        penalty = min(stats["teleports"] + stats["collisions"], 3)
        return float(-(q + w + 2.0 * spill + p) + 0.1 * thr - 1.0 * penalty - 0.25 * e)

    def _local_rewards(self) -> np.ndarray:
        """Each junction's own cost over its four approaches (same scale and terms as the global reward, minus throughput)."""
        out = []
        for jid in self.jids:
            apps = [self.sim.approach(jid, d) for d in C.DIRECTIONS]
            q = min(3.0, sum(a.queue for a in apps) / (4 * QUEUE_REF))
            w = min(3.0, sum(a.queue * a.wait_s for a in apps) / (4 * QUEUE_REF * WAIT_REF_S))
            spill = sum(1.0 for a in apps if a.downstream_occ >= 0.5) / 4.0
            e = min(1.0, sum(1.0 for a in apps if a.emergency and a.queue > 0))
            out.append(-(q + w + 2.0 * spill) - 0.25 * e)
        return np.asarray(out, dtype=np.float32)

    def _observe(self) -> np.ndarray:
        if self.obs_version == 2:
            return build_agent_observations(self.sim).reshape(-1)
        return build_observation(self.sim)


def build_observation(s: SignalSim) -> np.ndarray:
    """The observation vector for the current state of ``s`` (also used when a trained policy controls a run)."""
    jids = list(C.CONTROLLED)
    queue_ratio: dict[str, float] = {}
    per_j: dict[str, list[float]] = {}
    for jid in jids:
        feats: list[float] = []
        tot_q = tot_c = 0.0
        for d in C.DIRECTIONS:
            a = s.approach(jid, d)
            tot_q += a.queue
            tot_c += a.capacity
            feats += [min(1.0, a.queue / a.capacity), min(1.0, a.wait_s / 60.0), a.downstream_occ, 1.0 if a.emergency else 0.0]
        queue_ratio[jid] = tot_q / tot_c
        sig = s.signals[jid]
        feats += [1.0 if sig.stage == k else 0.0 for k in range(2)]
        feats += [0.0 if sig.mode == "green" else 1.0, min(1.0, sig.elapsed / C.MAX_GREEN_S) if sig.mode == "green" else 0.0,
                  min(1.0, sig.transition_left() / (C.YELLOW_S + C.ALL_RED_S))]
        per_j[jid] = feats
    out: list[float] = []
    for jid in jids:
        nb = [queue_ratio[n] for n in s.grid.junctions[jid].neighbours][:2]
        out += per_j[jid] + nb + [0.0] * (2 - len(nb))
    return np.asarray(out, dtype=np.float32)


def stage_demand(s: SignalSim, jid: str, stage: int) -> tuple[float, bool, bool]:
    """Queued plus (weighted) approaching vehicles on the approaches a stage serves, whether an exit is full, emergency present."""
    total, blocked, emerg = 0.0, False, False
    for d in s.grid.junctions[jid].served[stage]:
        a = s.approach(jid, d)
        total += a.queue + APPROACH_WEIGHT * max(0.0, a.count - a.queue)
        blocked = blocked or a.downstream_occ >= BLOCKED_OCC
        emerg = emerg or a.emergency
    return total, blocked, emerg


def build_agent_observations(s: SignalSim) -> np.ndarray:
    """v2 observation, one row per junction (rows are what a single parameter-shared agent sees), values in -1..1."""
    jids = list(C.CONTROLLED)
    ratio: dict[str, float] = {}
    wait: dict[str, float] = {}
    apps: dict[str, list] = {}
    for jid in jids:
        apps[jid] = [s.approach(jid, d) for d in C.DIRECTIONS]
        ratio[jid] = sum(a.queue for a in apps[jid]) / sum(a.capacity for a in apps[jid])
        wait[jid] = sum(min(1.0, a.wait_s / 60.0) for a in apps[jid]) / 4.0
    g_q, g_w = float(np.mean(list(ratio.values()))), float(np.mean(list(wait.values())))
    rows = []
    for jid in jids:
        sig = s.signals[jid]
        f: list[float] = []
        for a in apps[jid]:
            f += [min(1.0, a.queue / a.capacity), min(1.0, a.wait_s / 60.0), a.downstream_occ, 1.0 if a.emergency else 0.0]
        f += [1.0 if sig.stage == k else 0.0 for k in range(2)]
        f += [0.0 if sig.mode == "green" else 1.0, min(1.0, sig.elapsed / C.MAX_GREEN_S) if sig.mode == "green" else 0.0,
              min(1.0, sig.transition_left() / (C.YELLOW_S + C.ALL_RED_S)), 1.0 if sig.can_switch else 0.0]
        cur, b_cur, e_cur = stage_demand(s, jid, sig.stage)
        nxt, b_nxt, e_nxt = stage_demand(s, jid, sig.next_stage)
        norm = 2.0 * QUEUE_REF
        f += [min(1.0, cur / norm), min(1.0, nxt / norm), max(-1.0, min(1.0, (nxt - cur) / norm)),
              1.0 if b_cur else 0.0, 1.0 if b_nxt else 0.0, 1.0 if e_cur else 0.0, 1.0 if e_nxt else 0.0]
        nb = [ratio[n] for n in s.grid.junctions[jid].neighbours][:2]
        f += nb + [0.0] * (2 - len(nb)) + [g_q, g_w]
        rows.append(f)
    return np.asarray(rows, dtype=np.float32)
