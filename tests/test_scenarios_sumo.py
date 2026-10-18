"""Scenarios in real SUMO: correctness, routing, obstruction and reliability across seeds.

Every test drives a full SUMO run and inspects what SUMO did. ``FLOWSTATE_SWEEP_SEEDS`` (comma-separated, default
``1,2``) widens the all-scenarios sweep, e.g. ``FLOWSTATE_SWEEP_SEEDS=1,2,3,4,5,6,7,8``.
"""

import os

import pytest
from scenario_audit import Audit, custom_scenario, is_owned, run_audit

from flowstate.scenarios.catalog import CATALOG
from flowstate.scenarios.components import Construction, EmergencyDispatch, poi_edges
from flowstate.sim.vtypes import EMERGENCY_TYPES, TYPE_BY_ID

SEEDS = [int(s) for s in os.environ.get("FLOWSTATE_SWEEP_SEEDS", "1,2").split(",")]
# Gridlock teleports of ordinary vehicles that no scenario component owns. SUMO's junction model can wedge a ring of
# cars in a conflict (it shows up in the demand-only scenarios too, mostly in runs longer than the 5 minutes swept
# here). The sweep has measured 0 over 88 runs; the allowance keeps a one-off from failing the build.
MAX_BACKGROUND_TELEPORTS = 2


def problems(a: Audit) -> list[str]:
    out = []
    if a.bike_violations:
        out.append(f"bicycles on lanes that forbid them: { {k: sorted(v) for k, v in a.bike_violations.items()} }")
    if a.owned_teleports:
        out.append(f"scenario vehicles teleported: {a.owned_teleports}")
    if a.wreck_collisions:
        out.append(f"collision with a wreck: {a.wreck_collisions}")
    if a.background_teleports > MAX_BACKGROUND_TELEPORTS:
        out.append(f"{a.background_teleports} background teleports")
    broken = [e for e in a.events if "no route" in e[2] or "could not be placed" in e[2]]
    if broken:
        out.append(f"incomplete events: {broken}")
    return out


# ---------------------------------------------------------------------------
# All 11 scenarios x seeds
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", list(CATALOG))
@pytest.mark.parametrize("seed", SEEDS)
def test_scenario_runs_cleanly_in_sumo(sid, seed):
    a = run_audit(sid, seed, {"bike_share": 20}, duration=5)  # many cyclists: any lane-permission slip shows up
    assert not problems(a), f"{sid} seed {seed}: {problems(a)}"
    assert len(a.emergency_collisions) <= 1, a.emergency_collisions


# ---------------------------------------------------------------------------
# Accidents obstruct real lanes and affect traffic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("severity", ["minor", "moderate", "severe"])
@pytest.mark.parametrize("seed", SEEDS)
def test_accident_blocks_valid_lanes_and_reopens(severity, seed):
    state = {"through": set(), "entered": set(), "seen_at_close": None, "wreck_lanes": None, "reopened": None}

    def watch(eng, audit):
        acc = eng.scenario.components[0]
        inc = acc.inc
        if inc is None or eng.t < acc._created_at + 30:
            return
        T = eng.T
        if not inc.cleared:
            wrecks = [w for w in inc.blockers]
            assert all(w in eng.vehicles for w in wrecks), "a wreck vanished while the scene was active"
            state["wreck_lanes"] = {T.vehicle.getLaneIndex(w) for w in wrecks}
            assert {T.vehicle.getRoadID(w) for w in wrecks} == {acc.edge}
            tail = {li: min(T.vehicle.getLanePosition(w) - T.vehicle.getLength(w) for w in wrecks if T.vehicle.getLaneIndex(w) == li) for li in state["wreck_lanes"]}
            was_behind, now_behind = state.get("behind", {}), {}
            state["behind"] = now_behind
            for li, rear in tail.items():
                for v in T.lane.getLastStepVehicleIDs(f"{acc.edge}_{li}"):
                    if v in wrecks or v.startswith(("resp", "amb", "police")):
                        continue
                    # ahead of the wreck and still inside the wreck's own corridor (a car that has already slid over into
                    # the free lane is indexed in the blocked lane until its centre crosses the boundary). Only a car that was
                    # behind the wreck in this lane at the previous step counts: on a short one-way block a car may pass in
                    # the free lane and then move back over in front of the wreck to make its turn.
                    if T.vehicle.getLanePosition(v) < rear:
                        now_behind.setdefault(li, set()).add(v)
                    elif v in was_behind.get(li, set()) and T.vehicle.getLanePosition(v) > rear + 0.5 and abs(T.vehicle.getLateralLanePosition(v)) < 0.6:
                        state["through"].add(v)
            if acc.closed:
                for li in acc.car_lanes:
                    for v in T.lane.getLastStepVehicleIDs(f"{acc.edge}_{li}"):
                        if not v.startswith(("inc", "resp", "amb", "police")):
                            state["entered"].add(v)
                if state["seen_at_close"] is None:
                    state["seen_at_close"] = set(state["entered"])
        elif state["reopened"] is None:
            state["reopened"] = all("passenger" not in T.lane.getDisallowed(f"{acc.edge}_{li}") for li in acc.car_lanes)

    a = run_audit("accident", seed, {"severity": severity}, duration=10, on_step=watch)
    res = a.results["accident"]
    assert res["obstructed"] is True, "the wrecks must stand in the blocked lanes"
    assert res["cleared"] is True and state["reopened"] is True, "lanes must reopen with their permissions restored"
    assert state["wreck_lanes"] == set(res["lanes"]), "wrecks must occupy exactly the lanes the incident reports"
    assert not state["through"], f"vehicles drove past the wreck in a blocked lane: {sorted(state['through'])[:5]}"
    if res["closed"]:
        # nobody who was not already on the block when it closed may enter it; all lanes are really closed
        assert state["entered"] == state["seen_at_close"], f"vehicles entered a closed block: {sorted(state['entered'] - state['seen_at_close'])[:5]}"
        assert res["peakQueue"] >= 1, "a closed street must back traffic up"
    responders = [t for t in a.tracked if t["kind"] == "responder"]
    assert len(responders) == (1 if severity == "minor" else 2)
    assert all(t.get("responseTime") is not None for t in responders), f"responders missing from the scene: {responders}"
    inc = a.incidents[0]
    on_scene = [t["responseTime"] for t in responders]
    # the scene is only cleared once every unit has been there (clearing waits for late units, up to a hard stop)
    assert res["firstResponse"] is not None and max(on_scene) <= inc["tEnd"] - inc["tStart"], (on_scene, inc)
    assert not a.owned_teleports and not a.wreck_collisions
    assert a.background_teleports <= MAX_BACKGROUND_TELEPORTS


def test_closed_block_traffic_is_planned_around_it():
    """A fully closed accident block takes no new trips: nobody routes through it or is sent to it."""
    seen_routes: list[tuple[str, tuple[str, ...]]] = []

    def watch(eng, audit):
        acc = eng.scenario.components[0]
        if acc.inc is None or acc.inc.cleared or eng.t < acc._created_at + 20:
            return
        for vid in list(eng.vehicles)[:60]:
            if vid.startswith(("inc", "resp", "amb", "police")) or vid in eng.tracked:
                continue
            if vid in audit.extra.setdefault("checked", set()):
                continue
            audit.extra["checked"].add(vid)
            T = eng.T
            idx = T.vehicle.getRouteIndex(vid)
            seen_routes.append((vid, tuple(T.vehicle.getRoute(vid)[idx + 1:])))

    a = run_audit("accident", 3, {"severity": "severe"}, duration=10, on_step=watch)
    edge = a.results["accident"]["edge"]
    assert a.results["accident"]["closed"]
    assert seen_routes, "no traffic was sampled during the closure"
    through = [v for v, route in seen_routes if edge in route]
    assert not through, f"{len(through)} vehicles were still routed over the closed block: {through[:5]}"


# ---------------------------------------------------------------------------
# Ambulances and responders around closures
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", SEEDS)
def test_every_ambulance_call_is_answered(seed):
    a = run_audit("ambulance", seed, {"calls": 2}, duration=10)
    rows = a.results["ems"]
    assert len(rows) == 2 and not any(r["failed"] for r in rows), rows
    assert all(r["responseTime"] is not None for r in rows), f"unanswered call: {rows}"
    for r in rows:
        assert r["responseTime"] >= 0.5 * r["freeFlow"], "response faster than free flow is not credible"
        assert r["responseTime"] < 300, "response should not be dominated by being stuck"
    for r in rows:
        assert r["edge"] in a.roads[f"amb{r['call']}"], "the ambulance never drove onto its scene"
    assert not problems(a), problems(a)


def test_ambulance_drives_around_a_closed_street():
    """Put a full closure on the ambulance's own shortest route: it must find another way and still arrive."""
    state = {}

    def first(eng, audit):
        if "route" not in state and eng.t >= -80:
            ems = eng.scenario.components[0]
            c = ems.calls[0]
            r = eng.route_edges(ems.base[0], c["edge"], "ambulance")
            state["route"], state["call"], state["base"] = r[0], c["edge"], ems.base[0]
            eng.finished = True

    seed = 2
    probe = custom_scenario(lambda: [EmergencyDispatch(1)], {"duration": 5})
    run_audit(probe, seed, on_step=first, label="amb_probe")
    route = state["route"]
    mid = [e for e in route[1:-1] if e != state["call"]]
    from flowstate.api.service import NETWORKS_DIR
    from flowstate.network.info import load_net_info

    net = load_net_info(str(NETWORKS_DIR / "district.net.xml"), "district")
    stations = set(poi_edges(net, "hospital")) | set(poi_edges(net, "police"))  # their frontage is never closed
    block = next(e for e in mid if net.edges[e].part != "bay" and net.edges[e].kind == "street"
                 and not set(net.street_segment(e)) & stations)
    sc = custom_scenario(lambda: [EmergencyDispatch(1), Construction(location=block, closure="full")], {"duration": 10})
    a = run_audit(sc, seed, label="amb_closure")
    ems = a.results["ems"][0]
    assert not ems["failed"] and ems["responseTime"] is not None, ems
    closed_block = set(net.street_segment(block))
    assert not a.roads["amb1"] & closed_block, f"ambulance used the closed block {closed_block & a.roads['amb1']}"
    civilians = {v for v, roads in a.roads.items() if v.startswith("v") and roads & closed_block}
    assert len(civilians) <= 3, f"{len(civilians)} cars drove on the closed block"


def watch_drivers_ahead(near: dict, ahead: float = 60.0):
    """Record civilians on an active unit's street within ``ahead`` m in front of it: peak lateral offset and speeds."""
    def watch(eng, audit):
        T = eng.T
        for ev in eng.emergency_active():
            road = T.vehicle.getRoadID(ev)
            if road.startswith(":"):
                continue
            pos = T.vehicle.getLanePosition(ev)
            for v in T.edge.getLastStepVehicleIDs(road):
                if v == ev or is_owned(eng, v) or not 0 < T.vehicle.getLanePosition(v) - pos <= ahead:
                    continue
                r = near.setdefault(v, {"lat": 0.0, "v0": T.vehicle.getSpeed(v), "vmin": T.vehicle.getSpeed(v)})
                r["lat"] = max(r["lat"], abs(T.vehicle.getLateralLanePosition(v)))
                r["vmin"] = min(r["vmin"], T.vehicle.getSpeed(v))
    return watch


def test_drivers_make_way_for_an_ambulance(monkeypatch):
    """With its lights on an ambulance's bluelight device makes drivers ahead move aside; without it they mostly don't.

    Lights off resets the device's reaction distance to zero.
    """
    stats = {}
    for device in (True, False):
        with monkeypatch.context() as m:
            if not device:
                for k in EMERGENCY_TYPES:
                    m.setattr(TYPE_BY_ID[k], "params", {})
            near, quiet = {}, {}
            for seed in SEEDS:
                watch = watch_drivers_ahead(near)

                def on_step(eng, audit, watch=watch):
                    watch(eng, audit)
                    for q in eng._quiet & set(eng.vehicles):
                        if device and q not in quiet:
                            quiet[q] = eng.T.vehicle.getParameter(q, "device.bluelight.reactiondist")

                a = run_audit("ambulance", seed, {"calls": 2}, duration=10, on_step=on_step, label=f"amb_yield_{seed}_{device}")
                assert not a.owned_teleports and len(a.emergency_collisions) <= 1, (seed, a.owned_teleports, a.emergency_collisions)
        moving = [r for r in near.values() if r["v0"] > 3]
        stats[device] = {"near": len(near), "shifted": sum(r["lat"] > 0.3 for r in near.values()) / max(len(near), 1),
                         "slowdown": sum(1 - r["vmin"] / r["v0"] for r in moving) / max(len(moving), 1), "quiet": quiet}
    on, off = stats[True], stats[False]
    print(f"drivers ahead of an ambulance, device on {on} / off {off}")
    assert on["near"] >= 5, "too few drivers ahead of an ambulance to judge"
    assert on["shifted"] >= 0.75, f"only {on['shifted']:.0%} of drivers ahead moved aside"
    assert on["shifted"] >= off["shifted"] + 0.3, (on, off)
    assert on["slowdown"] >= off["slowdown"] - 0.05, (on, off)  # slowing is modest; it must at least not get worse
    assert on["quiet"] and all(float(d) == 0 for d in on["quiet"].values()), on["quiet"]


def test_responders_and_construction_never_meet_on_the_same_block():
    """Disruption mixes closures with ambulance calls; across seeds every call must still be answered."""
    calls = 0
    for seed in SEEDS:
        a = run_audit("disruption", seed, {"intensity": 4}, duration=10)
        for row in a.results.get("ems", []):
            calls += 1
            assert not row["failed"] and row["responseTime"] is not None, (seed, row)
        assert not a.owned_teleports, (seed, a.owned_teleports)
    assert calls or len(SEEDS) < 3, "no seed drew an ambulance call"


# ---------------------------------------------------------------------------
# Pursuit
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", SEEDS)
def test_pursuit_units_chase_and_the_pursuit_ends(seed):
    a = run_audit("pursuit", seed, {"units": 2}, duration=10)
    p = a.results["pursuit"]
    assert p["units"] == p["unitsRequested"] == 2, "every requested unit must join"
    assert p["outcome"] in ("stopped", "arrested", "called off"), f"pursuit never resolved: {p}"
    assert p["closestApproach"] is not None and p["closestApproach"] <= 60
    assert p["routingFailures"] <= 3, p
    suspect = next(t for t in a.tracked if t["id"] == "suspect")
    assert suspect["status"] in ("stopped", "in custody", "en route")
    assert len(a.roads["suspect"]) >= 8, "the suspect must actually drive through the district"
    # units occasionally clip a civilian at a junction exit; one such contact is tolerated, as in the sweep
    assert not a.owned_teleports and len(a.emergency_collisions) <= 1, (a.owned_teleports, a.emergency_collisions)


# ---------------------------------------------------------------------------
# Bicycles
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("network", ["district", "district_bike"])
@pytest.mark.parametrize("seed", SEEDS)
def test_bicycles_stay_on_cycling_infrastructure(network, seed):
    a = run_audit("bike_lanes", seed, {"network": network, "bike_share": 25}, duration=5)
    assert not a.bike_violations, {k: sorted(v) for k, v in a.bike_violations.items()}
    corridor = a.results["corridor"]
    assert corridor["bikeSamples"] > 100, "cyclists must actually ride King Street"
    assert corridor["bikeLaneViolations"] == 0
    if network == "district_bike":
        assert corridor["bikeProtectedShare"] >= 0.95, corridor


# ---------------------------------------------------------------------------
# Completeness and reproducibility
# ---------------------------------------------------------------------------
def test_transit_trips_complete_in_a_default_run():
    a = run_audit("transit", 2, duration=15)
    t = a.results["transit"]
    assert t["trips"] >= 6 and t["completed"] >= 0.85 * t["trips"], t
    assert not problems(a), problems(a)


def test_school_pickups_are_all_placed():
    a = run_audit("school", 1, duration=10)
    s = a.results["school"]
    assert s["failedPickups"] == 0 and s["pickups"] >= 40, s


def test_same_seed_gives_the_same_sumo_outcome():
    first = run_audit("accident", 5, {"severity": "moderate"}, duration=5, label="repeat_a")
    second = run_audit("accident", 5, {"severity": "moderate"}, duration=5, label="repeat_b")
    assert first.events == second.events
    assert first.results == second.results
    assert first.collisions == second.collisions and first.teleports == second.teleports
