"""Optimization v2: downstream-aware pressure control, with the queue forecast used only where it is not self-referential.

Why the first forecast-aware controller (:class:`flowstate.lab.control.ForecastAware`) did not help
(``tools/optimization/diag_holds.py``, development seeds): every one of its extra holds came from the demand shift
``beta x (p30 - q)``, none from predicted spillback. The forecaster predicts *halting* vehicles under the controllers it was
trained on, so for the green stage it predicts the queue that forms once the light turns red (+), and for the waiting stage the
discharge that follows its next green (-). Added to stage demand, both terms pull every close decision towards "keep the current
green" and cancel gap-out (46 % of the extra holds kept a green with fewer than one vehicle on it).

The controllers here keep the verified heuristic's structure and safety rules (same ``hold_s`` minimum, gap-out, emergency
pre-emption, blocked-exit test, and SafeSignal enforces minimum green, yellow, all-red and maximum green), but compare stages by
*pressure*: demand on the stage's approaches minus the queue on the approaches they discharge into (Varaiya's max-pressure, with
the heuristic's switching margin). The forecast enters only through terms that do not depend on our own next action:

* ``fc_down``: the downstream queue term uses ``max(queue now, forecast in 30 s)`` - the neighbouring junction's queue is driven by
  the neighbour, so it is a fair prediction of the storage our green will discharge into;
* ``fc_arrive``: a *waiting* stage gets ``beta x max(0, p30 - q)`` (vehicles about to join a red queue), bounded; the green stage
  gets no forecast term at all, so the forecast can only bring a switch forward, never hold an emptying green.
"""

from __future__ import annotations

from typing import Any

from ..rl import config as C
from ..rl.controllers import Controller, QueueResponsive

OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}


class DownstreamMap:
    """For every approach index: the approach indices its movements (straight, left, right) discharge into (None = leaves the grid)."""

    def __init__(self, sim: Any):
        topo = sim.topo
        by_edge = {a.edge: i for i, a in enumerate(topo.approaches)}
        self.down: list[list[int | None]] = []
        for a in topo.approaches:
            outs = sim.grid.junctions[a.jid].out_edges
            self.down.append([by_edge.get(outs[h]) for h in outs if h != a.direction])  # no U-turns on this network


class PressureControl(QueueResponsive):
    id = "pressure"
    title = "Downstream-aware pressure control"
    description = ("Queue heuristic that compares stages by pressure (own demand minus the queue on the streets they discharge into). "
                   "Same gap-out, emergency and blocked-exit rules, same SafeSignal layer.")

    def __init__(self, margin: float = 6.0, hold_s: float = 20.0, weight: float = 0.25, gamma: float = 1.0, fc_down: bool = False,
                 beta: float = 0.0, bound: float = 4.0, id: str = "pressure"):
        super().__init__(margin, hold_s, weight, id)
        self.gamma, self.fc_down, self.beta, self.bound = gamma, fc_down, beta, bound
        self._map: DownstreamMap | None = None
        self.decisions = self.changed = self.earlier = self.later = 0
        self.last_changed: list[int] = []
        self.log: list[tuple[int, int, int]] = []

    @property
    def uses_forecast(self) -> bool:
        return self.fc_down or self.beta > 0

    def reset(self, sim: Any) -> None:
        self._map = DownstreamMap(sim)
        self.decisions = self.changed = self.earlier = self.later = 0
        self.last_changed, self.log = [], []

    def _stage(self, sim: Any, jid: str, stage: int, waiting: bool, use_fc: bool) -> tuple[float, float, bool, bool]:
        """(raw demand, pressure, blocked, emergency) of one stage."""
        total, blocked, emerg = QueueResponsive._demand(self, sim, jid, stage)
        X = sim.state.X
        f = sim.forecast if use_fc else None
        idx = sim.topo.index
        down = 0.0
        arrive = 0.0
        for d in sim.grid.junctions[jid].served[stage]:
            i = idx[(jid, d)]
            ks = self._map.down[i]
            qs = []
            for k in ks:
                if k is None:
                    qs.append(0.0)
                elif f is not None and self.fc_down:
                    qs.append(max(float(X[k, 0]), min(float(f["p30"][k]), float(f["cap"][k]))))
                else:
                    qs.append(float(X[k, 0]))
            down += sum(qs) / len(qs)
            if f is not None and waiting and self.beta > 0:
                arrive += max(0.0, min(float(f["p30"][i]), float(f["cap"][i])) - float(f["q"][i]))
        bonus = min(self.bound, self.beta * arrive)
        return total, total + bonus - self.gamma * down, blocked, emerg

    def _decide(self, sim: Any, use_fc: bool) -> list[int]:
        out = []
        for jid in C.CONTROLLED:
            s = sim.signals[jid]
            if not s.can_switch or s.elapsed < self.hold_s:
                out.append(0)
                continue
            cur, p_cur, blocked, emerg_cur = self._stage(sim, jid, s.stage, False, use_fc)
            nxt, p_nxt, blocked_next, emerg_next = self._stage(sim, jid, s.next_stage, True, use_fc)
            if emerg_next and not emerg_cur:
                switch = True
            elif emerg_cur:
                switch = False
            elif blocked and not blocked_next and nxt >= 1.0:
                switch = True
            elif blocked_next and not blocked:
                switch = False
            elif cur < 1.0 and nxt >= 1.0:  # gap-out on measured demand: the forecast can never hold an empty green
                switch = True
            else:
                switch = p_nxt - p_cur > self.MARGIN
            out.append(1 if switch else 0)
        return out

    def decide(self, sim: Any) -> list[int]:
        if self._map is None:
            self.reset(sim)
        use_fc = self.uses_forecast and sim.forecast is not None
        out = self._decide(sim, use_fc)
        if use_fc:  # measure, not assume, how often the forecast changed the decision
            base = self._decide(sim, False)
            self.decisions += sum(1 for jid in C.CONTROLLED if sim.signals[jid].can_switch)
            self.last_changed = [k for k, (a, b) in enumerate(zip(base, out)) if a != b]
            for k in self.last_changed:
                self.changed += 1
                self.earlier += out[k] == 1
                self.later += out[k] == 0
                self.log.append((int(sim.t), k, 1 if out[k] == 1 else -1))
        return out

    def stats(self) -> dict[str, int]:
        return {"decisions": self.decisions, "changed": self.changed, "switchedEarlier": self.earlier, "heldLonger": self.later}


def parse(cid: str) -> PressureControl:
    """``pressure@margin,gamma`` (deterministic) or ``ml_pressure@margin,gamma,fc_down,beta``; bare ids use the selected parameters."""
    base, _, args = cid.partition("@")
    p = [float(x) for x in args.split(",")] if args else []
    if base == "pressure":
        sel = selected().get("pressure", {})
        m, g = (p + [sel.get("margin", 6.0), sel.get("gamma", 1.0)][len(p):])[:2]
        return PressureControl(margin=m, gamma=g, id=cid)
    if base == "ml_pressure":
        sel = selected().get("ml_pressure", {})
        d = [sel.get("margin", 6.0), sel.get("gamma", 1.0), sel.get("fc_down", 1.0), sel.get("beta", 0.0)]
        m, g, fd, b = (p + d[len(p):])[:4]
        return PressureControl(margin=m, gamma=g, fc_down=bool(fd), beta=b, id=cid)
    raise KeyError(cid)


def selected() -> dict[str, Any]:
    import json

    from .forecast import lab_dir

    f = lab_dir() / "optimization_v2_selection.json"
    return json.loads(f.read_text())["selected"] if f.exists() else {}


def is_candidate(cid: str) -> bool:
    return cid.split("@")[0] in ("pressure", "ml_pressure")


def make(cid: str, sim: Any) -> tuple[Controller, dict[str, Any]]:
    ctl = parse(cid)
    params = {"margin": ctl.MARGIN, "hold_s": ctl.hold_s, "weight": ctl.APPROACH_WEIGHT, "gamma": ctl.gamma, "fc_down": ctl.fc_down,
              "beta": ctl.beta, "bound": ctl.bound}
    return ctl, {"requested": cid, "used": cid, "mlInControl": ctl.uses_forecast, "params": params}
