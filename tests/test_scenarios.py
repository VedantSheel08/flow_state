"""Scenario generation: validation, reproducibility and variation (no SUMO run)."""

import pytest

from flowstate.api.service import NETWORKS_DIR
from flowstate.network.info import load_net_info
from flowstate.scenarios.catalog import CATALOG, create, list_specs


def instance(sid, seed, params=None):
    sc = create(sid, params or {})
    net = load_net_info(str(NETWORKS_DIR / f"{sc.network_id()}.net.xml"), sc.network_id())
    sc.instantiate(seed, net)
    return sc, net


def test_catalog_has_required_scenarios():
    ids = {s["id"] for s in list_specs()}
    assert {"rush_hour", "accident", "ambulance", "pursuit", "bike_lanes", "construction", "stadium", "school", "transit", "weather", "disruption"} <= ids


@pytest.mark.parametrize("sid", list(CATALOG))
def test_every_scenario_instantiates_for_many_seeds(sid):
    for seed in range(1, 16):
        sc, net = instance(sid, seed)
        for e in sc.reserved:
            assert e in net.edges and net.edges[e].kind == "street"
        ts = [m.t for m in sc.markers]
        assert ts == sorted(ts)


@pytest.mark.parametrize("sid", list(CATALOG))
def test_same_seed_reproduces_inputs(sid):
    a, _ = instance(sid, 1234)
    b, _ = instance(sid, 1234)
    assert a.describe()["briefing"] == b.describe()["briefing"]
    assert a.describe()["markers"] == b.describe()["markers"]


MAP_KINDS = {"accident": {"point"}, "ambulance": {"point"}, "pursuit": {"point"}, "construction": {"zone"}, "stadium": {"zone"},
             "school": {"zone", "point"}, "transit": {"route"}, "bike_lanes": {"route"}, "weather": {"weather"}}


@pytest.mark.parametrize("sid", list(CATALOG))
def test_overlays_place_every_scenario_on_the_map(sid):
    for seed in (1, 7, 42):
        sc, net = instance(sid, seed)
        ovs = sc.describe()["overlays"]
        assert MAP_KINDS.get(sid, set()) <= {o["kind"] for o in ovs}
        if sid not in ("rush_hour",):
            assert ovs
        for o in ovs:
            assert all(e in net.edges for e in o["edges"])
            if o["kind"] in ("point", "zone", "route"):
                assert o["edges"]
        assert len({o["id"] for o in ovs}) == len(ovs)


@pytest.mark.parametrize("sid", ["accident", "ambulance", "construction", "rush_hour", "pursuit", "disruption", "stadium"])
def test_different_seeds_give_different_situations(sid):
    seen = {str(instance(sid, s)[0].describe()["briefing"]) for s in range(1, 9)}
    assert len(seen) >= 4


def test_accident_locations_are_valid_and_varied():
    edges, sev = set(), set()
    for s in range(1, 40):
        sc, net = instance("accident", s)
        acc = sc.components[0]
        e = net.edges[acc.edge]
        assert e.kind == "street" and e.part != "bay" and acc.lanes
        assert all(f"{acc.edge}_{i}" in net.car_lanes(acc.edge) for i in acc.lanes)
        assert 0 < acc.pos < e.length
        edges.add(acc.edge)
        sev.add(acc.severity)
    assert len(edges) >= 10 and sev == {"minor", "moderate", "severe"}


def test_invalid_params_are_rejected():
    with pytest.raises(ValueError):
        create("accident", {"severity": "apocalyptic"})
    with pytest.raises(ValueError):
        create("ambulance", {"calls": 99})
    with pytest.raises(ValueError):
        create("rush_hour", {"unknown": 1})
    sc = create("accident", {"location": "not_an_edge"})
    with pytest.raises(ValueError):
        sc.instantiate(1, load_net_info(str(NETWORKS_DIR / "district.net.xml"), "district"))


def test_weather_changes_driver_parameters():
    from flowstate.sim.vtypes import build_vtypes

    snow, _ = instance("weather", 3, {"condition": "snow"})
    clear, _ = instance("weather", 3, {"condition": "clear"})
    assert snow.conditions().speed < 0.85 and snow.conditions().tau > 1.3
    assert clear.conditions().speed == 1.0
    assert build_vtypes(3, snow.conditions())[0] != build_vtypes(3, clear.conditions())[0]


def test_driver_profiles_vary_but_are_seeded():
    from flowstate.sim.vtypes import build_vtypes

    xml1, prof = build_vtypes(5)
    xml2, _ = build_vtypes(5)
    xml3, _ = build_vtypes(6)
    assert xml1 == xml2 and xml1 != xml3
    assert len(prof["sedan"]) == 8 and len(set(prof["sedan"])) == 8
    assert 'carFollowModel="IDM"' in xml1
