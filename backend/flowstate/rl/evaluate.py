"""Fair, reproducible controller comparison.

Every (scenario, seed) pair produces *one* demand file (identified by a hash); each controller is run on exactly
that file, with the same network, the same SUMO seed, the same safety layer and the same measurement code.
Episodes run for the demand horizon and then keep going (controller still active) until every vehicle has
arrived, so travel and waiting times are not flattered by trips that are simply left unfinished.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import config as C
from .controllers import Controller, make
from .sim import SignalSim

DRAIN_S = 900.0


def run_episode(controller: Controller, scenario: str, seed: int, *, horizon_s: float = 900.0, drain_s: float = DRAIN_S,
                intensity: float = 1.0, sim: SignalSim | None = None, label: str = "eval") -> dict[str, Any]:
    own = sim is None
    sim = sim or SignalSim(label)
    try:
        sim.reset(scenario, seed, horizon_s, drain_s, intensity)
        controller.reset(sim)
        t0 = time.time()
        while not sim.done:
            actions = controller.decide(sim)
            for jid, a in zip(C.CONTROLLED, actions):
                if a == 1:
                    sim.request(jid)
            sim.advance(C.DECISION_S)
        m = sim.metrics()
        m["controller"] = controller.id
        m["wall_s"] = round(time.time() - t0, 2)
        m["series"] = [(round(t), round(q, 1), round(w, 1), n) for t, q, w, n in sim.series]
        return m
    finally:
        if own:
            sim.close()


def _worker(job: tuple[str, str, int, float, float, float]) -> dict[str, Any]:
    cid, scenario, seed, horizon, drain, intensity = job
    controller, info = make(cid)
    m = run_episode(controller, scenario, seed, horizon_s=horizon, drain_s=drain, intensity=intensity, label=f"w{mp.current_process().pid}")
    m["requested_controller"] = cid
    m["fallback"] = info.get("fallback", False)
    return m


def evaluate(controller_ids: list[str], scenarios: list[str], seeds: list[int], *, horizon_s: float = 900.0, drain_s: float = DRAIN_S,
             intensity: float = 1.0, workers: int = 4, progress: Callable[[int, int], None] | None = None) -> list[dict[str, Any]]:
    """Run every controller on every (scenario, seed). Each worker process owns one libsumo simulation."""
    jobs = [(c, sc, s, horizon_s, drain_s, intensity) for sc in scenarios for s in seeds for c in controller_ids]
    ctx = mp.get_context("spawn")
    rows: list[dict[str, Any]] = []
    with ctx.Pool(workers) as pool:
        for k, r in enumerate(pool.imap_unordered(_worker, jobs), 1):
            rows.append(r)
            if progress:
                progress(k, len(jobs))
    rows.sort(key=lambda r: (r["scenario"], r["seed"], r["requested_controller"]))
    return rows


METRICS = ["mean_wait_s", "mean_travel_s", "p95_travel_s", "throughput_vph", "completed_fraction", "queue_mean_veh", "queue_max_veh",
           "spillback_pct", "teleports", "collisions", "emergency_mean_stopped_s"]
LOWER_IS_BETTER = {m: m not in ("throughput_vph", "completed_fraction") for m in METRICS}


def summarise(rows: list[dict[str, Any]], baseline: str = "fixed_time", rng_seed: int = 0) -> dict[str, Any]:
    """Mean, standard deviation across seeds and paired differences (bootstrap 95 % CI, Wilcoxon) against ``baseline``."""
    from scipy import stats

    by: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["requested_controller"], {})[(r["scenario"], r["seed"])] = r
    scenarios = sorted({r["scenario"] for r in rows})
    out: dict[str, Any] = {"baseline": baseline, "controllers": {}, "scenarios": scenarios, "episodes": len(rows)}
    rng = np.random.default_rng(rng_seed)

    def block(sel: list[tuple[str, int]]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for cid, runs in by.items():
            res[cid] = {"n": len(sel), "metrics": {}}
            for m in METRICS:
                v = np.array([runs[k][m] for k in sel if k in runs], dtype=float)
                res[cid]["metrics"][m] = {"mean": float(v.mean()), "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                                          "min": float(v.min()), "max": float(v.max())}
                if cid != baseline and baseline in by:
                    b = np.array([by[baseline][k][m] for k in sel if k in runs and k in by[baseline]], dtype=float)
                    d = v[: len(b)] - b
                    boot = [rng.choice(d, len(d)).mean() for _ in range(2000)] if len(d) > 1 else [d.mean()]
                    try:
                        p = float(stats.wilcoxon(d).pvalue) if np.any(d != 0) and len(d) > 5 else None
                    except ValueError:
                        p = None
                    res[cid]["metrics"][m]["vs_baseline"] = {
                        "mean_diff": float(d.mean()), "ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
                        "pct_change": float(100.0 * d.mean() / b.mean()) if b.mean() else None, "wilcoxon_p": p,
                        "better_in": int(np.sum(d < 0) if LOWER_IS_BETTER[m] else np.sum(d > 0)), "worse_in": int(np.sum(d > 0) if LOWER_IS_BETTER[m] else np.sum(d < 0)),
                        "paired": len(d)}
        return res

    keys = sorted({(r["scenario"], r["seed"]) for r in rows})
    out["overall"] = block(keys)
    out["per_scenario"] = {sc: block([k for k in keys if k[0] == sc]) for sc in scenarios}
    return out


def save(rows: list[dict[str, Any]], summary: dict[str, Any], path: Path, meta: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = [{k: v for k, v in r.items() if k != "series"} for r in rows]
    path.write_text(json.dumps({"meta": meta or {}, "summary": summary, "episodes": slim}, indent=1), encoding="utf-8")
