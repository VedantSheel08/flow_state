"""The RL safety layer: whatever a controller asks, the signal heads obey the clearance rules (no SUMO needed)."""

import random

from flowstate.rl import config as C
from flowstate.rl.safety import ALL_RED, GREEN, YELLOW, SafeSignal
from flowstate.sim.signals import WALK_MIN, all_red_time, flashing_dont_walk, yellow_time

GREENS = ["GGGgrrrrGGGgrrrr", "rrrrGGGgrrrrGGGg"]
YELLOWS = ["yyyyrrrryyyyrrrr", "rrrryyyyrrrryyyy"]


def drive(requests, seconds=900, seed=0):
    """Run a signal for ``seconds`` with a request policy; as the simulation does (ask, show, tick); returns the signal and the per-second trace."""
    sig = SafeSignal(GREENS, YELLOWS)
    rng = random.Random(seed)
    trace = []
    for t in range(seconds):
        if requests(rng, sig, t):
            sig.request_switch(float(t))
        trace.append((sig.mode, sig.state, sig.stage))  # what the heads show during this second
        sig.tick(1.0, float(t))
    return sig, trace


def runs(trace):
    """Collapse the per-second trace into (mode, stage, length) runs."""
    out = []
    for mode, _, stage in trace:
        if out and out[-1][0] == mode and out[-1][1] == stage:
            out[-1][2] += 1
        else:
            out.append([mode, stage, 1])
    return out


def test_constants_follow_the_signal_designer_formulas():
    assert C.YELLOW_S == yellow_time(C.SPEED_MS)
    assert C.ALL_RED_S == all_red_time(C.CROSSING_M, C.SPEED_MS)
    assert C.PED_CLEARANCE_S == flashing_dont_walk(C.CROSSING_M, C.YELLOW_S, C.ALL_RED_S)
    # a green must cover the pedestrian walk interval plus the clearance left after amber and all-red are credited
    assert C.MIN_GREEN_S >= WALK_MIN + C.PED_CLEARANCE_S
    assert C.MIN_GREEN_S < C.MAX_GREEN_S


def test_a_controller_that_requests_every_second_still_gets_full_greens_and_clearances():
    sig, trace = drive(lambda rng, s, t: True)
    for mode, stage, length in runs(trace)[1:-1]:  # the first and last runs are cut by the window
        if mode == GREEN:
            assert length >= C.MIN_GREEN_S - 1e-9, "green shorter than the pedestrian-safe minimum"
        elif mode == YELLOW:
            assert length == C.YELLOW_S
        else:
            assert length == C.ALL_RED_S
    assert sig.refused > 0, "requests inside the minimum green must be refused, not queued"


def test_random_requests_never_break_the_sequence_or_the_limits():
    for seed in range(20):
        p = random.Random(seed).uniform(0.02, 0.9)
        sig, trace = drive(lambda rng, s, t, p=p: rng.random() < p, seconds=1200, seed=seed)
        rs = runs(trace)
        order = {GREEN: YELLOW, YELLOW: ALL_RED, ALL_RED: GREEN}
        for (m1, s1, _), (m2, s2, _) in zip(rs, rs[1:]):
            assert m2 == order[m1], "yellow and all-red may never be skipped or reordered"
            assert s2 == (s1 if m1 != ALL_RED else (s1 + 1) % 2), "stages must alternate, one per cycle"
        for mode, stage, length in rs[1:-1]:
            if mode == GREEN:
                assert C.MIN_GREEN_S <= length <= C.MAX_GREEN_S
            elif mode == YELLOW:
                assert length == C.YELLOW_S
            else:
                assert length == C.ALL_RED_S


def test_a_controller_that_never_asks_is_still_switched_at_the_maximum_green():
    sig, trace = drive(lambda rng, s, t: False, seconds=600)
    greens = [length for mode, _, length in runs(trace)[:-1] if mode == GREEN]
    assert greens and all(g == C.MAX_GREEN_S for g in greens)
    assert sig.forced >= 8 and sig.refused == 0


def test_requests_during_yellow_and_all_red_are_refused():
    sig = SafeSignal(GREENS, YELLOWS)
    for t in range(int(C.MIN_GREEN_S)):
        sig.tick(1.0, float(t))
    assert sig.request_switch(20.0) and sig.mode == YELLOW
    assert not sig.request_switch(20.5) and sig.mode == YELLOW
    sig.tick(C.YELLOW_S, 25.0)
    assert sig.mode == ALL_RED and not sig.request_switch(25.0)
    sig.tick(C.ALL_RED_S, 27.0)
    assert sig.mode == GREEN and sig.stage == 1 and sig.elapsed == 0


def test_signal_state_never_shows_conflicting_greens():
    stage_a = {i for i, c in enumerate(GREENS[0]) if c in "Gg"}
    stage_b = {i for i, c in enumerate(GREENS[1]) if c in "Gg"}
    assert not stage_a & stage_b
    _, trace = drive(lambda rng, s, t: rng.random() < 0.3, seconds=800)
    for _, state, _ in trace:
        shown = {i for i, c in enumerate(state) if c in "Gg"}
        assert not (shown & stage_a and shown & stage_b), f"conflicting greens in {state}"
