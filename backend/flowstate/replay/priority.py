"""Ambulance green priority: a hand-written signal rule (not a learned model).

It acts only through phase *durations*, the same mechanism as transit signal priority in ``sim.signals``:

* an ambulance approaching a green within ``REACH_M`` gets that green extended (up to ``MAX_EXTENSION_S`` per phase) so it does not catch the amber;
* an ambulance held at a red within ``REACH_M`` makes the conflicting green end as soon as it has served ``MIN_GREEN_S``; the amber, all-red and pedestrian
  clearance intervals that follow are never shortened or skipped, so every signal sequence stays legal.

It cannot turn a red into a green directly, and it does nothing for ambulances more than ``REACH_M`` from their next signal.
"""

from __future__ import annotations

from typing import Any

from ..sim.signals import SignalManager, SignalPolicy

REACH_M = 120.0
MAX_EXTENSION_S = 20.0
MIN_GREEN_S = 10.0


class EmergencyPriority(SignalPolicy):
    id = "emergency_priority"

    def __init__(self) -> None:
        self.extensions = 0
        self.extended_s = 0.0
        self.cuts = 0
        self.cut_s = 0.0
        self._ext: dict[str, float] = {}  # tls -> seconds granted in its current phase
        self._cut: dict[str, int] = {}  # tls -> phase index already shortened
        self._mark: dict[str, float] = {}  # tls -> phase_start of the phase the budget belongs to

    def step(self, mgr: SignalManager, t: float) -> None:
        T = mgr.T
        eng = mgr.engine
        now = T.simulation.getTime()
        for vid in eng.emergency_active():
            try:
                upcoming = T.vehicle.getNextTLS(vid)
            except Exception:  # noqa: BLE001
                continue
            if not upcoming:
                continue
            tid, _link, dist, state = upcoming[0]
            info = mgr.tls.get(tid)
            if info is None or dist > REACH_M:
                continue
            if self._mark.get(tid) != info.phase_start:  # new phase: reset the per-phase extension budget
                self._mark[tid] = info.phase_start
                self._ext.pop(tid, None)
            remaining = T.trafficlight.getNextSwitch(tid) - now
            if state in "Gg":
                speed = max(5.0, T.vehicle.getSpeed(vid))
                need = dist / speed + 2.0
                if remaining >= need:
                    continue
                grant = min(need - remaining, MAX_EXTENSION_S - self._ext.get(tid, 0.0))
                if grant <= 0.5:
                    continue
                if tid not in self._ext:
                    self.extensions += 1
                self._ext[tid] = self._ext.get(tid, 0.0) + grant
                self.extended_s += grant
                T.trafficlight.setPhaseDuration(tid, remaining + grant)
            elif state in "rR" and ("G" in info.state or "g" in info.state) and self._cut.get(tid) != info.phase:
                # a conflicting green is running: let it end once it has served its minimum
                elapsed = t - info.phase_start
                target = max(0.0, MIN_GREEN_S - elapsed)
                if remaining - target <= 0.5:
                    continue
                self._cut[tid] = info.phase
                self.cuts += 1
                self.cut_s += remaining - target
                T.trafficlight.setPhaseDuration(tid, target)

    def summary(self) -> dict[str, Any]:
        return {"rule": "ambulance green priority (phase durations only; no skipped amber/all-red)", "extensions": self.extensions,
                "extendedSeconds": round(self.extended_s, 1), "earlyEnds": self.cuts, "earlyEndSeconds": round(self.cut_s, 1)}
