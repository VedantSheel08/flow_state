"""Versioned intelligence contract (``flowstate.intelligence/v1``): the one shape every consumer reads.

Three kinds of values never share a field, in any response:

* ``observed``      - measured by SUMO detectors at ``t`` (queues, counts, waiting). Ground truth for time ``t``.
* ``predicted``     - model output made at ``madeAtT`` for ``targetT = madeAtT + horizon``; carries the model version and, when a
                      calibration is bound to the artifact, an empirical interval. ``null`` when no model is available.
* ``interventions`` - what the controller actually did, with evidence (the forecast values behind each ML-changed decision).

This module is pure (no SUMO, no FastAPI), so the same builders serve the lab's live stream, replays and, later, the main app.
"""

from __future__ import annotations

import functools
from typing import Any

SCHEMA = "flowstate.intelligence/v1"
HORIZONS_S = (30, 60)
SPILL_FRAC = 0.5  # an approach is a bottleneck / spillback risk when its queue covers at least this share of its capacity
SEVERITY_EDGES = (0.0, 0.10, 0.25, 0.50)  # share of capacity; severity names below
SEVERITY = ("free", "light", "heavy", "spillback")


def severity_of(queue: float, cap: float) -> str:
    r = queue / cap if cap else 0.0
    k = sum(1 for e in SEVERITY_EDGES[1:] if r >= e)
    return SEVERITY[k]


@functools.lru_cache(maxsize=1)
def approach_table() -> tuple[dict[str, Any], ...]:
    """The 16 signalised approaches in model order (static: from the network, no simulation needed)."""
    from .sim import LabSim

    sim = LabSim("contract")
    return tuple({"index": i, "key": f"{a.jid}:{a.direction}", "junction": a.jid, "direction": a.direction, "edge": a.edge,
                  "capacity": round(float(a.capacity), 2), "entry": bool(a.is_entry)} for i, a in enumerate(sim.topo.approaches))


def _round(v: float, d: int = 2) -> float:
    return round(float(v), d)


def build_report(*, t: float, queues: list[float], capacity: list[float] | None = None, extra: list[dict[str, Any]] | None = None,
                 forecast: dict[str, Any] | None = None, model: dict[str, Any] | None = None, controller: dict[str, Any] | None = None,
                 emergency: list[dict[str, Any]] | None = None,
                 decisions: list[dict[str, Any]] | None = None, source: str = "live", context: dict[str, Any] | None = None) -> dict[str, Any]:
    """One time-slice of intelligence.

    ``forecast`` is ``{"p30": [...], "p60": [...], "lo30"/"hi30"/"lo60"/"hi60": [...] (optional)}``, made at ``t`` from the observation at ``t``.
    ``decisions`` are ML-changed controller decisions already recorded for the run (each with its evidence).
    """
    table = approach_table()
    cap = capacity or [a["capacity"] for a in table]
    observed = []
    for i, a in enumerate(table):
        row = {"key": a["key"], "junction": a["junction"], "direction": a["direction"], "queue": _round(queues[i], 1), "capacity": _round(cap[i], 1),
               "occupancy": _round(queues[i] / cap[i], 3) if cap[i] else 0.0, "severity": severity_of(queues[i], cap[i])}
        if extra:
            row |= extra[i]
        observed.append(row)
    predicted: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = []
    for i, a in enumerate(table):
        if queues[i] >= SPILL_FRAC * cap[i]:
            warnings.append({"approach": a["key"], "kind": "bottleneck_now", "basis": "observed", "horizonS": 0, "queue": _round(queues[i], 1),
                             "threshold": _round(SPILL_FRAC * cap[i], 1), "message": f"{a['key']} queue covers {100 * queues[i] / cap[i]:.0f}% of the approach (measured)"})
    if forecast is not None and model is not None:
        rows = []
        for i, a in enumerate(table):
            r: dict[str, Any] = {"key": a["key"]}
            for h in HORIZONS_S:
                v = float(forecast[f"p{h}"][i])
                cell: dict[str, Any] = {"horizonS": h, "targetT": t + h, "queue": _round(v, 2), "severity": severity_of(v, cap[i])}
                if f"lo{h}" in forecast:
                    cell["interval"] = {"lo": _round(forecast[f"lo{h}"][i], 2), "hi": _round(forecast[f"hi{h}"][i], 2), "level": model.get("intervalLevel")}
                    if forecast[f"hi{h}"][i] >= SPILL_FRAC * cap[i] > v:
                        cell["possibleSpillback"] = True  # point forecast is below the threshold but the calibrated upper bound is not
                r[f"p{h}"] = cell
                rule = (model.get("alert") or {}).get(str(h), {})
                tau = float(rule.get("tau", SPILL_FRAC))
                if v >= tau * cap[i] and queues[i] < SPILL_FRAC * cap[i]:
                    w = {"approach": a["key"], "kind": "spillback_risk", "basis": "predicted", "horizonS": h, "targetT": t + h, "queue": _round(v, 1),
                         "threshold": _round(tau * cap[i], 1), "rule": f"point forecast >= {tau:.3f} x capacity",
                         "message": f"{a['key']} forecast to reach {100 * v / cap[i]:.0f}% of the approach in {h} s (model forecast, not a measurement)"}
                    if "heldOutPrecision" in rule:  # how often this rule was right on held-out seeds: a measured rate, not a per-alert probability
                        w["ruleHeldOut"] = {"precision": rule["heldOutPrecision"], "recall": rule["heldOutRecall"]}
                    warnings.append(w)
            rows.append(r)
        predicted = {"kind": "prediction", "madeAtT": t, "model": model, "approaches": rows}
    return {"schema": SCHEMA, "source": source, "t": t, "context": context or {},
            "observed": {"kind": "observed", "t": t, "approaches": observed, "emergencyVehicles": emergency or [],
                         "network": {"queued": _round(sum(queues), 1), "approachesAtOrOverHalf": sum(1 for i in range(len(table)) if queues[i] >= SPILL_FRAC * cap[i])}},
            "predicted": predicted,
            "warnings": warnings,
            "interventions": {"kind": "controller_intervention", "controller": controller, "decisions": decisions or []}}


def model_block(status: dict[str, Any]) -> dict[str, Any]:
    """The small model descriptor embedded in every prediction."""
    return {"id": status.get("modelId"), "version": status.get("version"), "sha256": (status.get("sha256") or "")[:16],
            "calibrated": bool(status.get("calibrated")), "intervalLevel": status.get("intervalLevel"), "alert": status.get("alert"), "skillByControlRegime": status.get("skillByControlRegime"), "health": status.get("health")}


def observed_extra(X: Any) -> list[dict[str, Any]]:
    """Per-approach detector fields from a TrafficState matrix (feature order = flowstate.lab.sim.FEATURES)."""
    from .sim import FEATURES

    c = {n: FEATURES.index(n) for n in ("count", "mean_wait", "down_occ", "is_green")}
    return [{"vehicles": int(r[c["count"]]), "meanWaitS": _round(r[c["mean_wait"]], 1), "downstreamOccupancy": _round(r[c["down_occ"]], 3), "signalGreen": bool(r[c["is_green"]])} for r in X]
