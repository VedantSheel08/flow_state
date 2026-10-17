"""Signal timing: clearance formulas, phase legality, pedestrian clearance, corridor offsets and
demand guardrails — checked on the built networks and in real SUMO runs.

Assumptions behind the numbers are documented in ``flowstate/sim/signals.py`` and
``flowstate/sim/demand.py``; nothing here is a calibration to measured counts of a real city.
"""

import json
import xml.etree.ElementTree as ET

import pytest

from flowstate.api.service import NETWORKS_DIR
from flowstate.network.info import load_net_info
from flowstate.sim import signals as S
from helpers import make_engine, run_until

NETS = ("district", "district_bike")


# ---------------------------------------------------------------------------
# Formulas and phase classification (pure functions)
# ---------------------------------------------------------------------------
def test_yellow_follows_the_ite_kinematic_formula():
    assert S.yellow_time(50 / 3.6) == 4.0  # 1.0 + 15.3 / (2 * 3.05)
    assert S.yellow_time(40 / 3.6) == 3.5
    assert S.yellow_time(80 / 3.6) == S.MAX_YELLOW  # clamped
    assert S.yellow_time(10 / 3.6) == S.MIN_YELLOW
    assert S.yellow_time(50 / 3.6, left=True) == S.LEFT_YELLOW


def test_all_red_clears_the_crossed_street_and_never_below_the_floor():
    assert S.all_red_time(19.8, 50 / 3.6) >= (19.8 + S.VEHICLE_LENGTH) / (50 / 3.6 * S.V85_FACTOR)
    assert S.all_red_time(7.2, 50 / 3.6) == S.MIN_ALL_RED
    assert S.all_red_time(19.8, 40 / 3.6) > S.all_red_time(19.8, 50 / 3.6)  # slower traffic needs longer


def test_pedestrian_clearance_covers_the_crossing():
    for width in (7.2, 10.4, 19.8):
        y, ar = 4.0, S.all_red_time(width, 50 / 3.6)
        fdw = S.flashing_dont_walk(width, y, ar)
        # flashing don't walk + amber + all-red, minus the 1 s buffer, lets a 1.07 m/s pedestrian finish
        assert fdw + y + ar - S.PED_BUFFER >= width / S.PED_SPEED - 1e-9
        assert fdw + y + ar - S.PED_BUFFER < width / S.PED_SPEED + 1.0  # but is not wasteful
    assert S.flashing_dont_walk(19.8, 4.0, 2.0) >= 13


NETCONVERT_STAGE = [
    "gGGgrrrrgGGgrrrrrGrG", "gGGgrrrrgGGgrrrrrrrr", "yyygrrrryyygrrrrrrrr", "rrrGrrrrrrrGrrrrrrrr", "rrryrrrrrrryrrrrrrrr", "rrrrrrrrrrrrrrrrrrrr",
    "rrrrgGGgrrrrgGGgGrGr", "rrrrgGGgrrrrgGGgrrrr", "rrrryyyyrrrryyyyrrrr", "rrrrrrrrrrrrrrrrrrrr",
]


def test_phase_roles_recognise_the_netconvert_sequence():
    roles = S.phase_roles(NETCONVERT_STAGE)
    assert roles == [S.GREEN, S.CLEAR, S.YELLOW, S.LEFT, S.LEFT_YELLOW_ROLE, S.ALL_RED, S.GREEN, S.CLEAR, S.YELLOW, S.ALL_RED]
    # with the pedestrian links known, a crossing that turns red is still just pedestrian clearance
    assert S.phase_roles(NETCONVERT_STAGE, crossing={16, 17, 18, 19}) == roles


def test_crossing_links_are_inferred_from_the_absence_of_amber():
    assert S.infer_crossings(NETCONVERT_STAGE) == {16, 17, 18, 19}


def test_split_stage_all_red_keeps_the_crosswalk_green_but_is_still_an_all_red():
    # direction A amber, then a short all-red with the walk still showing, then direction B
    states = ["rrrrgGrrrrrGrG", "rrrryyrrrrrGrG", "rrrrrrrrrrrGrG", "rrrrrrgGrrrGrG", "rrrrrrgGrrrrrr", "rrrrrryyrrrrrr", "rrrrrrrrrrrrrr",
              "gGrrrrrrrrrrrr", "yyrrrrrrrrrrrr", "rrrrrrrrrrrrrr"]
    crossings = S.infer_crossings(states)
    assert crossings == {11, 13}  # link 12 is never walked in this toy program
    roles = S.phase_roles(states, crossings)
    assert roles[:7] == [S.GREEN, S.YELLOW, S.ALL_RED, S.GREEN, S.CLEAR, S.YELLOW, S.ALL_RED]


def test_actuated_programs_never_shorten_clearance_intervals(tmp_path):
    info = load_net_info(str(NETWORKS_DIR / "district.net.xml"), "district")
    out = tmp_path / "act.xml"
    S.write_actuated_programs(info.tls_programs, out)
    root = ET.parse(out).getroot()
    assert len(root.findall("tlLogic")) == len(info.tls_programs)
    for tl in root.findall("tlLogic"):
        phases = tl.findall("phase")
        states = [p.get("state") for p in phases]
        roles = S.phase_roles(states, S.infer_crossings(states))
        assert S.ALL_RED in roles and S.YELLOW in roles
        for p, role in zip(phases, roles):
            if role in (S.CLEAR, S.YELLOW, S.LEFT_YELLOW_ROLE, S.ALL_RED):
                assert p.get("minDur") is None and p.get("maxDur") is None, (tl.get("id"), role)
            else:
                assert 0 < float(p.get("minDur")) <= float(p.get("duration"))
                if role == S.GREEN and float(p.get("duration")) >= 10:  # a walk phase keeps at least the 7 s WALK
                    assert float(p.get("minDur")) >= S.WALK_MIN, (tl.get("id"), p.get("state"))


# ---------------------------------------------------------------------------
# The built networks
# ---------------------------------------------------------------------------
def read_programs(net_id):
    """tlLogic id -> (offset, [(duration, state, name)]) straight from the .net.xml."""
    root = ET.parse(NETWORKS_DIR / f"{net_id}.net.xml").getroot()
    out = {}
    for tl in root.iter("tlLogic"):
        out[tl.get("id")] = (float(tl.get("offset")), [(float(p.get("duration")), p.get("state"), p.get("name")) for p in tl.iter("phase")])
    return out


def cyclic_pairs(seq):
    return zip(seq, seq[1:] + seq[:1])


@pytest.mark.parametrize("net_id", NETS)
def test_every_program_is_a_legal_signal_sequence(net_id):
    designs = S.read_signal_designs(NETWORKS_DIR / f"{net_id}.net.xml")
    assert len(designs) >= 11
    for tid, (offset, phases) in read_programs(net_id).items():
        d = designs[tid]
        veh = sorted(d.links)
        states = [s for _, s, _ in phases]
        # the cycle is one of the type cycles, and the offset lies inside it
        cycle = sum(dur for dur, _, _ in phases)
        assert cycle == S.TYPE_PLANS[d.kind].cycle, (tid, d.kind, cycle)
        assert 0 <= offset < cycle
        for (dur_a, a, _), (dur_b, b, _) in cyclic_pairs(phases):
            for i in veh:
                # a green may only end through an amber, never directly into red
                assert not (a[i] in "Gg" and b[i] == "r"), (tid, i, a, b)
                # amber only follows green or amber, and is always followed by red or amber
                assert not (b[i] == "y" and a[i] not in "Ggy"), (tid, i, a, b)
                assert not (a[i] == "y" and b[i] in "Gg"), (tid, i, a, b)
        for dur, state, name in phases:
            if any(state[i] == "y" for i in veh):
                assert S.MIN_YELLOW <= dur <= S.MAX_YELLOW, (tid, name, dur)
            if not any(state[i] in "Gg" for i in veh) and not any(state[i] == "y" for i in veh):
                assert dur >= S.MIN_ALL_RED, (tid, name, dur)
        # no two phases give green to a link and to a conflicting link of the opposite axis at once
        for _, state, _ in phases:
            axes = {d.links[i].axis for i in veh if state[i] in "Gg" and d.links[i].dir in "sS"}
            assert len(axes) <= 1, (tid, state)


def crossing_info(net_id):
    """crossing link index -> (tls, crossing length, ids of the edges that approach the crossed leg)."""
    root = ET.parse(NETWORKS_DIR / f"{net_id}.net.xml").getroot()
    length, crossed = {}, {}
    for e in root.iter("edge"):
        if e.get("function") == "crossing":
            length[e.get("id")] = float(e.find("lane").get("length"))
            crossed[e.get("id")] = set(e.get("crossingEdges", "").split())
    out = {}
    for c in root.iter("connection"):
        tl, idx, to = c.get("tl"), c.get("linkIndex"), c.get("to", "")
        if tl and idx and to in length:
            out[(tl, int(idx))] = (length[to], crossed[to])
    return out


def conflicting_links(net_id):
    """(tls, link index) of the vehicle links that start on an edge of the given set."""
    root = ET.parse(NETWORKS_DIR / f"{net_id}.net.xml").getroot()
    return [(c.get("tl"), int(c.get("linkIndex")), c.get("from")) for c in root.iter("connection") if c.get("tl") and not c.get("from", "").startswith(":")]


@pytest.mark.parametrize("net_id", NETS)
def test_pedestrians_get_a_walk_and_a_full_clearance_before_conflicting_traffic(net_id):
    programs = read_programs(net_id)
    crossings = crossing_info(net_id)
    approach = conflicting_links(net_id)
    checked = 0
    for (tid, idx), (length, crossed) in crossings.items():
        phases = programs[tid][1]
        n = len(phases)
        green = [p[1][idx] == "G" for p in phases]
        if not any(green):
            continue
        conflicts = [i for t, i, frm in approach if t == tid and frm in crossed]
        assert conflicts, (tid, idx)
        start = next(k for k in range(n) if green[k] and not green[k - 1])  # first phase of a walk run
        k, walk = start, 0.0
        while green[k % n]:
            walk += phases[k % n][0]
            k += 1
        assert walk >= S.WALK_MIN, (tid, idx, walk)
        # from the end of the walk to the next green of the crossed street's own approaches
        t = 0.0
        while not any(phases[k % n][1][i] in "Gg" for i in conflicts):
            t += phases[k % n][0]
            k += 1
        assert t >= length / S.PED_SPEED + S.PED_BUFFER - 1e-9, (tid, idx, length, t)
        checked += 1
    assert checked >= 40


def test_cycle_length_and_splits_follow_the_intersection_type():
    designs = S.read_signal_designs(NETWORKS_DIR / "district.net.xml")
    programs = read_programs("district")
    kinds = {t: d.kind for t, d in designs.items()}
    assert set(kinds.values()) == {"arterial_arterial", "arterial_collector", "arterial_local", "collector_collector"}
    for t, kind in kinds.items():
        cycle = sum(p[0] for p in programs[t][1])
        assert cycle == (40 if kind == "collector_collector" else 80), (t, kind, cycle)
    # an arterial that meets a local street is served much longer than the local street (which is nevertheless
    # held to its pedestrian-clearance minimum); two arterials share the cycle equally
    def served_time(tid, axis):
        d = designs[tid]
        idx = [i for i, l in d.links.items() if l.axis == axis]
        return sum(dur for dur, s, _ in programs[tid][1] if any(s[i] in "Ggy" for i in idx))

    art_local = next(t for t, k in kinds.items() if k == "arterial_local" and designs[t].streets["ew"][1] == 3)
    assert served_time(art_local, "ew") > 1.3 * served_time(art_local, "ns")
    assert served_time(art_local, "ns") >= 20  # 7 s walk + clearance across a 20 m arterial
    aa = next(t for t, k in kinds.items() if k == "arterial_arterial")
    assert abs(served_time(aa, "ew") - served_time(aa, "ns")) <= 4
    # arterial protected lefts exist at arterial x arterial; the collector-only signal has none
    assert any(n == "left" for _, _, n in programs[aa][1])
    assert not any(n == "left" for _, _, n in programs["n2_0"][1])


def test_collector_directions_are_split_so_permissive_lefts_cannot_deadlock():
    designs = S.read_signal_designs(NETWORKS_DIR / "district.net.xml")
    d = designs["n2_1"]  # King (arterial) x Church (collector)
    assert d.kind == "arterial_collector"
    church = [i for i, l in d.links.items() if l.axis == "ns"]
    for _, state, _ in read_programs("district")["n2_1"][1]:
        served = {d.links[i].sign for i in church if state[i] in "Gg"}
        assert len(served) <= 1, state  # never both directions at once
    both = {sign for _, state, _ in read_programs("district")["n2_1"][1] for i in church if state[i] in "Gg" for sign in [d.links[i].sign]}
    assert both == {1, -1}  # but both are served


@pytest.mark.parametrize("net_id", NETS)
def test_corridor_offsets_are_engineered_and_reduce_waiting(net_id):
    programs = read_programs(net_id)
    layout = json.loads((NETWORKS_DIR / f"{net_id}.city.json").read_text(encoding="utf-8"))
    summary = layout["signals"]
    assert {s["offset"] for s in summary["signals"].values()} == {offset for offset, _ in programs.values()}
    assert len({o for o, _ in programs.values()}) >= 6  # not the old all-zero pattern
    arterial = [summary["corridors"][n] for n in ("King Street", "Queen Street", "Main Street")]
    legs = [l for c in arterial for l in c["legs"]]
    # waiting of through traffic (in the design model, 85 % of the limit, one platoon per 2 s) roughly halves ...
    assert sum(l["waitSec"] for l in legs) < 0.7 * sum(l["waitSecNoOffsets"] for l in legs)
    assert sum(l["stops"] for l in legs) < 0.7 * sum(l["stopsNoOffsets"] for l in legs)
    # ... and no direction is sacrificed for the other
    for leg in legs:
        assert leg["waitSec"] < leg["waitSecNoOffsets"]
        assert leg["waitSec"] < 45


# ---------------------------------------------------------------------------
# SUMO runs
# ---------------------------------------------------------------------------
def test_sumo_executes_the_plan_with_its_offsets_and_durations():
    eng = make_engine("rush_hour", 3, {"duration": 5, "pedestrians": False}, "sigplan")
    eng.start()
    programs = read_programs(eng.network_id)
    watch = ("n1_1", "n2_1", "n2_0", "n0_2")
    changes = {t: [] for t in watch}
    last = {t: None for t in watch}
    try:
        while eng.t < 330:
            eng.step()
            now = eng.T.simulation.getTime()
            for t in watch:
                ph = eng.tls_phase[t]
                if ph != last[t]:
                    changes[t].append((now, ph))
                    last[t] = ph
        for t in watch:
            offset, phases = programs[t]
            cycle = sum(d for d, _, _ in phases)
            # the first phase of the program starts at simulation time = offset (mod cycle)
            starts = [now for now, ph in changes[t] if ph == 0][1:]
            assert starts, t
            for now in starts:
                assert abs(((now - offset + cycle / 2) % cycle) - cycle / 2) <= 0.6, (t, now, offset, cycle)
            # every completed phase lasts as planned
            for (t0, ph), (t1, _) in zip(changes[t][1:], changes[t][2:]):
                assert abs((t1 - t0) - phases[ph][0]) <= 0.6, (t, ph, t1 - t0, phases[ph][0])
    finally:
        eng.close()


def test_signal_states_stay_legal_while_vehicles_and_pedestrians_use_them():
    eng = make_engine("rush_hour", 5, {"duration": 5}, "siglegal")
    eng.start()
    prev = {}
    designs = S.read_signal_designs(NETWORKS_DIR / f"{eng.network_id}.net.xml")
    try:
        def check(e):
            for tid, state in e.tls_state.items():
                if tid in prev and prev[tid] != state:
                    for i in designs[tid].links:
                        assert not (prev[tid][i] in "Gg" and state[i] == "r"), (tid, prev[tid], state)
                prev[tid] = state

        run_until(eng, 200, check)
        assert eng.teleports == 0
    finally:
        eng.close()


def test_rush_hour_is_busy_but_never_gridlocks():
    """15 minutes of the AM peak: substantial queues, no teleports (SUMO's deadlock resolution)."""
    eng = make_engine("rush_hour", 42, {"duration": 15}, "sigrush")
    eng.start()
    peak_active = 0
    try:
        def watch(e):
            nonlocal peak_active
            peak_active = max(peak_active, len(e.vehicles))

        run_until(eng, 15 * 60, watch)
        assert eng.teleports == 0
        assert peak_active >= 120  # visible traffic in a 600 m x 500 m district
        assert max(h["queued"] for h in eng.metrics.history) >= 40  # real queues at the signals
        assert eng.metrics.completed >= 400
        assert eng.demand.throttle > 0.5 and eng.demand.deferred < 0.1 * eng.demand.counter  # the guardrail did not choke demand
    finally:
        eng.close()


def test_actuated_signals_still_run_and_keep_clearances():
    eng = make_engine("rush_hour", 7, {"duration": 5, "signals": "actuated"}, "sigact")
    eng.start()
    try:
        run_until(eng, 120)
        assert eng.teleports == 0 and eng.metrics.completed > 5
        assert {eng.T.trafficlight.getProgram(t) for t in eng.T.trafficlight.getIDList()} == {"actuated"}
    finally:
        eng.close()


# ---------------------------------------------------------------------------
# Demand calibration and guardrails
# ---------------------------------------------------------------------------
def test_gateways_are_weighted_by_street_class():
    eng = make_engine("rush_hour", 1, {"duration": 5}, "sigdem")
    eng.start()
    try:
        dem = eng.demand
        counts = {}
        for _ in range(6000):
            e = dem._side_edge("W", True)
            cls = eng.net.edges[e].cls
            counts[cls] = counts.get(cls, 0) + 1
        per_edge = {c: counts[c] / sum(1 for e in eng.net.entries["W"] if eng.net.edges[e].cls == c) for c in counts}
        assert per_edge["arterial"] > 2.5 * per_edge["collector"] > 0
        assert per_edge["collector"] > 1.5 * per_edge["local"]
    finally:
        eng.close()


def test_demand_guardrails_gate_full_streets_and_throttle_on_spillback():
    eng = make_engine("rush_hour", 1, {"duration": 5}, "siggate")
    eng.start()
    try:
        dem = eng.demand
        entry = eng.net.entries["W"][0]
        assert not dem.crowded(entry)
        cap = dem._capacity(entry)
        eng.edge_stats = {e: (int(cap), int(cap), 0.0) for e in dem._seg(entry)}  # the whole approach is queued
        assert dem.crowded(entry)
        assert dem.update_throttle(1000.0) < 1.0
        # when every block is jammed the generation rate falls to its floor but never to zero
        eng.edge_stats = {e: (999, 999, 0.0) for e in eng.net.edges}
        assert dem.update_throttle(2000.0) == dem.MIN_THROTTLE > 0
        eng.edge_stats = {}
        assert dem.update_throttle(3000.0) == 1.0
        before = dem.counter
        for _ in range(20):
            dem.spawn_trip(0.0, eng.scenario.demand(0.0), tag="scenario")  # tagged scenario trips are never deferred
        assert dem.counter > before
    finally:
        eng.close()


# ---------------------------------------------------------------------------
# Emergency pre-emption (fake TraCI: the decisions, not SUMO, are under test)
# ---------------------------------------------------------------------------
class _FakeTraci:
    def __init__(self):
        self.now = 100.0
        self.next_tls: dict[str, list] = {}
        self.speed: dict[str, float] = {}
        self.switch = 130.0
        self.durations: list[tuple[str, float]] = []
        self.vehicle = self.trafficlight = self.simulation = self

    def getNextTLS(self, vid):
        return self.next_tls[vid]

    def getSpeed(self, vid):
        return self.speed[vid]

    def getNextSwitch(self, tid):
        return self.switch

    def getTime(self):
        return self.now

    def setPhaseDuration(self, tid, d):
        self.durations.append((tid, d))


class _FakeEngine:
    def __init__(self, T):
        self.T = T
        self.active: list[str] = []

    def emergency_active(self):
        return self.active

    def emit(self, *a, **kw):
        return None

    def junction_name(self, tid):
        return tid

    def label(self, vid):
        return vid


def _manager():
    T = _FakeTraci()
    eng = _FakeEngine(T)
    mgr = S.SignalManager(eng, "fixed", True, False)
    mgr.tls["J"] = S.TlsInfo("J", crossing_links={3})
    return T, eng, mgr


def _phase(mgr, t, state, prev="rrrr"):
    info = mgr.tls["J"]
    info.prev_state, info.state, info.phase, info.phase_start = prev, state, info.phase + 1, t


def test_preemption_advances_through_conflicting_greens_to_the_serving_phase():
    T, eng, mgr = _manager()
    eng.active = ["amb1"]
    T.speed["amb1"] = 12.0
    T.next_tls["amb1"] = [("J", 0, 120.0, "r")]
    _phase(mgr, 80.0, "rGrr")  # conflicting green, 20 s old: ended at once
    mgr.step(100.0)
    assert T.durations == [("J", 0.0)] and mgr.tls["J"].held_by == "amb1" and mgr.tls["J"].advancing
    T.durations.clear()
    _phase(mgr, 101.0, "ryrr", "rGrr")  # amber always runs in full
    mgr.step(103.0)
    assert T.durations == []
    _phase(mgr, 105.0, "rrGr", "rrrr")  # the next conflicting green only needs the short minimum while advancing
    mgr.step(106.0)
    assert T.durations == []
    mgr.step(107.5)
    assert T.durations == [("J", 0.0)]
    T.durations.clear()
    T.next_tls["amb1"] = [("J", 0, 40.0, "G")]  # now served: the green is held until the unit has crossed
    _phase(mgr, 112.0, "Grrr", "rrrr")
    T.switch = T.now + 1.0
    mgr.step(112.5)
    assert T.durations and T.durations[-1][1] >= 40.0 / 12.0
    T.next_tls["amb1"] = []  # passed: hold released
    mgr.step(115.0)
    assert mgr.tls["J"].held_by is None and not mgr.tls["J"].advancing


def test_preemption_never_cuts_pedestrian_clearance():
    T, eng, mgr = _manager()
    eng.active = ["amb1"]
    T.speed["amb1"] = 12.0
    T.next_tls["amb1"] = [("J", 0, 100.0, "r")]
    _phase(mgr, 60.0, "rGrr", "rGrG")  # crosswalk link 3 went from walk to don't walk: clearance
    mgr.step(100.0)
    assert T.durations == []


def test_preemption_reaches_further_signals_only_while_the_unit_is_moving():
    T, eng, mgr = _manager()
    eng.active = ["amb1"]
    T.next_tls["amb1"] = [("J", 0, 250.0, "r")]
    _phase(mgr, 60.0, "rGrr")
    T.speed["amb1"] = 0.0  # stuck in a queue: a signal 250 m away is not held for it
    mgr.step(100.0)
    assert mgr.tls["J"].held_by is None and T.durations == []
    T.speed["amb1"] = 13.0  # 250 m is under 20 s away at this speed
    mgr.step(101.0)
    assert mgr.tls["J"].held_by == "amb1" and T.durations == [("J", 0.0)]


def test_a_second_unit_on_a_served_movement_shares_the_hold():
    T, eng, mgr = _manager()
    eng.active = ["amb1", "police1"]
    T.speed.update({"amb1": 10.0, "police1": 10.0})
    T.next_tls["amb1"] = [("J", 0, 30.0, "G")]
    T.next_tls["police1"] = [("J", 1, 90.0, "G")]
    _phase(mgr, 95.0, "GGrr")
    T.switch = T.now + 2.0
    mgr.step(100.0)
    assert mgr.tls["J"].held_by == "amb1"
    assert max(d for _, d in T.durations) >= 90.0 / 10.0  # extended for the further unit as well
