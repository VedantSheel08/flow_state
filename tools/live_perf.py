"""Live-server cost of a jam preset: engine step, frame build, JSON size, one process, no evaluation collector.

    python tools/live_perf.py gridlock_stress 42 out.json
Run it on an otherwise idle machine; times are wall-clock for this process only.
"""
import gzip, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flowstate.api.service import NETWORKS_DIR, WORK_DIR
from flowstate.performance.congestion import engine_options, resolve
from flowstate.scenarios.catalog import create
from flowstate.sim.engine import Engine, RunConfig

preset, seed, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
params, warmup, _ = resolve({"duration": 5}, preset, "rush_hour")
sc = create("rush_hour", params)
eng = Engine(sc, RunConfig("rush_hour", sc.params, seed, "fixed", warmup=warmup, label="perf", **engine_options(preset)), NETWORKS_DIR, WORK_DIR / "perf")
eng.start()
cpu0, wall0 = time.process_time(), time.perf_counter()
rows, steps, frames, sizes = [], [], [], []
peak = 0
while not eng.finished:
    t = time.perf_counter(); eng.step(); steps.append((time.perf_counter() - t) * 1000)
    n = len(steps)
    if n % 4 == 0:  # the live loop publishes a frame about once per simulated second
        t = time.perf_counter(); f = eng.frame(); s = json.dumps(f, separators=(",", ":")); frames.append((time.perf_counter() - t) * 1000)
        sizes.append((len(s), len(gzip.compress(s.encode(), 1)), len(eng.vehicles)))
wall, cpu = time.perf_counter() - wall0, time.process_time() - cpu0
q = lambda a, p: round(sorted(a)[min(len(a) - 1, int(p * len(a)))], 2)
# peak memory
import importlib; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from flowstate.evaluation.runner import _peak_rss_mb
res = {"preset": preset, "seed": seed, "simSeconds": round(eng.t + warmup), "wallSeconds": round(wall, 1), "cpuCoresAverage": round(cpu / wall, 2), "peakRssMB": _peak_rss_mb(),
       "stepMs": {"mean": round(sum(steps) / len(steps), 2), "p50": q(steps, .5), "p95": q(steps, .95), "p99": q(steps, .99), "max": round(max(steps), 1)},
       "frameBuildMs": {"mean": round(sum(frames) / len(frames), 2), "p95": q(frames, .95), "max": round(max(frames), 1)},
       "frameBytes": {"jsonMax": max(s[0] for s in sizes), "jsonAtPeakVehicles": max(sizes, key=lambda s: s[2])[0], "gzip1AtPeak": max(sizes, key=lambda s: s[2])[1], "maxVehicles": max(s[2] for s in sizes)},
       "realtimeFactor": round((eng.t + warmup) / wall, 2), "stepBudgetMs": 250.0}
eng.close()
out.write_text(json.dumps(res, indent=1)); print(json.dumps(res))
