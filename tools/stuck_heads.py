import sys, json, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flowstate.evaluation import conditions as C
from flowstate.evaluation.runner import run_condition
cid, seed, stop = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
class Stop(Exception): pass
def hook(eng, col):
    T = eng.T
    if T.simulation.getTime() < stop: return
    stuck = [v for v in T.vehicle.getIDList() if T.vehicle.getWaitingTime(v) > 120]
    print("time", T.simulation.getTime(), "stuck", len(stuck), "of", len(T.vehicle.getIDList()))
    kinds = collections.Counter(); rows = []
    for v in stuck:
        lane = T.vehicle.getLaneID(v); ldr = T.vehicle.getLeader(v, 5)
        if ldr and ldr[0] in stuck and ldr[1] < 5: continue   # not a head: queued behind a stuck leader
        links = T.vehicle.getNextLinks(v)
        nl = links[0] if links else None
        nt = T.vehicle.getNextTLS(v)
        injunction = lane.startswith(":")
        info = {"v": v, "type": T.vehicle.getTypeID(v).split("@")[0], "lane": lane, "pos": round(T.vehicle.getLanePosition(v), 1), "wait": round(T.vehicle.getWaitingTime(v)),
                "injunction": injunction, "tls": (nt[0][0], nt[0][3], round(nt[0][2], 1)) if nt else None}
        if nl and not injunction:
            via = nl[4]; info["link"] = {"to": nl[0], "open": nl[1], "prio": nl[2], "foe": nl[7] if len(nl) > 7 else None, "state": nl[6] if len(nl)>6 else None}
            occ = [(x, round(T.vehicle.getSpeed(x), 1)) for x in T.lane.getLastStepVehicleIDs(via)] if via else []
            info["viaOcc"] = occ
            tolane = nl[0]
            info["toLaneVeh"] = T.lane.getLastStepVehicleNumber(tolane) if tolane else None
        rows.append(info)
    for r in rows[:25]: print(json.dumps(r))
    raise Stop
try:
    run_condition(C.get(cid), seed, Path("reports/diag"), on_step=hook, progress=False)
except Stop:
    pass
