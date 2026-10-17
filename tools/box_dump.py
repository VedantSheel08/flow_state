import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flowstate.evaluation import conditions as C
from flowstate.evaluation.runner import run_condition
cid, seed, stop, jid = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
class Stop(Exception): pass
def hook(eng, col):
    T = eng.T
    if T.simulation.getTime() < stop: return
    print("time", T.simulation.getTime(), "tls", T.trafficlight.getRedYellowGreenState(jid) if jid in T.trafficlight.getIDList() else None)
    for lane in sorted(T.junction.getIDList() and [l for l in T.lane.getIDList() if l.startswith(f":{jid}_")]):
        for v in T.lane.getLastStepVehicleIDs(lane):
            ldr = T.vehicle.getLeader(v, 10)
            links = T.vehicle.getNextLinks(v)
            print(f"{lane:14} {v:6} {T.vehicle.getTypeID(v).split('@')[0]:6} pos={T.vehicle.getLanePosition(v):5.1f}/{T.lane.getLength(lane):4.1f} v={T.vehicle.getSpeed(v):4.1f} w={T.vehicle.getWaitingTime(v):5.0f} ldr={ldr} stop={T.vehicle.getStopState(v)} "
                  f"to={[ (l[0], l[1], l[2]) for l in links[:1]]} dest={T.vehicle.getRoute(v)[-1]}")
    ppl = [p for p in T.person.getIDList() if T.person.getRoadID(p).startswith(f":{jid}")]
    print("persons on junction", [(p, T.person.getRoadID(p), round(T.person.getWaitingTime(p))) for p in ppl])
    raise Stop
try:
    run_condition(C.get(cid), seed, Path("reports/diag"), on_step=hook, progress=False)
except Stop:
    pass
