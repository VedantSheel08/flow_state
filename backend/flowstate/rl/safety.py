"""The safety layer between any controller (heuristic or learned) and the signal heads.

Controllers may only *ask* for a change. This state machine decides whether the request is legal and then
walks through the mandatory clearance on its own, one SUMO step at a time::

    GREEN(stage k) --request, elapsed >= MIN_GREEN--> YELLOW --> ALL-RED --> GREEN(stage k+1)

* the minimum green already contains the pedestrian WALK interval and the clearance that remains after the
  amber and all-red are credited (see :mod:`flowstate.rl.config`);
* yellow and all-red always run their full length, whatever the controller asks for;
* a green that reaches ``MAX_GREEN_S`` is ended by the layer (no approach is starved forever);
* a request that arrives before the minimum green, or during yellow / all-red, is refused and counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config as C

GREEN, YELLOW, ALL_RED = "green", "yellow", "all_red"


@dataclass
class SafeSignal:
    greens: list[str]
    yellows: list[str]
    stage: int = 0
    mode: str = GREEN
    elapsed: float = 0.0  # seconds in the current mode
    refused: int = 0
    forced: int = 0
    switches: int = 0
    history: list[tuple[float, str, int]] = field(default_factory=list)  # (t, mode, stage) on every mode change, for audits

    def __post_init__(self) -> None:
        self.all_red = "r" * len(self.greens[0])
        self.n_stages = len(self.greens)

    # -- queries ------------------------------------------------------------------------------
    @property
    def state(self) -> str:
        if self.mode == GREEN:
            return self.greens[self.stage]
        if self.mode == YELLOW:
            return self.yellows[self.stage]
        return self.all_red

    @property
    def can_switch(self) -> bool:
        return self.mode == GREEN and self.elapsed >= C.MIN_GREEN_S

    @property
    def next_stage(self) -> int:
        return (self.stage + 1) % self.n_stages

    def transition_left(self) -> float:
        if self.mode == GREEN:
            return 0.0
        return (C.YELLOW_S - self.elapsed if self.mode == YELLOW else C.ALL_RED_S - self.elapsed) + (C.ALL_RED_S if self.mode == YELLOW else 0.0)

    # -- control --------------------------------------------------------------------------------
    def request_switch(self, t: float) -> bool:
        """The controller asks for the next stage. Returns whether the request was honoured."""
        if not self.can_switch:
            self.refused += 1
            return False
        self._enter(YELLOW, t)
        self.switches += 1
        return True

    def tick(self, dt: float, t: float) -> None:
        """Advance one simulation step."""
        self.elapsed += dt
        if self.mode == GREEN and self.elapsed >= C.MAX_GREEN_S:
            self.forced += 1
            self._enter(YELLOW, t)
            self.switches += 1
        elif self.mode == YELLOW and self.elapsed >= C.YELLOW_S - 1e-9:
            self._enter(ALL_RED, t)
        elif self.mode == ALL_RED and self.elapsed >= C.ALL_RED_S - 1e-9:
            self.stage = self.next_stage
            self._enter(GREEN, t)

    def _enter(self, mode: str, t: float) -> None:
        self.mode = mode
        self.elapsed = 0.0
        self.history.append((t, mode, self.stage))
