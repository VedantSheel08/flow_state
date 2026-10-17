import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flowstate.evaluation import conditions as C
from flowstate.evaluation.runner import run_condition
cid, seed, stop, jid = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
state = {"done": False, "mark": None}
def box(T): return [v for l in T.lane.getIDList() if l.startswith(f":{jid}_") and not l.startswith(f":{jid}_c") and not l.startswith(f":{jid}_w") for v in T.lane.getLastStepVehicleIDs(l)]
def hook(eng, col):
    T = eng.T; t = T.simulation.getTime()
    if not state["done"] and t >= stop:
        b = box(T); print("t", t, "vehicles in box", len(b), "moving", sum(1 for v in b if T.vehicle.getSpeed(v) > 0.5))
        ppl = [p for p in T.person.getIDList() if T.person.getRoadID(p).startswith(f":{jid}")]
        print("removing persons", ppl)
        for p in ppl: T.person.remove(p)
        state["done"] = True; state["mark"] = t
    elif state["done"] and t in (state["mark"] + 5, state["mark"] + 10, state["mark"] + 20, state["mark"] + 40):
        b = box(T); print("t", t, "vehicles in box", len(b), "moving", sum(1 for v in b if T.vehicle.getSpeed(v) > 0.5))
        if t >= state["mark"] + 40: raise SystemExit
run_condition(C.get(cid), seed, Path("reports/diag"), on_step=hook, progress=False)
