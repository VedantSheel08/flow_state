"""Diagnostic: episode return under the training reward for each controller on identical demand (no learning involved)."""
import sys, json, numpy as np
from flowstate.rl import config as C
from flowstate.rl.env import SignalControlEnv
from flowstate.rl.controllers import make, FixedTime
from flowstate.rl.demand import TRAIN_SCENARIOS

def rollout(env, ctl, sc, seed, rng=None):
    env.reset(options={"scenario": sc, "demand_seed": seed, "intensity": 1.0}); ctl.reset(env.sim) if ctl else None
    done=False
    while not done:
        a = rng.integers(0,2,4) if ctl is None else ctl.decide(env.sim)
        _,r,_,done,info = env.step(a)
    return env.episode_return, info["episode_metrics"]
if __name__=="__main__":
    env = SignalControlEnv(TRAIN_SCENARIOS, horizon_s=900.0, drain_s=0.0, label="diag")
    rng=np.random.default_rng(0)
    cands = {"fixed30": make("fixed_time")[0], "heuristic": make("queue_heuristic")[0], "random": None,
             "always_hold": type("H",(),{"reset":lambda s,x:None,"decide":lambda s,sim:[0,0,0,0]})(),
             "rl60k": make("rl_ppo@artifacts/rl/run1/best_validation.zip")[0]}
    out={}
    for n,c in cands.items():
        rs=[];ws=[]
        for sc in TRAIN_SCENARIOS:
            for sd in (2000,2001):
                r,m=rollout(env,c,sc,sd,rng); rs.append(r); ws.append(m["mean_wait_s"])
        out[n]={"return":round(float(np.mean(rs)),1),"mean_wait_s_900s":round(float(np.mean(ws)),1)}
        print(n,out[n],flush=True)
    json.dump(out,open("artifacts/rl/diagnostics/reward_check.json","w"),indent=1)
