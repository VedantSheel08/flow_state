"""Versioned, provisional congestion controls expressed as existing SUMO inputs.

These are modeled district-wide trip requests, NOT Toronto intersection counts
or guarantees of achieved congestion. Scenario components retain ownership of OD
patterns, fleet composition and time profiles.
"""
from copy import deepcopy

VERSION = 1
PRESETS = {
    "light": ("Light", 600, 1.0, False),
    "normal": ("Normal", 1600, 1.0, False),
    "rush_hour": ("Rush Hour", 2800, 1.0, False),
    "severe": ("Severe Congestion", 3200, 1.6, True),
    "gridlock_stress": ("Gridlock Stress Test", 3200, 1.6, True),
}


def list_presets():
    return [{"id": key, "label": label, "version": VERSION,
             "params": {"duration": 5, "demand": demand, "traffic": traffic}, "warmup": 180,
             "rushHourPeak": 3.0 if key == "gridlock_stress" else 2.5 if key == "severe" else 2.0 if key == "rush_hour" else 1.2,
             "stressTest": stress, "calibrated": False, "safetyValidated": False,
             "description": "Modeled load; actual departures, queues and throughput must be measured. Dense baseline runs have known collision findings."}
            for key, (label, demand, traffic, stress) in PRESETS.items()]


def resolve(params: dict, preset: str | None, scenario: str):
    selected = None
    defaults = {"duration": 5}
    if preset is not None:
        selected = next((p for p in list_presets() if p["id"] == preset), None)
        if selected is None:
            raise ValueError(f"unknown congestion preset '{preset}'")
        defaults.update(selected["params"])
        if scenario == "rush_hour":
            defaults["peak"] = selected["rushHourPeak"]
    resolved = {**defaults, **deepcopy(params)}
    return resolved, (selected["warmup"] if selected else 90.0), selected
