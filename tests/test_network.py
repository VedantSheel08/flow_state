"""Network geometry, connectivity and signal plans."""

import sumolib

from flowstate.api.service import NETWORKS_DIR
from flowstate.network.geometry import network_geometry
from flowstate.network.info import load_net_info


def test_networks_load_and_are_connected():
    for nid in ("district", "district_bike"):
        net = sumolib.net.readNet(str(NETWORKS_DIR / f"{nid}.net.xml"))
        info = load_net_info(str(NETWORKS_DIR / f"{nid}.net.xml"), nid)
        assert len(info.tls_ids) >= 8
        assert set(info.entries) == {"W", "E", "N", "S"} and set(info.exits) == {"W", "E", "N", "S"}
        for o in info.entry_edges:  # every entry reaches every exit by car
            for d in info.exit_edges:
                if info.edges[o].street == info.edges[d].street and info.edges[o].dir != info.edges[d].dir:
                    continue  # would be a U-turn on the same street
                path, _ = net.getShortestPath(net.getEdge(o), net.getEdge(d), vClass="passenger")
                assert path, f"no route {o} -> {d} in {nid}"


def test_turn_bays_are_left_only_and_through_lanes_cannot_turn_left():
    net = sumolib.net.readNet(str(NETWORKS_DIR / "district.net.xml"))
    bays = [e for e in net.getEdges() if e.getID().endswith("b") and e.getType() == "arterial"]
    assert bays, "arterials should have left-turn bays"
    for e in bays:
        lanes = [l for l in e.getLanes() if l.allows("passenger")]
        assert {c.getDirection() for c in lanes[-1].getOutgoing()} <= {"l", "L"}
        for l in lanes[:-1]:
            assert not {c.getDirection() for c in l.getOutgoing()} & {"l", "L"}


def test_geometry_export_has_real_lane_shapes_crossings_and_signals():
    g = network_geometry(str(NETWORKS_DIR / "district.net.xml"), "district")
    roles = {l["role"] for e in g["edges"] for l in e["lanes"]}
    assert {"car", "bike", "sidewalk", "median"} <= roles
    assert len(g["crossings"]) > 40 and all(len(c["shape"]) >= 4 for c in g["crossings"])
    assert all(len(j["shape"]) >= 6 for j in g["junctions"] if j["type"] == "traffic_light")
    tls_lanes = [l for e in g["edges"] for l in e["lanes"] if l["control"] == "tls"]
    assert tls_lanes and all(l["tls"] and l["links"] for l in tls_lanes)
    assert any(l["control"] == "stop" for e in g["edges"] for l in e["lanes"]), "local streets should be stop-controlled"


def test_bike_network_reallocates_king_street():
    a = load_net_info(str(NETWORKS_DIR / "district.net.xml"), "district")
    b = load_net_info(str(NETWORKS_DIR / "district_bike.net.xml"), "district_bike")
    king_a = [e for e in a.edges.values() if e.street == "king" and e.kind == "street"]
    king_b = [e for e in b.edges.values() if e.street == "king" and e.kind == "street"]
    assert not any(a.lanes[l].allows_bike and not a.lanes[l].allows_car for e in king_a for l in e.lanes)
    assert all(any(b.lanes[l].allows_bike and not b.lanes[l].allows_car for l in e.lanes) for e in king_b)
