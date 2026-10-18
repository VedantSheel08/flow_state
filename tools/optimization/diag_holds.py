"""Diagnose the ML-assisted heuristic's extra holds: what was the green serving when the ML held it, and did it end by max green?

Runs ml_forecast on development seeds only (4000-4003, never used before). For every decision where the ML held a green the plain
heuristic would have ended, records the current stage demand, the waiting stage demand, elapsed green, and whether the predicted
downstream spillback then materialised (actual downstream occupancy >= 0.92 within 30 s).
"""
import json, sys, multiprocessing as mp
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def job(args):
    sc, seed = args
    from flowstate.rl import config as C
    from flowstate.lab.sim import LabSim
    from flowstate.lab.control import make_controller
    from flowstate.lab.forecast import intensity_for
    sim = LabSim(f"dg{mp.current_process().pid}")
    sim.reset(sc, seed, 900.0, 600.0, intensity_for(seed))
    ctl, _ = make_controller("ml_forecast", sim)
    ctl.reset(sim)
    holds, pending = [], []
    while not sim.done:
        sim.observe()
        ctl._ml = False
        base = ctl.__class__.__mro__[1].decide(ctl, sim)
        info = {}
        for k, jid in enumerate(C.CONTROLLED):
            s = sim.signals[jid]
            if base[k] == 1:
                ctl._ml = False
                cur, _, _ = ctl._demand(sim, jid, s.stage); nxt, _, _ = ctl._demand(sim, jid, s.next_stage)
                down_next = max(sim.approach(jid, d).downstream_occ for d in sim.grid.junctions[jid].served[s.next_stage])
                info[k] = (cur, nxt, s.elapsed, down_next)
        ctl._ml = True
        out = ctl.decide(sim)
        for k, jid in enumerate(C.CONTROLLED):
            if base[k] == 1 and out[k] == 0:
                cur, nxt, el, dn = info[k]
                mc, mb, _ = ctl._demand(sim, jid, sim.signals[jid].stage); mn, mbn, _ = ctl._demand(sim, jid, sim.signals[jid].next_stage)
                holds.append({"t": sim.t, "j": k, "cur": cur, "nxt": nxt, "elapsed": el, "down_occ_now": dn, "stage": sim.signals[jid].stage,
                              "ml_cur": mc, "ml_nxt": mn, "ml_blocked_cur": mb, "ml_blocked_next": mbn})
            if out[k]:
                sim.request(jid)
        # follow-up: did the green end by max-green, and did the downstream really block in the next 30 s
        sim.advance(C.DECISION_S)
    m = sim.metrics()
    sim.close()
    return {"sc": sc, "seed": seed, "holds": holds, "forced": m["forced_changes"], "switches": m["signal_switches"], "wait": m["mean_wait_s"]}


if __name__ == "__main__":
    mp.set_executable(sys.executable)  # venv on Windows: spawned workers must use the venv interpreter
    jobs = [(sc, s) for sc in ("balanced", "ns_heavy", "ew_heavy", "pulse") for s in (4000, 4001)]
    with mp.get_context("spawn").Pool(6) as p:
        res = p.map(job, jobs)
    import numpy as np
    H = [h for r in res for h in r["holds"]]
    cur = np.array([h["cur"] for h in H]); nxt = np.array([h["nxt"] for h in H]); el = np.array([h["elapsed"] for h in H]); dn = np.array([h["down_occ_now"] for h in H])
    print("ML holds:", len(H), "episodes", len(res))
    print("cur-stage demand at hold: median %.1f, share <1 veh (empty green held) %.2f" % (np.median(cur), (cur < 1).mean()))
    print("waiting-stage demand: median %.1f, mean %.1f" % (np.median(nxt), nxt.mean()))
    print("ML view at hold: green-stage demand shift median %+.1f, waiting-stage shift median %+.1f, blocked_next %.2f, blocked_cur %.2f" % (
        np.median([h["ml_cur"] - h["cur"] for h in H]), np.median([h["ml_nxt"] - h["nxt"] for h in H]), np.mean([h["ml_blocked_next"] for h in H]), np.mean([h["ml_blocked_cur"] for h in H])))
    print("holds caused only by the shift (no blocked flag): %.2f; of those, gap-out cancelled (cur<1<=ml_cur): %.2f" % (
        np.mean([not h["ml_blocked_next"] for h in H]), np.mean([h["cur"] < 1 <= h["ml_cur"] for h in H if not h["ml_blocked_next"]])))
    print("elapsed green at hold: median %.0f s" % np.median(el))
    print("actual downstream occupancy of waiting stage at hold: median %.2f, share >=0.92 %.2f, share <0.7 %.2f" % (np.median(dn), (dn >= .92).mean(), (dn < .7).mean()))
    # consecutive holds on the same junction/stage form one extension
    ext = {}
    for r in res:
        for h in r["holds"]:
            ext.setdefault((r["sc"], r["seed"], h["j"], h["stage"], int(h["t"] - h["elapsed"])), []).append(h)
    lens = np.array([len(v) * 5 for v in ext.values()])
    print("extensions:", len(ext), "mean length %.1f s, max %d s" % (lens.mean(), lens.max()))
    reach = np.array([max(x["elapsed"] for x in v) + 5 >= 60 for v in ext.values()])
    print("extensions that ran into the 60 s max green: %.2f" % reach.mean())
    print(json.dumps([{k: r[k] for k in ("sc", "seed", "forced", "switches", "wait")} for r in res]))
