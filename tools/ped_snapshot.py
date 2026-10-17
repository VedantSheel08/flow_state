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
    ids = T.person.getIDList()
    byedge = collections.Counter(); wait = collections.defaultdict(list); stage = collections.Counter()
    for p in ids:
        e = T.person.getRoadID(p); byedge[e] += 1; wait[e].append(round(T.person.getWaitingTime(p)))
    print("time", T.simulation.getTime(), "persons", len(ids), "vehicles", len(T.vehicle.getIDList()))
    for e, n in byedge.most_common(12): print("  ", e, n, "wait", sorted(wait[e])[-5:])
    raise Stop
try:
    run_condition(C.get(cid), seed, Path("reports/diag"), on_step=hook, progress=False)
except Stop:
    pass
