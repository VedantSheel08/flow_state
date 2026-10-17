"""Where and when scenario events are placed: valid, non-overlapping, reproducible and short-demo friendly.

These tests draw scenario instances only (no SUMO), so they sweep many seeds quickly.
"""

import pytest
from scenario_audit import instantiate

from flowstate.scenarios.catalog import CATALOG
from flowstate.scenarios.components import Accident, Construction, EmergencyDispatch, Pursuit, Transit

SEEDS = range(1, 61)
DURATIONS = (5, 10, 15)


def comps(sc, cls):
    return [c for c in sc.components if isinstance(c, cls)]


def segment(net, edge):
    return set(net.street_segment(edge))


def test_disruption_events_never_share_a_street_or_touch_responder_streets():
    for seed in SEEDS:
        sc, net = instantiate("disruption", seed, {"intensity": 4})
        used: list[tuple[str, set[str]]] = []
        for c in sc.components:
            for e in c.reserved_edges():
                seg = segment(net, e)
                if isinstance(c, (Accident, Construction)):
                    assert not seg & sc.protected, f"seed {seed}: {c.name}@{e} closes a hospital or police street"
                for other, oseg in used:
                    if other != c.name or isinstance(c, (Accident, Construction)):
                        assert not seg & oseg, f"seed {seed}: {c.name}@{e} overlaps {other}"
                used.append((c.name, seg))


def test_closures_and_accidents_avoid_hospital_and_police_streets():
    for seed in SEEDS:
        for sid in ("accident", "construction"):
            sc, net = instantiate(sid, seed)
            edge = sc.components[0].edge
            assert edge not in sc.protected and net.edges[edge].part != "bay", (sid, seed, edge)
    sc, net = instantiate("accident", 1)
    protected = sorted(sc.protected)
    assert protected, "hospital and police access streets should be protected"
    from flowstate.scenarios.catalog import create

    with pytest.raises(ValueError):
        create("accident", {"location": protected[0]}).instantiate(1, net)


def test_ambulance_calls_use_distinct_streets_and_leave_time_to_respond():
    for seed in SEEDS:
        for duration in DURATIONS:
            sc, net = instantiate("ambulance", seed, {"calls": 4, "duration": duration})
            ems = comps(sc, EmergencyDispatch)[0]
            segs = [segment(net, c["edge"]) for c in ems.calls]
            for i, a in enumerate(segs):
                for b in segs[i + 1:]:
                    assert not a & b, f"seed {seed}: two ambulances sent to the same block"
            for c in ems.calls:
                assert c["edge"] not in sc.protected and c["edge"] not in ems.base
                length = net.edges[c["edge"]].length
                assert min(25.0, 0.35 * length) <= c["pos"] <= max(26.0, length - 20.0), "stop too close to a junction to be reached"
                assert c["t"] <= max(0.1 * duration * 60, duration * 60 - ems.RESPONSE_MARGIN_S) + 1e-6


def test_disruption_closures_never_cover_an_ambulance_scene():
    for seed in SEEDS:
        sc, net = instantiate("disruption", seed, {"intensity": 4})
        ems = comps(sc, EmergencyDispatch)
        if not ems:
            continue
        scenes = set().union(*(segment(net, c["edge"]) for c in ems[0].calls))
        for c in comps(sc, Accident) + comps(sc, Construction):
            assert not segment(net, c.edge) & scenes, f"seed {seed}: closure on an ambulance scene"


def test_placement_varies_with_the_seed_but_repeats_for_the_same_seed():
    where = {s: instantiate("accident", s)[0].components[0].edge for s in SEEDS}
    assert len(set(where.values())) >= 12
    again = {s: instantiate("accident", s)[0].components[0].edge for s in list(SEEDS)[:10]}
    assert all(where[s] == again[s] for s in again)
    calls = {s: [c["edge"] for c in comps(instantiate("ambulance", s)[0], EmergencyDispatch)[0].calls] for s in SEEDS}
    assert len({tuple(v) for v in calls.values()}) >= 25


@pytest.mark.parametrize("sid", list(CATALOG))
@pytest.mark.parametrize("duration", DURATIONS)
def test_every_event_fits_inside_a_short_run(sid, duration):
    D = duration * 60.0
    for seed in range(1, 21):
        sc, net = instantiate(sid, seed, {"duration": duration})
        for m in sc.markers:
            assert 0.0 <= m.t <= D, f"{sid} seed {seed}: marker '{m.label}' at {m.t:.0f}s outside a {D:.0f}s run"
        for c in sc.components:
            if isinstance(c, Accident):
                assert c.t_start >= 0.05 * D
                assert c.t_clear <= 0.9 * D + 1e-6, "scene must clear with time left to watch the queue discharge"
                assert c.hard_clear <= 0.9 * D + 1e-6
                if c.closed:
                    assert c.hard_clear <= max(Accident.CLOSED_HARD_S, c.clearance), "a closed block must reopen before SUMO's 240 s teleport"
                assert c.response_delay < 30
            if isinstance(c, Pursuit):
                assert c.t0 <= 0.1 * D
            if isinstance(c, Transit):
                for ln in c.lines:
                    assert ln["deps"], "every line needs at least one departure"
                    assert ln["deps"][-1] <= max(30.0, D)


def test_instantiation_order_does_not_change_a_components_draw():
    """Emergency calls are drawn before closures, yet each component keeps the random stream of its declared slot."""
    from scenario_audit import custom_scenario

    from flowstate.api.service import NETWORKS_DIR
    from flowstate.network.info import load_net_info

    net = load_net_info(str(NETWORKS_DIR / "district.net.xml"), "district")

    def draw(factory):
        sc = custom_scenario(factory)
        sc.instantiate(7, net)
        return sc

    first = draw(lambda: [EmergencyDispatch(2), Construction()])
    again = draw(lambda: [EmergencyDispatch(2), Construction()])
    assert [c["edge"] for c in comps(first, EmergencyDispatch)[0].calls] == [c["edge"] for c in comps(again, EmergencyDispatch)[0].calls]
    assert comps(first, Construction)[0].edge == comps(again, Construction)[0].edge
    # declared the other way round the closure still cannot land on an ambulance scene
    for seed in range(1, 30):
        sc = custom_scenario(lambda: [Construction(closure="full"), EmergencyDispatch(3)])
        sc.instantiate(seed, net)
        scenes = set().union(*(segment(net, c["edge"]) for c in comps(sc, EmergencyDispatch)[0].calls))
        assert not segment(net, comps(sc, Construction)[0].edge) & scenes, seed
