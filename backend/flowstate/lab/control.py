"""Forecast-aware signal control: the verified queue-responsive heuristic, with bounded adjustments from the ML forecast.

The base rule is unchanged (:class:`flowstate.rl.controllers.QueueResponsive`, tuned parameters). The forecast enters in two
places only, both bounded:

* **anticipated demand** - each stage's demand is shifted by ``beta x (predicted queue in 30 s - queue now)`` for the approaches
  it serves, clipped to +/- ``bound`` vehicles, so a platoon about to arrive at a red approach counts before it stops;
* **predicted spillback** - if the approach a stage discharges into is forecast to be nearly full, the stage is treated as
  blocked (the base rule's own "exit full" logic), so the controller stops feeding a street that is about to back up.

Every request still goes through SafeSignal (minimum green incl. pedestrian clearance, full yellow and all-red, maximum green),
and emergency-vehicle handling is the base rule's. For every decision the controller also computes what the plain heuristic
would have done, so the number of decisions the ML actually changed is measured, not assumed.
"""

from __future__ import annotations

from typing import Any

from ..rl import config as C
from ..rl.controllers import Controller, QueueResponsive, make, tuned_heuristic


class ForecastAware(QueueResponsive):
    id = "ml_forecast"
    title = "Queue heuristic + ML forecast"
    description = ("The verified queue-responsive heuristic, with demand shifted by the 30 s queue forecast (bounded) and predicted "
                   "spillback treated as a blocked exit. Same SafeSignal layer.")

    def __init__(self, beta: float = 0.5, bound: float = 6.0, spill: float = 0.85, margin: float = 3.0, hold_s: float = C.MIN_GREEN_S,
                 weight: float = 0.25, id: str = "ml_forecast"):
        super().__init__(margin, hold_s, weight, id)
        self.beta, self.bound, self.spill = beta, bound, spill
        self._ml = True
        self.decisions = self.changed = self.earlier = self.later = 0
        self.last_changed: list[int] = []
        self.log: list[tuple[int, int, int]] = []  # (t, junction index, +1 switched earlier / -1 held longer)

    def reset(self, sim: Any) -> None:
        self.decisions = self.changed = self.earlier = self.later = 0
        self.last_changed, self.log = [], []

    def _demand(self, sim: Any, jid: str, stage: int) -> tuple[float, bool, bool]:
        total, blocked, emerg = super()._demand(sim, jid, stage)
        f = sim.forecast
        if not self._ml or f is None:
            return total, blocked, emerg
        topo = sim.topo
        shift = 0.0
        for d in sim.grid.junctions[jid].served[stage]:
            i = topo.index[(jid, d)]
            shift += min(float(f["p30"][i]), float(f["cap"][i])) - float(f["q"][i])
            k = topo.feeds[i]
            if k is not None and float(f["p30"][k]) >= self.spill * float(f["cap"][k]):
                blocked = True
        return total + max(-self.bound, min(self.bound, self.beta * shift)), blocked, emerg

    def decide(self, sim: Any) -> list[int]:
        self._ml = False
        base = super().decide(sim)
        self._ml = True
        out = super().decide(sim)
        if sim.forecast is not None:
            self.decisions += sum(1 for jid in C.CONTROLLED if sim.signals[jid].can_switch)
        self.last_changed = [k for k, (a, b) in enumerate(zip(base, out)) if a != b]
        for k in self.last_changed:
            self.changed += 1
            if out[k] == 1:
                self.earlier += 1
            else:
                self.later += 1
            self.log.append((int(sim.t), k, 1 if out[k] == 1 else -1))
        return out

    def stats(self) -> dict[str, int]:
        return {"decisions": self.decisions, "changed": self.changed, "switchedEarlier": self.earlier, "heldLonger": self.later}


def ml_params() -> dict[str, float]:
    from .forecast import lab_dir

    import json

    f = lab_dir() / "ml_control_tuning.json"
    return json.loads(f.read_text())["best"] if f.exists() else {}


CONTROLLERS = {
    "fixed_time": "Fixed-time (30 s greens)",
    "queue_heuristic": "Queue-responsive heuristic (no ML)",
    "ml_forecast": "Queue heuristic + ML forecast",
}


def make_controller(cid: str, sim: Any, *, shadow_forecast: bool = True) -> tuple[Controller, dict[str, Any]]:
    """Build a lab controller. Attaches the forecaster to ``sim`` (shadow-only for non-ML controllers: shown, never used)."""
    from .forecast import Forecaster

    fc = Forecaster.load_default()
    if cid.startswith("ml_forecast"):
        if fc is None:
            raise RuntimeError("no trained forecaster: run `python -m flowstate.lab.cli train` first")
        sim.forecaster = fc
        params = {**(tuned_heuristic() or {}), **ml_params()}
        if "@" in cid:  # explicit parameters for tuning: ml_forecast@beta,bound,spill
            b, bd, sp = (float(x) for x in cid.split("@")[1].split(","))
            params |= {"beta": b, "bound": bd, "spill": sp}
        ctl = ForecastAware(**params, id=cid)
        return ctl, {"requested": cid, "used": cid, "mlInControl": True, "params": params}
    sim.forecaster = fc if shadow_forecast else None
    ctl, info = make(cid)
    return ctl, info | {"mlInControl": False}
