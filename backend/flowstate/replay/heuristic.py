"""Queue-responsive signal control: the Intelligence Lab's hand-written queue heuristic, ported to main's signal manager so it runs on any network.

Rules, applied once a second to every signalised junction on top of its fixed plan (the plan's own phase sequence is kept; only green durations move):

* **gap out** – a green whose approaches are empty ends as soon as it has served ``MIN_GREEN_S`` while vehicles wait on red;
* **switch on demand** – a green ends early once the red approaches hold clearly more queued vehicles than the green ones;
* **extend** – a green about to end is extended, a few seconds at a time, while its approaches still hold more queue than the red ones, up to ``MAX_GREEN_S``.

Amber, all-red and pedestrian-clearance intervals are never shortened or skipped (the rule only sets the remaining duration of green phases). This is a rule, not a
learned model; it uses measured queues only (SUMO's halting counts per approach lane).
"""

from __future__ import annotations

from typing import Any

from ..sim.signals import SignalManager, SignalPolicy

MIN_GREEN_S = 10.0
MAX_GREEN_S = 60.0
EXTEND_S = 4.0
SWITCH_RATIO = 1.5
SWITCH_MARGIN = 3.0


class QueueResponsive(SignalPolicy):
    id = "queue_responsive"

    def __init__(self) -> None:
        self.gap_outs = 0
        self.switches = 0
        self.extensions = 0
        self.extended_s = 0.0
        self._lanes: dict[str, list[tuple[int, str]]] = {}  # tls -> [(link index, incoming lane)]
        self._acted: dict[str, float] = {}  # tls -> phase_start of the phase already shortened
        self._last = -1.0

    def _links(self, mgr: SignalManager, tid: str) -> list[tuple[int, str]]:
        if tid not in self._lanes:
            info = mgr.tls[tid]
            out = []
            for idx, links in enumerate(mgr.T.trafficlight.getControlledLinks(tid)):
                if idx in info.crossing_links:
                    continue
                for frm, _to, _via in links:
                    if not frm.startswith(":"):
                        out.append((idx, frm))
            self._lanes[tid] = out
        return self._lanes[tid]

    def step(self, mgr: SignalManager, t: float) -> None:
        if t - self._last < 1.0 - 1e-6:
            return
        self._last = t
        T = mgr.T
        now = T.simulation.getTime()
        for tid, info in mgr.tls.items():
            state = info.state
            if not state or not any(c in "Gg" for c in state):
                continue  # amber / all-red / clearance: never touched
            green_q = red_q = 0.0
            green_n = 0
            seen: set[str] = set()
            for idx, lane in self._links(mgr, tid):
                if lane in seen or idx >= len(state):
                    continue
                seen.add(lane)
                q = T.lane.getLastStepHaltingNumber(lane)
                if state[idx] in "Gg":
                    green_q += q
                    green_n += T.lane.getLastStepVehicleNumber(lane)
                elif state[idx] in "rR":
                    red_q += q
            elapsed = t - info.phase_start
            remaining = T.trafficlight.getNextSwitch(tid) - now
            if elapsed >= MIN_GREEN_S and self._acted.get(tid) != info.phase_start and remaining > 1.0:
                if green_n == 0 and red_q > 0:
                    self._acted[tid] = info.phase_start
                    self.gap_outs += 1
                    T.trafficlight.setPhaseDuration(tid, 0.0)
                    continue
                if red_q > SWITCH_RATIO * green_q + SWITCH_MARGIN:
                    self._acted[tid] = info.phase_start
                    self.switches += 1
                    T.trafficlight.setPhaseDuration(tid, 0.0)
                    continue
            if remaining <= 1.5 and green_q > red_q + 1 and elapsed + EXTEND_S <= MAX_GREEN_S:
                self.extensions += 1
                self.extended_s += EXTEND_S
                T.trafficlight.setPhaseDuration(tid, remaining + EXTEND_S)

    def summary(self) -> dict[str, Any]:
        return {"rule": "queue-responsive green durations on fixed plans (gap-out, switch on demand, extend; min 10 s, max 60 s)", "gapOuts": self.gap_outs,
                "switches": self.switches, "extensions": self.extensions, "extendedSeconds": round(self.extended_s, 1)}
