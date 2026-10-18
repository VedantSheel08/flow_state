"""Turn an instrumented episode into the congestion measurements and replay markers the demo package reports.

Every threshold is a fixed rule written down here (and copied into the manifest), not tuned per seed. Storage means the number
of queued vehicles the 16 signalised approaches can hold (road length x lanes / 7.5 m per queued vehicle).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .runner import FORECAST_SPILL, SPILL_OCC

RULES = {
    "queue_buildup": "first 10 s with halted vehicles on the 16 approaches >= 5 % of their storage, held for 3 consecutive samples (15 s)",
    "congested": "first time halted vehicles reach 15 % of approach storage (3 consecutive samples)",
    "spillback": f"first sample where an internal link has halted vehicles filling >= {int(SPILL_OCC * 100)} % of its storage (same rule as the lab's spillback %)",
    "predicted_spillback": (f"first forecast (30 s ahead) of an approach queue >= {int(FORECAST_SPILL * 100)} % of its storage while its current "
                            "queue is still below 50 %; 'verified' when that approach reaches 50 % within 60 s (the forecaster's own spillback definition)"),
    "controller_intervention": "decisions where the ML-forecast controller differs from the plain heuristic (clustered into 30 s bins)",
    "recovery_start": "first time after the queue peak that halted vehicles stay below 80 % of the peak for 30 s",
    "recovery_half": "first time after the peak that halted vehicles stay below 50 % of the peak for 30 s",
    "cleared": "first time after the peak that halted vehicles stay below 10 % of approach storage for 30 s",
    "near_standstill": "network mean speed below 1.5 m/s with >= 50 % of vehicles halted, held for 30 s",
}


def _first(ts: list[float], flags: list[bool], hold: int = 3, start: float = 0.0) -> float | None:
    run = 0
    for i, (t, f) in enumerate(zip(ts, flags)):
        if t < start:
            continue
        run = run + 1 if f else 0
        if run >= hold:
            return ts[i - hold + 1]
    return None


def analyse(run: dict[str, Any]) -> dict[str, Any]:
    s = run["series"]
    ts = [r["t"] for r in s]
    q = np.array([r["queue"] for r in s])
    storage = float(sum(run["approachCap"]))
    speed = np.array([r["meanSpeed"] for r in s])
    halted = np.array([r["haltedShare"] for r in s])
    inn = np.array([r["inNet"] for r in s])
    horizon = run["metrics"]["horizon_s"]
    peak_i = int(q.argmax())
    peak_t = ts[peak_i]
    out: dict[str, Any] = {"storage_veh": round(storage, 1), "queue_peak_veh": float(q.max()), "queue_peak_pct_storage": round(100 * float(q.max()) / storage, 1),
                           "queue_peak_t_s": peak_t, "in_network_peak": int(inn.max()), "in_network_peak_t_s": ts[int(inn.argmax())]}
    out["queue_mean_veh"] = round(float(q.mean()), 1)
    out["queue_mean_in_window_veh"] = round(float(q[[t <= horizon for t in ts]].mean()), 1)
    # buildup: growth rate between 10 % storage and the peak
    t10 = _first(ts, list(q >= 0.05 * storage))
    t25 = _first(ts, list(q >= 0.15 * storage))
    out["t_queue_buildup_s"], out["t_congested_s"] = t10, t25
    if t10 is not None and peak_t > t10:
        out["queue_growth_veh_per_min"] = round((float(q.max()) - 0.05 * storage) / ((peak_t - t10) / 60.0), 1)
    # speed and standstill
    win = [t <= horizon for t in ts]
    out["mean_speed_ms_window"] = round(float(speed[win].mean()), 2)
    out["min_speed_ms_30s_avg"] = round(float(np.convolve(speed, np.ones(6) / 6, "valid").min()), 2)
    out["halted_share_peak"] = round(float(halted.max()), 3)
    out["halted_share_mean_window"] = round(float(halted[win].mean()), 3)
    out["t_near_standstill_s"] = _first(ts, list((speed < 1.5) & (halted >= 0.5)), hold=6)
    out["near_standstill_seconds"] = float(SERIES_S * np.sum((speed < 1.5) & (halted >= 0.5)))
    # spillback
    sp = [r["spilled"] for r in s]
    first = next(((r["t"], r["spilled"][0]) for r in s if r["spilled"]), None)
    out["t_spillback_s"], out["spillback_first_link"] = (first if first else (None, None))
    out["spilled_links_peak"] = max(len(x) for x in sp)
    out["spillback_link_seconds"] = float(SERIES_S * sum(len(x) for x in sp))
    # which approach queued first (where the problem starts): first approach to reach 50 % storage
    qa = np.array([r.get("q", []) for r in s])
    cap = np.array(run["approachCap"])
    if qa.size:
        full = qa >= SPILL_OCC * cap
        if full.any():
            i = int(np.argmax(full.any(axis=1)))
            out["first_full_approach"] = {"approach": run["approachKeys"][int(np.argmax(full[i]))], "t_s": ts[i]}
            out["approaches_ever_full"] = [run["approachKeys"][k] for k in range(len(cap)) if full[:, k].any()]
    # recovery
    after = [t >= peak_t for t in ts]
    out["t_recovery_start_s"] = _first(ts, list(q < 0.8 * q.max()), hold=6, start=peak_t)
    out["t_recovery_half_s"] = _first(ts, list(q < 0.5 * q.max()), hold=6, start=peak_t)
    out["t_cleared_s"] = _first(ts, list(q < 0.1 * storage), hold=6, start=peak_t)
    out["queue_end_veh"] = float(q[-1])
    # throughput and delay (regular vehicles)
    m, core, d = run["metrics"], run.get("core", {}), run["demand"]
    out |= {"throughput_vph": round(m["throughput_vph"], 0), "completed_fraction": d["completedFraction"],
            "mean_wait_s": round(core.get("mean_wait_s", m["mean_wait_s"]), 1), "p95_wait_s": round(core.get("p95_wait_s", 0.0), 1),
            "mean_travel_s": round(core.get("mean_travel_s", m["mean_travel_s"]), 1), "p95_travel_s": round(core.get("p95_travel_s", 0.0), 1),
            "spillback_pct": round(m["spillback_pct"], 2), "teleports": m["teleports"], "collisions": m["collisions"],
            "teleported_vehicles": d["teleportedVehicles"], "never_inserted": d["neverInserted"], "on_road_at_end": d["onRoadAtEnd"],
            "peak_pending_vehicles": int(max(r["pending"] for r in s))}
    out["markers"] = markers(run, out)
    return out


SERIES_S = 5.0


def markers(run: dict[str, Any], a: dict[str, Any]) -> list[dict[str, Any]]:
    """Replay markers in simulated seconds. Kinds: queue_buildup, spillback, predicted_spillback, controller_intervention, recovery, incident."""
    mk: list[dict[str, Any]] = []

    def add(kind: str, t: float | None, label: str, **extra: Any) -> None:
        if t is not None:
            mk.append({"kind": kind, "tSec": float(t), "label": label, **extra})

    add("queue_buildup", a["t_queue_buildup_s"], "Queues start to build")
    add("queue_buildup", a["t_congested_s"], "Network congested (15 % of approach storage halted)", level="congested")
    for inc in run["incident"]:
        add("incident", inc["stoppedFrom_s"], "Vehicle breaks down and blocks a lane", vehicle=inc["vehicle"])
        add("incident", inc["stoppedTo_s"] + 1, "Breakdown clears", vehicle=inc["vehicle"])
    add("spillback", a["t_spillback_s"], f"Spillback begins on link {a['spillback_first_link']}" if a["t_spillback_s"] is not None else "", link=a["spillback_first_link"])
    pred = predicted_spillback(run)
    if pred:
        add("predicted_spillback", pred["t_s"], f"Forecast: {pred['approach']} queue will fill to {pred['p30']:.0f} of {pred['cap']:.0f} within 30 s", approach=pred["approach"],
            verified=pred["verified"], leadS=pred["lead_s"])
    for t, n in intervention_bins(run):
        add("controller_intervention", t, f"ML changed {n} signal decision(s)", count=n)
    add("recovery", a["t_recovery_start_s"], "Recovery begins (queue below 80 % of peak)", level="start")
    add("recovery", a["t_recovery_half_s"], "Queue halved from peak", level="half")
    add("recovery", a["t_cleared_s"], "Queues cleared (below 10 % of storage)", level="cleared")
    mk.sort(key=lambda m: m["tSec"])
    return mk


def predicted_spillback(run: dict[str, Any]) -> dict[str, Any] | None:
    keys, cap = run["approachKeys"], np.array(run["approachCap"])
    qs = {r["t"]: np.array(r.get("q", [])) for r in run["series"]}
    fc = run["forecasts"]
    if not fc or not qs:
        return None
    for f in fc:
        q, p30 = qs.get(f["t"]), np.array(f["p30"])
        if q is None or q.size == 0:
            continue
        hit = (p30 >= FORECAST_SPILL * cap) & (q < SPILL_OCC * cap)
        if hit.any():
            i = int(np.argmax(hit))
            later = [t for t, qq in qs.items() if f["t"] < t <= f["t"] + 60 and qq.size and qq[i] >= SPILL_OCC * cap[i]]
            return {"t_s": f["t"], "approach": keys[i], "p30": float(p30[i]), "cap": float(cap[i]), "verified": bool(later),
                    "lead_s": (later[0] - f["t"]) if later else None}
    return None


def forecast_skill(run: dict[str, Any]) -> dict[str, Any]:
    """How good were the shadow forecasts *in this run*: precision/recall of 'approach reaches 50 % of storage within 60 s' and lead time."""
    keys, cap = run["approachKeys"], np.array(run["approachCap"])
    ts = [r["t"] for r in run["series"]]
    q = np.array([r.get("q", []) for r in run["series"]])
    if not run["forecasts"] or q.size == 0:
        return {}
    tp = fp = fn = 0
    leads: list[float] = []
    for f in run["forecasts"]:
        i = ts.index(f["t"]) if f["t"] in ts else None
        if i is None:
            continue
        j = [k for k, t in enumerate(ts) if f["t"] < t <= f["t"] + 60]
        if not j:
            continue
        future = (q[j] >= SPILL_OCC * cap).any(axis=0) & (q[i] < SPILL_OCC * cap)
        pred = (np.array(f["p30"]) >= FORECAST_SPILL * cap) & (q[i] < SPILL_OCC * cap)
        tp += int((pred & future).sum())
        fp += int((pred & ~future).sum())
        fn += int((~pred & future).sum())
        for k in np.where(pred & future)[0]:
            leads.append(next(ts[x] for x in j if q[x, k] >= SPILL_OCC * cap[k]) - f["t"])
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "median_lead_s": float(np.median(leads)) if leads else None}


def intervention_bins(run: dict[str, Any], bin_s: int = 30) -> list[tuple[float, int]]:
    out: dict[int, int] = {}
    for t, _k, _d in run.get("mlLog", []):
        out[int(t) // bin_s * bin_s] = out.get(int(t) // bin_s * bin_s, 0) + 1
    return sorted((float(k), v) for k, v in out.items())
