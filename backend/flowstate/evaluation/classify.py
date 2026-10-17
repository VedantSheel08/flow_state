"""Outcome classifier: contract targets -> achieved class (docs/CONGESTION_ACCEPTANCE.md, criteria version 1).

The classifier never reads the requested preset to decide what was achieved. It evaluates the measured series
against the contract's tier gates and returns the highest tier whose *traffic-state* gates all pass, plus the
safety/accounting gates that every tier needs. Gate results carry the measured numbers, so a failure says why.
"""

from __future__ import annotations

import statistics
from typing import Any

CRITERIA_VERSION = 1
OBSERVATION_S = 300
MIN_SECONDS = 240  # density / storage minima must hold at least this long within the observation
TIER_ORDER = ["rush", "severe", "gridlock"]

TIERS: dict[str, dict[str, Any]] = {
    "rush": {"density": (40, 75), "storage_min": 0.30, "body_min": 0.20, "p50": (8, 20), "p90_max": 35},
    "severe": {"density": (85, 115), "storage_min": 0.65, "body_min": 0.40, "p50_max": 8, "p90_max": 20, "halted_min": 0.55},
    "gridlock": {"density_min": 110, "storage_min": 0.85, "body_min": 0.50, "p50_max": 1, "p90_max": 5, "halted_min": 0.85},
}


def _gate(name: str, ok: bool | None, measured: Any, required: str) -> dict[str, Any]:
    return {"gate": name, "ok": ok, "measured": measured, "required": required}


def _med(values: list[float | None]) -> float | None:
    v = [x for x in values if x is not None]
    return statistics.median(v) if v else None


def _secs(rows: list[dict[str, Any]], pred) -> int:
    return sum(1 for r in rows if pred(r))


def traffic_gates(tier: str, m: dict[str, Any]) -> list[dict[str, Any]]:
    """Traffic-state gates of one tier. ``m`` holds the measured series and episode evidence (see runner.analyze)."""
    spec = TIERS[tier]
    key = "dzRows" if tier == "gridlock" and m.get("dzRows") else "rows"
    rows = m[key]
    out: list[dict[str, Any]] = []
    scope = "declared zone" if key == "dzRows" else "interior arterials"
    lo = spec["density"][0] if "density" in spec else spec["density_min"]
    secs = _secs(rows, lambda r: r["density"] >= lo)
    mean_density = statistics.fmean(r["density"] for r in rows) if rows else 0.0
    out.append(_gate("density", secs >= MIN_SECONDS, {"secondsAtLeastMin": secs, "mean": round(mean_density, 1)},
                     f">= {lo} veh/km/lane for >= {MIN_SECONDS}/{OBSERVATION_S} s ({scope})"))
    storage_secs = _secs(rows, lambda r: r["storage"] >= spec["storage_min"])
    out.append(_gate("storage", storage_secs >= MIN_SECONDS, {"secondsAtLeastMin": storage_secs, "mean": round(statistics.fmean(r["storage"] for r in rows), 3) if rows else None},
                     f"standstill-footprint use >= {spec['storage_min']:.0%} for >= {MIN_SECONDS} s"))
    body_secs = _secs(rows, lambda r: r["body"] >= spec["body_min"])
    out.append(_gate("bodyOccupancy", body_secs >= MIN_SECONDS, {"secondsAtLeastMin": body_secs, "mean": round(statistics.fmean(r["body"] for r in rows), 3) if rows else None},
                     f"spatial body occupancy >= {spec['body_min']:.0%} for >= {MIN_SECONDS} s"))
    p50_inst, p90 = _med([r["p50"] for r in rows]), _med([r["p90"] for r in rows])
    # Amendment (docs/TRAFFIC_REALISM.md): on a signalised grid about half of all vehicles are stopped at a red at any
    # instant, so the instantaneous median speed is ~0 even at 3 veh/km/lane and cannot tell rush hour from free flow.
    # The median gate therefore uses each vehicle's average speed through the scope; the instantaneous median is
    # reported alongside. The declared zone of a gridlock test has too few finished trips, so it uses the same
    # per-vehicle definition over the vehicles inside it.
    if key == "dzRows":
        p50 = m.get("travelP50Declared") if m.get("travelSamplesDeclared", 0) >= 15 else p50_inst
    else:
        p50 = m.get("travelP50") if m.get("travelSamples", 0) >= 15 else p50_inst
    halted = statistics.fmean(r["halted"] for r in rows) if rows else None
    if "p50" in spec:
        a, b = spec["p50"]
        out.append(_gate("medianSpeed", p50 is not None and a <= p50 <= b, {"travel": p50, "instantaneous": p50_inst}, f"{a}-{b} km/h (per-vehicle travel speed)"))
    else:
        out.append(_gate("medianSpeed", p50 is not None and p50 <= spec["p50_max"], {"travel": p50, "instantaneous": p50_inst}, f"<= {spec['p50_max']} km/h (per-vehicle travel speed)"))
    out.append(_gate("p90Speed", p90 is not None and p90 <= spec["p90_max"], p90, f"<= {spec['p90_max']} km/h"))
    if "halted_min" in spec:
        out.append(_gate("haltedShare", halted is not None and halted >= spec["halted_min"], None if halted is None else round(halted, 3), f">= {spec['halted_min']:.0%}"))
    if tier == "rush":
        out.append(_gate("queueApproaches", m["queueApproaches"] >= 2, m["queueApproaches"], ">= 2 approaches with a persisted queue of >= 8 vehicles and >= 50 m"))
        out.append(_gate("stopAndGo", m["stopGoVehicles"] >= 20, m["stopGoVehicles"], ">= 20 distinct motor vehicles with >= 2 stops and >= 2 starts"))
    if tier == "severe":
        out.append(_gate("corridorSpillback", m["corridorSpillSeconds"] >= 120, m["corridorSpillSeconds"],
                         ">= 120 s with two consecutive blocks of one corridor each >= 75% queued AND a green-blocked movement between them"))
        first, last = m["firstMinuteDensity"], m["lastMinuteDensity"]
        out.append(_gate("sustained", first is not None and last is not None and last >= 0.8 * first, {"first60": first, "last60": last}, "final-60 s density >= 80% of first-60 s"))
        out.append(_gate("stopAndGo", m["stopGoVehicles"] >= 20, m["stopGoVehicles"], ">= 20 distinct motor vehicles with >= 2 stops and >= 2 starts"))
    if tier == "gridlock":
        out.append(_gate("blockedCycle", m["cycleSeconds"] >= 120, {"longestRunSeconds": m["cycleSeconds"], "junctions": m["cycleJunctions"], "streets": m["cycleStreets"]},
                         "blocked directed cycle through >= 4 junctions across >= 2 corridors for >= 120 s"))
        out.append(_gate("legalGreensBlocked", m["greenBlockedEvidence"], m["greenBlockedJunctions"], "each cycle junction shows green-blocked movements (signals keep cycling)"))
        ref = m.get("outflowRatio")
        out.append(_gate("componentOutflow", None if ref is None else ref <= 0.10, ref, "outflow across the declared component <= 10% of its matched nonlocked reference"))
        out.append(_gate("rescueDisabled", m["rescueDisabled"], m["timeToTeleport"], "automatic deadlock rescue (teleport) disabled and disclosed for the observation"))
    return out


def safety_gates(m: dict[str, Any], tier: str) -> list[dict[str, Any]]:
    out = [
        _gate("collisions", m["collisionRecords"] == 0, {"records": m["collisionRecords"], "episodes": m["collisionEpisodes"], "uniquePairs": m["collisionPairs"]},
              "zero native vehicle/vehicle and vehicle/person collision records, warm-up included"),
        _gate("routes", m["invalidRoutes"] == 0, m["invalidRoutes"], "zero invalid active routes (sampled every 10 s)"),
        _gate("hiddenLoss", m["ledgerResidual"] == 0, m["ledgerResidual"], "integer demand ledger reconciles (no unexplained vehicles)"),
    ]
    if tier != "gridlock":
        out.append(_gate("teleports", m["teleports"] == 0, m["teleports"], "zero teleports in normal/severe conditions"))
    spec = TIERS.get(tier, TIERS["rush"])
    lo = spec["density"][0] if "density" in spec else spec["density_min"]
    sd = m["startDensity"]
    out.append(_gate("steadyAtStart", sd["minTwoCycles"] is not None and sd["minTwoCycles"] >= lo and sd["atZero"] >= lo, sd,
                     f"density >= {lo} at t=0 and throughout the last two signal cycles of the warm-up"))
    return out


def classify(m: dict[str, Any], requested: str | None) -> dict[str, Any]:
    results: dict[str, list[dict[str, Any]]] = {}
    passed: list[str] = []
    for tier in TIER_ORDER:
        gates = traffic_gates(tier, m)
        results[tier] = gates
        if all(g["ok"] is True for g in gates):
            passed.append(tier)
    achieved = passed[-1] if passed else None
    safety = safety_gates(m, achieved or requested or "rush")
    safe = all(g["ok"] is True for g in safety)
    if achieved is None:
        qualification = "target_unmet"
    elif not safe:
        qualification = "unsafe"
    elif requested is None or requested == achieved:
        qualification = "qualified"
    else:
        qualification = "qualified_other_tier"  # overshoot or undershoot: report the achieved class, never the request
    failing = []
    for tier in ([requested] if requested else TIER_ORDER):
        failing += [f"{tier}.{g['gate']}" for g in results.get(tier, []) if g["ok"] is not True]
    failing += [f"safety.{g['gate']}" for g in safety if g["ok"] is not True]
    return {
        "criteriaVersion": CRITERIA_VERSION, "requestedTier": requested, "achievedTier": achieved, "qualification": qualification,
        "tiers": results, "safety": safety, "failedGates": failing,
        "note": "Achieved class is measured; a slider position, a refused insertion or a screenshot never qualifies a tier.",
    }
