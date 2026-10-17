"""Paired comparisons on the held-out evaluation files (same demand per (scenario, seed)); bootstrap CI is seeded."""
import json, sys, numpy as np
from scipy import stats
rng = np.random.default_rng(0)
out = {}
for name in ("standard", "stress"):
    d = json.load(open(f"artifacts/rl/evaluation_{name}.json"))
    by = {}
    for e in d["episodes"]:
        c = "rl_ppo" if "@" in e["requested_controller"] else e["requested_controller"]
        by.setdefault(c, {})[(e["scenario"], e["seed"])] = e
    keys = sorted(by["fixed_time"])
    res = {}
    for a, b in (("queue_heuristic", "fixed_time"), ("rl_ppo", "queue_heuristic"), ("rl_ppo", "fixed_time")):
        for scope, sel in (("all", keys), *[(s, [k for k in keys if k[0] == s]) for s in sorted({k[0] for k in keys})]):
            for m in ("mean_wait_s", "mean_travel_s", "throughput_vph", "teleports"):
                x = np.array([by[a][k][m] for k in sel], float); y = np.array([by[b][k][m] for k in sel], float); dd = x - y
                boot = [rng.choice(dd, len(dd)).mean() for _ in range(4000)]
                try: p = float(stats.wilcoxon(dd).pvalue) if np.any(dd != 0) else None
                except ValueError: p = None
                res[f"{a} - {b} | {scope} | {m}"] = {"mean_diff": round(float(dd.mean()), 2), "ci95": [round(float(np.percentile(boot, 2.5)), 2), round(float(np.percentile(boot, 97.5)), 2)],
                                                     "wilcoxon_p": None if p is None else round(p, 4), "n": len(dd)}
    out[name] = res
json.dump(out, open("artifacts/rl/diagnostics/paired_heldout.json", "w"), indent=1)
for name in out:
    print("=====", name)
    for k, v in out[name].items():
        if "| all |" in k or ("mean_wait_s" in k and "rush_ramp" in k) or ("teleports" in k and "| all |" in k):
            print(f"{k:62s} diff {v['mean_diff']:+7.2f}  CI [{v['ci95'][0]:+7.2f},{v['ci95'][1]:+7.2f}] p={v['wilcoxon_p']}")
