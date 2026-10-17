"""The strategies that are compared: fixed-time, tuned fixed-time, a queue-responsive heuristic and the trained policy.

Every controller only returns a request vector (0 = hold, 1 = ask for the next stage) once per decision
interval, exactly like the RL agent, and every request goes through the same safety layer. So all strategies
share the same minimum green, yellow, all-red, maximum green and 5 s decision grid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from . import config as C

if TYPE_CHECKING:  # pragma: no cover - numpy and libsumo load lazily so the API can list controllers without them
    from .sim import SignalSim


class Controller:
    id = "controller"
    title = "Controller"
    description = ""

    def reset(self, sim: SignalSim) -> None:  # noqa: B027
        pass

    def decide(self, sim: SignalSim) -> list[int]:
        raise NotImplementedError


class FixedTime(Controller):
    """Conventional pre-timed control: every stage gets the same green, all junctions start together, no detection."""

    def __init__(self, green_s: float = 30.0, id: str = "fixed_time", title: str | None = None):
        self.green_s = float(green_s)
        self.id = id
        self.title = title or f"Fixed-time ({green_s:.0f} s greens)"
        self.description = (f"Pre-timed plan, {green_s:.0f} s of green per stage (rounded up to the 5 s decision grid), "
                            f"{C.YELLOW_S:.0f} s yellow and {C.ALL_RED_S:.0f} s all-red; ignores traffic.")

    def decide(self, sim: SignalSim) -> list[int]:
        out = []
        for jid in C.CONTROLLED:
            s = sim.signals[jid]
            out.append(1 if s.mode == "green" and s.elapsed >= self.green_s else 0)
        return out


class QueueResponsive(Controller):
    """Actuated-style heuristic: serve the stage with the larger demand, gap out empty greens, respect blocked exits.

    Uses only what a loop-detector installation could provide (stopped and approaching vehicles per approach, and
    whether the street ahead is full). No lookahead, no learning.
    """

    id = "queue_heuristic"
    title = "Queue-responsive heuristic"
    description = ("Switches when the waiting stage has clearly more demand than the green stage, gaps out an empty green, "
                   "holds when the exit is full, and gives way to an approaching emergency vehicle. Same safety layer as every strategy.")
    BLOCKED = 0.92

    def __init__(self, margin: float = 3.0, hold_s: float = C.MIN_GREEN_S, weight: float = 0.25, id: str = "queue_heuristic"):
        self.MARGIN, self.hold_s, self.APPROACH_WEIGHT, self.id = margin, hold_s, weight, id

    def _demand(self, sim: SignalSim, jid: str, stage: int) -> tuple[float, bool, bool]:
        served = sim.grid.junctions[jid].served[stage]
        total, blocked, emerg = 0.0, False, False
        for d in served:
            a = sim.approach(jid, d)
            moving = max(0.0, a.count - a.queue)
            total += a.queue + self.APPROACH_WEIGHT * moving
            blocked = blocked or a.downstream_occ >= self.BLOCKED
            emerg = emerg or a.emergency
        return total, blocked, emerg

    def decide(self, sim: SignalSim) -> list[int]:
        out = []
        for jid in C.CONTROLLED:
            s = sim.signals[jid]
            if not s.can_switch or s.elapsed < self.hold_s:
                out.append(0)
                continue
            cur, blocked, emerg_cur = self._demand(sim, jid, s.stage)
            nxt, blocked_next, emerg_next = self._demand(sim, jid, s.next_stage)
            switch = False
            if emerg_next and not emerg_cur:
                switch = True
            elif emerg_cur:
                switch = False
            elif blocked and not blocked_next and nxt >= 1.0:
                switch = True
            elif blocked_next and not blocked:
                switch = False
            elif cur < 1.0 and nxt >= 1.0:
                switch = True
            elif nxt - cur > self.MARGIN:
                switch = True
            out.append(1 if switch else 0)
        return out


class PolicyController(Controller):
    """A trained Stable-Baselines3 policy acting deterministically."""

    id = "rl_ppo"
    title = "Reinforcement-learning controller (PPO)"
    description = "Proximal Policy Optimisation policy trained in SUMO; acts through the same safety layer as every strategy."

    def __init__(self, checkpoint: "str | Path | object", id: str = "rl_ppo"):
        from stable_baselines3 import PPO

        if isinstance(checkpoint, (str, Path)):
            self.model = PPO.load(str(checkpoint), device="cpu")
            self.checkpoint = str(checkpoint)
        else:  # a model that is still in memory (validation during training)
            self.model, self.checkpoint = checkpoint, "in-memory"
        self.id = id

    def decide(self, sim: SignalSim) -> list[int]:
        import numpy as np

        from .env import OBS_V2_PER_JUNCTION, build_agent_observations, build_observation

        if self.model.observation_space.shape == (OBS_V2_PER_JUNCTION,):  # per-junction agent: one shared policy, one row per junction
            action, _ = self.model.predict(build_agent_observations(sim), deterministic=True)
        else:  # v1: one joint policy over the whole grid
            action, _ = self.model.predict(build_observation(sim), deterministic=True)
        return [int(a) for a in np.asarray(action).reshape(-1)]


REGISTRY: dict[str, Callable[[], Controller]] = {
    "fixed_time": lambda: FixedTime(30.0),
}


def artifacts_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "artifacts" / "rl"


def tuned_heuristic() -> dict[str, float] | None:
    f = artifacts_dir() / "heuristic_tuning.json"
    return json.loads(f.read_text())["best"] if f.exists() else None


def heuristic() -> QueueResponsive:
    """The queue-responsive heuristic with the parameters tuned on the training seeds (defaults if never tuned)."""
    p = tuned_heuristic()
    return QueueResponsive(**p) if p else QueueResponsive()


def tuned_fixed_green() -> float | None:
    f = artifacts_dir() / "fixed_time_tuning.json"
    if f.exists():
        return float(json.loads(f.read_text())["best_green_s"])
    return None


def available() -> dict[str, dict[str, object]]:
    """Controllers FlowState can run now, with whether the trained one passed validation."""
    out = {"fixed_time": {"title": FixedTime(30).title, "description": FixedTime(30).description, "learned": False, "available": True, "status": "baseline"},
           "queue_heuristic": {"title": QueueResponsive.title, "description": QueueResponsive.description, "learned": False, "available": True,
                               "status": "verified"}}
    tuned = tuned_fixed_green()
    if tuned is not None and tuned != 30.0:  # when tuning picks the default there is nothing extra to offer
        t = FixedTime(tuned, id="fixed_time_tuned", title=f"Fixed-time, tuned ({tuned:.0f} s greens)")
        out["fixed_time_tuned"] = {"title": t.title, "description": t.description + " Green length chosen on training seeds.", "learned": False, "available": True,
                                   "status": "baseline"}
    ok, why = rl_status()
    out["rl_ppo"] = {"title": PolicyController.title, "description": PolicyController.description, "learned": True, "available": ok,
                     "status": "verified" if ok else "experimental", "note": why}
    return out


def default_controller() -> str:
    """The production adaptive controller: the trained policy only if it passed the gate, otherwise the verified heuristic."""
    return "rl_ppo" if rl_status()[0] else "queue_heuristic"


def file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def rl_status() -> tuple[bool, str]:
    """The trained policy is offered only if its checkpoint exists, is the very file that passed validation, and the gate passed."""
    ckpt = artifacts_dir() / "ppo_policy.zip"
    gate = artifacts_dir() / "validation.json"
    if not ckpt.exists():
        return False, "no validated checkpoint; the queue-responsive heuristic is the verified adaptive controller"
    if not gate.exists():
        return False, "checkpoint present but not validated; falling back to the queue-responsive heuristic"
    v = json.loads(gate.read_text())
    if not v.get("passed"):
        return False, f"validation failed ({v.get('reason', 'see validation.json')}); falling back to the queue-responsive heuristic"
    if v.get("checkpoint_sha256") != file_sha256(ckpt):
        return False, "checkpoint does not match the one that passed validation; falling back to the queue-responsive heuristic"
    return True, "validated"


def make(controller_id: str) -> tuple[Controller, dict[str, object]]:
    """Build a controller by id. A learned controller that is unavailable degrades to the verified heuristic, and says so."""
    if controller_id == "rl_ppo":
        ok, why = rl_status()
        if not ok:
            return heuristic(), {"requested": "rl_ppo", "used": "queue_heuristic", "fallback": True, "reason": why}
        return PolicyController(artifacts_dir() / "ppo_policy.zip"), {"requested": "rl_ppo", "used": "rl_ppo", "fallback": False}
    if controller_id.startswith("rl_ppo@"):  # an explicit checkpoint, used to validate a candidate before it is offered
        path = controller_id.split("@", 1)[1]
        return PolicyController(path, id=controller_id), {"requested": controller_id, "used": controller_id, "fallback": False}
    if controller_id.startswith("queue_heuristic@"):  # explicit parameters, e.g. queue_heuristic@4,20,0.25 (used for tuning)
        m, h, w = (float(x) for x in controller_id.split("@")[1].split(","))
        return QueueResponsive(m, h, w, id=controller_id), {"requested": controller_id, "used": controller_id, "fallback": False}
    if controller_id == "queue_heuristic":
        return heuristic(), {"requested": controller_id, "used": controller_id, "fallback": False}
    if controller_id.startswith("fixed_time@"):  # explicit green length, e.g. fixed_time@25 (used for tuning)
        g = float(controller_id.split("@")[1])
        return FixedTime(g, id=controller_id), {"requested": controller_id, "used": controller_id, "fallback": False}
    if controller_id == "fixed_time_tuned":
        g = tuned_fixed_green()
        if g is None:
            return FixedTime(30.0), {"requested": controller_id, "used": "fixed_time", "fallback": True, "reason": "no tuning result"}
        return FixedTime(g, id="fixed_time_tuned", title=f"Fixed-time, tuned ({g:.0f} s greens)"), {"requested": controller_id, "used": controller_id, "fallback": False}
    if controller_id in REGISTRY:
        return REGISTRY[controller_id](), {"requested": controller_id, "used": controller_id, "fallback": False}
    raise KeyError(f"unknown controller '{controller_id}'")
