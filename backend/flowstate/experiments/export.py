"""JSON documents for the dashboard and for learning agents (the data contract).

Every document carries ``schemaVersion`` and is built *from stored measurements*, so a number on a chart can always be
traced to a row in ``kpi_samples`` / ``edge_samples`` / ... and from there to a SUMO run identified by
(scenario, parameters, seed). Series are columnar (``{"t": [...], "co2_mg_s": [...]}``) so a chart can use them directly.
"""

from __future__ import annotations

from typing import Any

from .store import ExperimentStore, KPI_METRICS, SUMMARY_METRICS

SCHEMA_VERSION = 1

# Units and meaning of every number, shipped with the data so a UI never has to guess.
UNITS = {
    "sim_t": "s since the start of the observation (warm-up excluded)",
    "mean_speed_kmh": "km/h", "density_veh_km_lane": "vehicles per km of general-traffic lane", "storage_occupancy": "share of jam storage (7.5 m per vehicle-lane)",
    "co2_mg_s": "mg/s (simulated, HBEFA3)", "co_mg_s": "mg/s", "hc_mg_s": "mg/s", "nox_mg_s": "mg/s", "pmx_mg_s": "mg/s", "fuel_mg_s": "mg/s of fuel mass",
    "idle_co2_mg_s": "mg/s emitted by stationary vehicles not at a planned stop", "throughput_per_h": "completed trips per hour",
    "co2_total_g": "g over the observation window", "co2_g_per_km": "g per vehicle-km driven", "idle_s_total": "vehicle-seconds stationary (engine on)",
}
EMISSIONS_NOTE = ("Simulated estimates from SUMO's HBEFA3 model with an assumed fleet (see flowstate.experiments.emissions); "
                  "not measurements of real urban emissions. Compare paired runs, do not quote absolute totals.")


def _columnar(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {"t": [r["sim_t"] for r in rows]}
    for k in keys:
        out[k] = [r.get(k) for r in rows]
    return out


def export_experiment(store: ExperimentStore, experiment_id: str, *, kpi_step_s: float | None = None, heat_bucket_s: float = 30.0,
                      max_events: int = 400, heat_top_edges: int | None = 60) -> dict[str, Any]:
    exp = store.get_experiment(experiment_id)
    if exp is None:
        raise KeyError(experiment_id)
    summary = exp.pop("summary", None) or {}
    series = store.kpi_series(experiment_id, KPI_METRICS, step_s=kpi_step_s)
    edges = store.edge_heatmap(experiment_id, "halting", heat_bucket_s, top=heat_top_edges)
    # also the per-cell emission field, for the emissions heatmap (same cells, different metric)
    co2 = {(r["edge_id"], r["t0"]): r["value"] for r in store.edge_heatmap(experiment_id, "co2_mg_s", heat_bucket_s, top=heat_top_edges)}
    occ = {(r["edge_id"], r["t0"]): r["value"] for r in store.edge_heatmap(experiment_id, "occupancy", heat_bucket_s, top=heat_top_edges)}
    cells = [{"edge": r["edge_id"], "t0": r["t0"], "halting": r["value"], "haltingPeak": r["peak"], "occupancy": occ.get((r["edge_id"], r["t0"])),
              "co2_mg_s": co2.get((r["edge_id"], r["t0"]))} for r in edges]
    junctions = [{"tls": r["tls_id"], "t0": r["t0"], "queue": r["value"], "queuePeak": r["peak"], "crossings": r["crossings"]} for r in store.junction_heatmap(experiment_id, heat_bucket_s)]
    events = store.signal_events(experiment_id, limit=max_events * 5)
    interesting = [e for e in events if e["kind"] != "phase"] + [e for e in events if e["kind"] == "phase"]
    safety = store.safety_events(experiment_id, limit=max_events)
    extra = summary.get("extra") or {}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "experiment": {k: exp.get(k) for k in ("id", "name", "scenario", "controller", "network", "seed", "params", "params_hash", "warmup_s",
                                                "duration_s", "started_at", "finished_at", "status", "suite", "tags", "baseline_id", "engine", "emission_model", "notes", "error")},
        "summary": {k: summary.get(k) for k in SUMMARY_METRICS},
        "extra": extra,
        "kpi": _columnar(series, KPI_METRICS),
        "heatmap": {"bucketS": heat_bucket_s, "edges": cells, "junctions": junctions,
                    "note": "edge cells are averages over the bucket of SUMO's per-edge halting count; join `edge` to the network geometry"},
        "signals": {"decisionStats": store.signal_decision_stats(experiment_id), "events": interesting[:max_events],
                    "note": "kind=phase: a phase change with how long the previous phase ran; preempt/tsp: priority actions; policy: a controller's own decisions"},
        "safety": {"counts": store.safety_counts(experiment_id), "events": safety},
        "emissions": {"note": EMISSIONS_NOTE, "model": exp.get("emission_model"), "totals_g": {"co2": summary.get("co2_total_g"), "fuel": summary.get("fuel_total_g"),
                      **(extra.get("otherTotalsG") or {})}, "co2_g_per_km": summary.get("co2_g_per_km"), "idle": {"seconds": summary.get("idle_s_total"),
                      "co2_g": summary.get("idle_co2_g"), "fuel_g": summary.get("idle_fuel_g"), "share_of_co2": summary.get("idle_share_of_co2")}},
        "units": UNITS,
    }


def export_comparison(store: ExperimentStore, baseline_id: str, candidate_id: str, *, kpi_step_s: float | None = 5.0) -> dict[str, Any]:
    """Before-versus-after document for two experiments: summary deltas, aligned KPI series and a per-edge congestion difference."""
    cmp_ = store.compare_experiments(baseline_id, candidate_id)
    metrics = ["active_vehicles", "halted_vehicles", "mean_speed_kmh", "queue_total", "queue_max", "co2_mg_s", "fuel_mg_s", "idle_co2_mg_s", "throughput_per_h", "mean_travel_s"]
    a = store.kpi_series(baseline_id, metrics, step_s=kpi_step_s)
    b = store.kpi_series(candidate_id, metrics, step_s=kpi_step_s)
    ea = {(r["edge_id"], r["t0"]): r["value"] for r in store.edge_heatmap(baseline_id, "halting", 60.0)}
    eb = {(r["edge_id"], r["t0"]): r["value"] for r in store.edge_heatmap(candidate_id, "halting", 60.0)}
    diff = [{"edge": k[0], "t0": k[1], "baseline": ea.get(k, 0.0), "candidate": eb.get(k, 0.0), "delta": eb.get(k, 0.0) - ea.get(k, 0.0)} for k in sorted(set(ea) | set(eb), key=lambda k: (k[1], k[0]))]
    return {"schemaVersion": SCHEMA_VERSION, **cmp_, "kpi": {"baseline": _columnar(a, metrics), "candidate": _columnar(b, metrics)},
            "haltingDifference": {"bucketS": 60.0, "cells": diff, "note": "negative delta = fewer halted vehicles with the candidate controller"},
            "emissionsNote": EMISSIONS_NOTE, "units": UNITS}
