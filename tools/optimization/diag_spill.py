"""When does the 'predicted spillback' trigger (p30 >= 0.7 cap on a downstream approach) fire, and is it right 30 s later?"""
import sys, multiprocessing as mp
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def job(args):
    sc, seed = args
    import numpy as np
    from flowstate.rl import config as C
    from flowstate.lab.sim import LabSim
    from flowstate.lab.control import make_controller
    from flowstate.lab.forecast import intensity_for
    sim = LabSim(f"ds{mp.current_process().pid}")
    sim.reset(sc, seed, 900.0, 600.0, intensity_for(seed))
    ctl, _ = make_controller("queue_heuristic", sim)  # forecaster attached in shadow mode
    P, Q, CNT, CAP = [], [], [], None
    while not sim.done:
        X = sim.observe()
        P.append(sim.forecast["p30"].copy()); Q.append(X[:, 0].copy()); CNT.append(X[:, 1].copy()); CAP = X[:, 16].copy()
        for jid, a in zip(C.CONTROLLED, ctl.decide(sim)):
            if a: sim.request(jid)
        sim.advance(C.DECISION_S)
    sim.close()
    P, Q, CNT = np.array(P), np.array(Q), np.array(CNT)
    internal = np.array([i for i, a in enumerate(sim.topo.approaches) if not a.is_entry])
    return P[:-6, internal], Q[6:, internal], CNT[6:, internal], CNT[:-6, internal], CAP[internal]


if __name__ == "__main__":
    mp.set_executable(sys.executable)
    import numpy as np
    jobs = [(sc, s) for sc in ("balanced", "ns_heavy", "ew_heavy", "pulse") for s in (4000, 4001)]
    with mp.get_context("spawn").Pool(6) as p:
        res = p.map(job, jobs)
    P = np.concatenate([r[0] for r in res]); Qf = np.concatenate([r[1] for r in res]); Cf = np.concatenate([r[2] for r in res]); Cn = np.concatenate([r[3] for r in res]); cap = res[0][4]
    trig = P >= 0.7 * cap
    print("internal approach capacity (veh):", cap)
    print("trigger rate %.3f of approach-decisions" % trig.mean())
    print("when triggered: predicted halting %.1f, actual halting 30 s later %.1f, actual vehicles 30 s later %.1f (cap %.0f)" % (P[trig].mean(), Qf[trig].mean(), Cf[trig].mean(), cap.mean()))
    print("precision: actual halting >= 0.7 cap: %.2f; actual occupancy >= 0.92 (base rule's 'blocked'): %.2f" % ((Qf[trig] >= 0.7 * cap[np.nonzero(trig)[1]]).mean(), (Cf[trig] >= 0.92 * cap[np.nonzero(trig)[1]]).mean()))
    print("free storage when triggered (cap - vehicles 30 s later): median %.1f veh" % np.median((cap - Cf)[trig]))
    print("bias over all internal approach-decisions: pred - actual = %.2f, |err| at q>=20: %.2f" % ((P - Qf).mean(), np.abs(P - Qf)[Qf >= 20].mean()))
