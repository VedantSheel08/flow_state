"""Unit tests for the Results-page replay tooling that do not need SUMO."""

from flowstate.replay.priority import MAX_EXTENSION_S, MIN_GREEN_S, EmergencyPriority
from flowstate.replay.record import asset, paired


def _run(seed, ev, queue, waiting, frames=()):
    return {"seed": seed, "tlsIds": ["j1"], "frames": list(frames), "ambulances": [],
            "metrics": {"ev_travel_mean_s": ev, "ev_stopped_red_s": 0.0, "ev_stopped_queue_s": 0.0, "queue_total_mean_veh": queue, "throughput_vph": 1000.0,
                        "teleports": 0, "collisions": 0, "car": {"waiting": waiting, "speed": 20.0, "trips": 50}}}


def test_paired_signs_and_counts():
    pairs = [{"seed": s, "left": _run(s, 100 + s, 20.0, 25.0), "right": _run(s, 80 + s, 18.0, 24.0)} for s in range(6)]
    pairs.append({"seed": 9, "left": _run(9, 100, 20.0, 25.0), "right": _run(9, 130, 25.0, 30.0)})  # one run where the right side is worse
    agg = paired(pairs, ["waiting", "speed", "trips"])
    ev = agg["ev_travel_mean_s"]
    assert ev["pairs"] == 7 and ev["better"] == 6 and ev["worse"] == 1
    assert ev["mean_diff"] < 0 and ev["ci95"][0] <= ev["mean_diff"] <= ev["ci95"][1]
    assert agg["car_waiting"]["better"] == 6 and agg["car_waiting"]["worse"] == 1
    assert agg["car_speed"]["better"] == 0 and agg["car_speed"]["worse"] == 0  # unchanged speed is neither


def test_asset_lists_every_ambulance_and_picks_by_rule():
    def unit(call, iid, travel, red, queue, finished=True):
        return {"id": f"amb{call}", "call": call, "intId": iid, "travelS": travel, "stoppedRedS": red, "stoppedQueueS": queue, "finished": finished,
                "departS": 10.0, "first": 10, "last": 10 + int(travel)}

    left = _run(1, 100.0, 20.0, 25.0, [{"t": 0, "v": [], "s": [""]}])
    right = _run(1, 80.0, 18.0, 24.0, [{"t": 0, "v": [], "s": [""]}])
    left["ambulances"] = [unit(1, 5, 120.0, 10.0, 30.0), unit(2, 6, 150.0, 0.0, 5.0), unit(3, 7, 90.0, 0.0, 0.0, finished=False)]
    right["ambulances"] = [unit(1, 50, 70.0, 0.0, 2.0), unit(2, 60, 60.0, 0.0, 0.0), unit(3, 70, 80.0, 0.0, 0.0)]
    doc = asset({"seed": 1, "left": left, "right": right}, {}, [1])
    assert [u["call"] for u in doc["ambulances"]] == [1, 2, 3]
    assert doc["ambulances"][2]["savedS"] is None  # unfinished on one side is reported, not dropped
    assert doc["example"] == 5  # call 1: queued >= 10 s on the left and held <= 3 s on the right; call 2 saved more but was not queue-blocked
    assert doc["ambulances"][0]["right"]["id"] == 50 and doc["codes"] == "main" and doc["network"] == "district"


def test_priority_rule_constants_and_summary():
    p = EmergencyPriority()
    assert MIN_GREEN_S >= 7.0 and MAX_EXTENSION_S <= 30.0
    s = p.summary()
    assert s["extensions"] == 0 and s["earlyEnds"] == 0 and "no skipped amber" in s["rule"]
