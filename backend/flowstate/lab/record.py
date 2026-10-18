"""Run lab episodes (optionally recording frames) and build paired replays for synchronized playback.

A pair is two *independent* SUMO runs (separate processes) of one (scenario, seed, intensity): same network, same route file
(checked by its SHA-256), same SUMO seed, same horizon, same SafeSignal layer. Only the controller differs. Every number in a
replay file is measured from those runs.
"""

from __future__ import annotations

import gzip
import json
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ..rl import config as C

HORIZON_S, DRAIN_S = 900.0, 600.0
KPI_FIELDS = ["inNetwork", "halting", "meanWaitNow", "arrived", "co2Kg", "teleports", "collisions", "pending"]
COMPARE = [("mean_wait_s", "Mean waiting time", "s", -1), ("mean_travel_s", "Mean travel time", "s", -1),
           ("p95_travel_s", "95th-pct travel time", "s", -1), ("throughput_vph", "Throughput (demand window)", "veh/h", 1),
           ("queue_total_mean_veh", "Mean queued vehicles", "veh", -1), ("queue_max_veh", "Peak queued vehicles", "veh", -1),
           ("spillback_pct", "Spillback", "% block-s", -1), ("co2_kg", "CO2 (HBEFA3 estimate)", "kg", -1),
           ("teleports", "Teleports", "", -1), ("collisions", "Collisions", "", -1), ("vehicles_completed", "Vehicles completed", "", 1),
           ("mean_wait_civilian_s", "Mean wait, all vehicles except ambulances", "s", -1), ("ev_travel_mean_s", "Ambulance travel time", "s", -1),
           ("ev_stopped_red_s", "Ambulance stopped at red lights", "s", -1), ("ev_stopped_queue_s", "Ambulance stuck behind queues", "s", -1)]


def replays_dir() -> Path:
    from .forecast import data_dir

    return data_dir() / "replays"


def run_episode(cid: str, scenario: str, seed: int, intensity: float = 1.0, record: bool = False, horizon: float = HORIZON_S,
                drain: float = DRAIN_S) -> dict[str, Any]:
    from .control import make_controller
    from .sim import LabSim

    sim = LabSim(f"rec{mp.current_process().pid}", record=record)
    try:
        man = sim.reset(scenario, seed, horizon, drain, intensity)
        ctl, info = make_controller(cid, sim, shadow_forecast=record)
        ctl.reset(sim)
        obs: list[dict[str, Any]] = []
        t0 = time.time()
        while not sim.done:
            sim.observe()
            actions = ctl.decide(sim)
            for jid, a in zip(C.CONTROLLED, actions):
                if a:
                    sim.request(jid)
            if record:
                o: dict[str, Any] = {"t": int(sim.t), "q": sim.state.X[:, 0].round(1).tolist()}
                if sim.forecast is not None:
                    o |= {k: sim.forecast[k].round(1).tolist() for k in ("p30", "p60", "lo30", "hi30", "lo60", "hi60") if k in sim.forecast}
                if getattr(ctl, "last_changed", None):
                    o["ml"] = ctl.last_changed
                obs.append(o)
            sim.advance(C.DECISION_S)
        m = sim.metrics()
        out = {"controller": cid, "controllerInfo": info, "metrics": m, "timing": sim.timing(), "wall_s": round(time.time() - t0, 2),
               "demandSha": man["sha256"], "intensity": intensity}
        if hasattr(ctl, "stats"):
            out["ml"] = ctl.stats()
            out["mlLog"] = ctl.log
            out["mlEvidence"] = ctl.evidence_log
        if record:
            out["frames"] = sim.frames
            out["obs"] = obs
        return out
    finally:
        sim.close()


def _job(args: tuple[str, str, int, float, bool]) -> dict[str, Any]:
    return run_episode(*args)


def pair_id(left: str, right: str, scenario: str, seed: int, intensity: float) -> str:
    return f"{left}__{right}__{scenario}__{seed}__{intensity:g}"


def diff_table(lm: dict[str, Any], rm: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, label, unit, better in COMPARE:
        a, b = float(lm[key]), float(rm[key])
        d = b - a
        rows.append({"key": key, "label": label, "unit": unit, "left": round(a, 2), "right": round(b, 2), "diff": round(d, 2),
                     "pct": round(100.0 * d / a, 1) if abs(a) > 1e-9 else None,
                     "verdict": "same" if abs(d) < 1e-9 else ("right better" if d * better > 0 else "left better")})
    return rows


def record_pair(left: str, right: str, scenario: str, seed: int, intensity: float = 1.0, pool: ProcessPoolExecutor | None = None) -> dict[str, Any]:
    from .forecast import Forecaster

    own = pool is None
    pool = pool or ProcessPoolExecutor(2, mp_context=mp.get_context("spawn"))
    try:
        fl = pool.submit(_job, (left, scenario, seed, intensity, True))
        fr = pool.submit(_job, (right, scenario, seed, intensity, True))
        L, R = fl.result(), fr.result()
    finally:
        if own:
            pool.shutdown()
    if L["demandSha"] != R["demandSha"]:
        raise RuntimeError("paired runs saw different demand; refusing to compare")
    pid = pair_id(left, right, scenario, seed, intensity)
    fc = Forecaster.load_default()
    doc = {"id": pid, "scenario": scenario, "seed": seed, "intensity": intensity, "horizon_s": HORIZON_S, "demandSha": L["demandSha"],
           "kpiFields": KPI_FIELDS, "left": L, "right": R, "diff": diff_table(L["metrics"], R["metrics"]),
           "mlInControl": {"left": L["controllerInfo"].get("mlInControl", False), "right": R["controllerInfo"].get("mlInControl", False)},
           "forecaster": {"kind": fc.meta.get("kind"), "trained_at": fc.meta.get("trained_at"), "sha256": fc.sha256[:16], "calibrated": bool(fc.calibration)} if fc else None,
           "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    d = replays_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json.gz").write_bytes(gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), 6))
    _update_index(doc)
    return summary_of(doc)


def summary_of(doc: dict[str, Any]) -> dict[str, Any]:
    return {"id": doc["id"], "scenario": doc["scenario"], "seed": doc["seed"], "intensity": doc["intensity"], "left": doc["left"]["controller"],
            "right": doc["right"]["controller"], "mlInControl": doc["mlInControl"], "diff": doc["diff"], "demandSha": doc["demandSha"],
            "ml": doc["right"].get("ml"), "recorded_at": doc["recorded_at"], "duration_s": len(doc["left"]["frames"])}


def _update_index(doc: dict[str, Any]) -> None:
    f = replays_dir() / "index.json"
    idx = json.loads(f.read_text()) if f.exists() else {}
    idx[doc["id"]] = summary_of(doc)
    f.write_text(json.dumps(idx, indent=1))


def index() -> dict[str, Any]:
    f = replays_dir() / "index.json"
    return json.loads(f.read_text()) if f.exists() else {}


DEFAULT_PAIRS = [
    ("queue_heuristic", "ml_forecast_ev", "rush_ramp", 3100, 1.18),  # the demo pair: ML forecast + lights cleared ahead of ambulances
    ("queue_heuristic", "ml_forecast_ev", "pulse", 3101, 1.18),
    ("queue_heuristic", "ml_forecast_ev", "balanced", 3102, 1.0),
    ("queue_heuristic", "ml_forecast_ev", "ns_heavy", 3103, 1.18),
    ("queue_heuristic", "ml_forecast", "rush_ramp", 3100, 1.18),  # forecast effect alone
    ("queue_heuristic", "ml_forecast", "pulse", 3101, 1.18),
    ("fixed_time", "queue_heuristic", "rush_ramp", 3100, 1.18),
    ("fixed_time", "queue_heuristic", "balanced", 3102, 1.0),
]


def record_defaults(workers: int = 4) -> list[dict[str, Any]]:
    out = []
    with ProcessPoolExecutor(min(workers, 2), mp_context=mp.get_context("spawn")) as pool:
        for left, right, sc, seed, inten in DEFAULT_PAIRS:
            s = record_pair(left, right, sc, seed, inten, pool)
            out.append({"id": s["id"], "mean_wait": [r for r in s["diff"] if r["key"] == "mean_wait_s"][0]})
    return out
