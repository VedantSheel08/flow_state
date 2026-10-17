"""SUMO runs: spawning, arrivals, determinism, emergency routing, cyclists, incidents, transit."""

from helpers import make_engine, run_until


def test_vehicles_spawn_move_and_complete_trips():
    eng = make_engine("rush_hour", 7, {"duration": 5}, "spawn")
    eng.start()
    try:
        run_until(eng, 180)
        assert len(eng.vehicles) > 20
        assert eng.metrics.completed > 10
        assert eng.kpi["meanSpeed"] and eng.kpi["meanSpeed"] > 5
        assert eng.teleports == 0
        assert len(eng.persons) > 5
        frame = eng.frame()
        assert frame["v"] and all(len(v) == 7 for v in frame["v"])
        assert set(frame["tls"]) == set(eng.net.tls_ids)
    finally:
        eng.close()


def test_same_seed_reproduces_the_simulation():
    def snapshot(seed):
        eng = make_engine("accident", seed, {"duration": 5}, "det")
        eng.start()
        try:
            run_until(eng, 60)
            return sorted((v[0], v[2], v[3]) for v in eng.frame()["v"]), eng.metrics.completed
        finally:
            eng.close()

    a, b, c = snapshot(11), snapshot(11), snapshot(12)
    assert a == b
    assert a != c


def test_ambulance_drives_a_real_route_and_reaches_the_scene():
    eng = make_engine("ambulance", 5, {"calls": 1, "duration": 10}, "amb")
    eng.start()
    try:
        call = eng.scenario.components[0].calls[0]
        roads = set()

        def watch(e):
            if "amb1" in e.vehicles:
                roads.add(e.T.vehicle.getRoadID("amb1"))

        run_until(eng, call["t"] + 240, watch)
        assert call["arrived"] is not None, "ambulance never reached the scene"
        assert call["edge"] in roads
        assert all(r in eng.net.edges for r in roads if not r.startswith(":"))
        assert eng.tracked["amb1"].data["responseTime"] >= call["ff"] * 0.8  # cannot beat free flow
    finally:
        eng.close()


def test_cyclists_only_use_lanes_that_permit_bicycles():
    eng = make_engine("bike_lanes", 3, {"duration": 5, "bike_share": 25}, "bike")
    eng.start()
    try:
        seen = 0

        def check(e):
            nonlocal seen
            if int(e.t * 4) % 20:
                return
            for vid in list(e.vehicles):
                if e.vtype.get(vid, "").startswith("bike"):
                    lane = e.T.vehicle.getLaneID(vid)
                    if lane.startswith(":"):
                        continue
                    seen += 1
                    assert "bicycle" in e.T.lane.getAllowed(lane), lane

        run_until(eng, 150, check)
        assert seen > 20
    finally:
        eng.close()


def test_accident_physically_blocks_lanes_and_clears():
    eng = make_engine("accident", 21, {"severity": "moderate", "duration": 15}, "acc")
    eng.start()
    try:
        acc = eng.scenario.components[0]
        run_until(eng, acc.t_start + 5)
        inc = acc.inc
        assert inc is not None and inc.blockers
        for wid in inc.blockers:
            assert eng.T.vehicle.getRoadID(wid) == acc.edge
        run_until(eng, acc.t_clear + 2)
        assert inc.cleared
        assert all(w not in eng.vehicles for w in inc.blockers)
    finally:
        eng.close()


def test_buses_serve_stops_and_tsp_extends_greens():
    eng = make_engine("transit", 4, {"duration": 10, "headway": 3, "tsp": True}, "bus")
    eng.start()
    try:
        stopped_at = set()

        def watch(e):
            for vid in e.buses_active():
                if e.T.vehicle.isAtBusStop(vid):
                    stopped_at.add(e.T.vehicle.getRoadID(vid))

        run_until(eng, 420, watch)
        assert stopped_at, "buses never served a stop"
        assert eng.signals.tsp_count >= 1
    finally:
        eng.close()


def test_actuated_mode_runs_sumo_actuated_programs():
    eng = make_engine("rush_hour", 2, {"signals": "actuated", "duration": 5}, "act")
    eng.start()
    try:
        run_until(eng, 30)
        assert all(eng.T.trafficlight.getProgram(t) == "actuated" for t in eng.net.tls_ids)
    finally:
        eng.close()
