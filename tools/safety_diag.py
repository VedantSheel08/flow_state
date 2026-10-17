"""SUMO-diagnostic run of one evaluation condition: attribute every native collision and teleport to a cause.

    python tools/safety_diag.py exp_pm_10 42 out.json
Records, for each collision (libsumo getCollisions) the colliders' lanes, speeds, signal state and lane-change state, and
for each teleport a snapshot taken the step before it (wait time, lane, leader, signal, junction position).
"""
import json, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flowstate.evaluation import conditions as C
from flowstate.evaluation.runner import run_condition

cid, seed, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
events = {"collisions": [], "teleports": []}
snap = {}
seen_coll = set()

def hook(eng, col):
    T = eng.T
    t = round(T.simulation.getTime(), 2)
    for c in T.simulation.getCollisions():
        key = (c.collider, c.victim, c.type, c.lane)
        if key in seen_coll:
            continue
        seen_coll.add(key)
        def info(v):
            if not v or v not in eng.vehicles and v not in T.vehicle.getIDList():
                return None
            try:
                return {"lane": T.vehicle.getLaneID(v), "speed": round(T.vehicle.getSpeed(v), 2), "type": T.vehicle.getTypeID(v),
                        "wait": round(T.vehicle.getWaitingTime(v), 1), "route": T.vehicle.getRoute(v)[T.vehicle.getRouteIndex(v):][:3],
                        "speedMode": T.vehicle.getSpeedMode(v), "laneChangeMode": T.vehicle.getLaneChangeMode(v), "signals": T.vehicle.getSignals(v)}
            except Exception as e:  # noqa
                return {"err": str(e)[:60]}
        tl = None
        lane = c.lane
        events["collisions"].append({"t": t, "collider": c.collider, "victim": c.victim, "ctype": c.colliderType, "vtype": c.victimType,
                                     "cspeed": round(c.colliderSpeed, 2), "vspeed": round(c.victimSpeed, 2), "kind": c.type, "lane": lane, "pos": round(c.pos, 1),
                                     "a": info(c.collider), "b": info(c.victim)})
    for v in T.simulation.getStartingTeleportIDList():
        s = snap.get(v, {})
        events["teleports"].append({"t": t, "veh": v, **s})
    for v in T.vehicle.getIDList():
        w = T.vehicle.getWaitingTime(v)
        if w > 120 or v in snap:
            try:
                lane = T.vehicle.getLaneID(v)
                ldr = T.vehicle.getLeader(v, 50)
                nt = T.vehicle.getNextTLS(v)
                snap[v] = {"wait": round(w, 1), "lane": lane, "leader": ldr[0] if ldr else None, "leaderGap": round(ldr[1], 1) if ldr else None,
                           "injunction": lane.startswith(":"), "nextTls": nt[0][0] if nt else None, "tlsDist": round(nt[0][2], 1) if nt else None,
                           "tlsState": nt[0][3] if nt else None, "type": T.vehicle.getTypeID(v), "route": T.vehicle.getRoute(v)[T.vehicle.getRouteIndex(v):][:3]}
            except Exception:  # noqa
                pass

rep = run_condition(C.get(cid), seed, Path("reports/diag"), on_step=hook, progress=False)
events["summary"] = {"classification": rep["classification"]["achievedTier"], "safety": rep["safety"], "ledger": rep["ledger"]}
out.write_text(json.dumps(events, default=str, indent=1))
print("collisions", len(events["collisions"]), "teleports", len(events["teleports"]))
