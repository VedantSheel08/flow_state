"""The compared strategies: decision logic (stubbed, fast), fair execution in SUMO, fallback to the verified heuristic."""

import json
from types import SimpleNamespace

import pytest

from flowstate.rl import config as C
from flowstate.rl import controllers
from flowstate.rl.controllers import FixedTime, QueueResponsive, make
from flowstate.rl.safety import SafeSignal
from flowstate.rl.sim import ApproachView


class Stub:
    """Just enough of SignalSim for a controller to decide on: two stages (N/S and E/W) at every junction."""

    def __init__(self, views, elapsed=C.MIN_GREEN_S + 1, stage=0):
        self.views = views  # direction -> ApproachView
        greens, yellows = ["GGGgrrrrGGGgrrrr", "rrrrGGGgrrrrGGGg"], ["y" * 4 + "r" * 4] * 2
        self.signals = {j: SafeSignal(greens, yellows, stage=stage, elapsed=elapsed) for j in C.CONTROLLED}
        junction = SimpleNamespace(served=[["N", "S"], ["E", "W"]])
        self.grid = SimpleNamespace(junctions={j: junction for j in C.CONTROLLED})

    def approach(self, jid, direction):
        return self.views[direction]


def view(queue=0.0, count=None, occ=0.0, emergency=False):
    return ApproachView(queue, queue if count is None else count, 0.0, 0.0, occ, emergency, 50.0)


def test_fixed_time_switches_only_after_its_green():
    ctl = FixedTime(30.0)
    early = Stub({d: view() for d in C.DIRECTIONS}, elapsed=25.0)
    late = Stub({d: view() for d in C.DIRECTIONS}, elapsed=30.0)
    assert ctl.decide(early) == [0, 0, 0, 0] and ctl.decide(late) == [1, 1, 1, 1]


def test_heuristic_serves_the_stage_with_clearly_more_demand():
    ctl = QueueResponsive(margin=3.0, hold_s=C.MIN_GREEN_S)
    views = {"N": view(2), "S": view(1), "E": view(12), "W": view(9)}
    assert ctl.decide(Stub(views)) == [1, 1, 1, 1]
    near_tie = {"N": view(6), "S": view(5), "E": view(7), "W": view(6)}
    assert ctl.decide(Stub(near_tie)) == [0, 0, 0, 0], "a small advantage does not justify the lost time of a change"


def test_heuristic_gaps_out_an_empty_green_but_only_for_waiting_traffic():
    ctl = QueueResponsive()
    assert ctl.decide(Stub({"N": view(0), "S": view(0), "E": view(2), "W": view(0)})) == [1, 1, 1, 1]
    assert ctl.decide(Stub({d: view(0) for d in C.DIRECTIONS})) == [0, 0, 0, 0], "nobody waiting: keep the green"


def test_heuristic_never_asks_before_its_hold_and_respects_blocked_exits_and_emergencies():
    ctl = QueueResponsive(margin=1.0, hold_s=24.0)
    busy_cross = {"N": view(0), "S": view(0), "E": view(15), "W": view(15)}
    assert ctl.decide(Stub(busy_cross, elapsed=20.0)) == [0, 0, 0, 0], "inside the hold time"
    assert ctl.decide(Stub(busy_cross, elapsed=26.0)) == [1, 1, 1, 1]
    ctl = QueueResponsive()
    # the green stage's exits are full: sending more vehicles would only block the junction
    blocked = {"N": view(5, occ=0.97), "S": view(5, occ=0.97), "E": view(2), "W": view(2)}
    assert ctl.decide(Stub(blocked)) == [1, 1, 1, 1]
    # an emergency vehicle on the waiting stage wins even against a longer queue on the green stage
    emergency = {"N": view(10), "S": view(10), "E": view(0, count=1, emergency=True), "W": view(0)}
    assert ctl.decide(Stub(emergency)) == [1, 1, 1, 1]
    # ... and an emergency vehicle on the green stage keeps its green
    keep = {"N": view(0, count=1, emergency=True), "S": view(0), "E": view(30), "W": view(30)}
    assert ctl.decide(Stub(keep)) == [0, 0, 0, 0]


def test_a_request_during_the_minimum_green_is_never_decided_by_the_heuristic():
    st = Stub({"N": view(0), "S": view(0), "E": view(20), "W": view(20)}, elapsed=C.MIN_GREEN_S - 5)
    assert QueueResponsive().decide(st) == [0, 0, 0, 0]


def test_controller_ids_and_parameters_parse():
    ctl, info = make("queue_heuristic@4,24,0.5")
    assert (ctl.MARGIN, ctl.hold_s, ctl.APPROACH_WEIGHT) == (4.0, 24.0, 0.5) and not info["fallback"]
    ctl, _ = make("fixed_time@40")
    assert ctl.green_s == 40.0
    with pytest.raises(KeyError):
        make("magic_ai")


def test_learned_controller_degrades_to_the_verified_heuristic_when_it_cannot_be_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr(controllers, "artifacts_dir", lambda: tmp_path)
    ctl, info = make("rl_ppo")
    assert isinstance(ctl, QueueResponsive) and info["fallback"] and "no validated checkpoint" in info["reason"]
    assert controllers.available()["rl_ppo"]["available"] is False
    (tmp_path / "ppo_policy.zip").write_bytes(b"x")
    ctl, info = make("rl_ppo")
    assert info["fallback"] and "not validated" in info["reason"]
    (tmp_path / "validation.json").write_text(json.dumps({"passed": False, "reason": "worse than the baseline on held-out seeds"}))
    ctl, info = make("rl_ppo")
    assert info["fallback"] and "worse than the baseline" in info["reason"]
    assert isinstance(ctl, QueueResponsive) and controllers.available()["rl_ppo"]["learned"] is True


def test_catalog_lists_every_controller_with_its_availability():
    cat = controllers.available()
    assert {"fixed_time", "queue_heuristic", "rl_ppo"} <= set(cat)
    assert cat["fixed_time"]["available"] and cat["queue_heuristic"]["available"]
    assert all({"title", "description", "learned", "available"} <= set(v) for v in cat.values())
