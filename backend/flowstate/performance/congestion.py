"""Versioned, provisional congestion controls expressed as existing SUMO inputs.

These are modeled district-wide trip requests, NOT Toronto intersection counts
or guarantees of achieved congestion. Scenario components retain ownership of OD
patterns, fleet composition and time profiles.
"""
from copy import deepcopy

VERSION = 2
PRESETS = {
    "light": ("Light", 600, 1.0, False),
    "normal": ("Normal", 1600, 1.0, False),
    "rush_hour": ("Rush Hour", 2800, 1.0, False),
    "severe": ("Severe Congestion", 3200, 1.6, True),
    "gridlock_stress": ("Gridlock Stress Test", 3200, 1.6, True),
}


# Engine-level realism options of the three jam presets (docs/TRAFFIC_REALISM.md). They are what the experiments in
# reports/ measured, not tuning targets: the district is held at a declared accumulation (the surplus waits in the
# demand generator's counted external queue, never discarded), runs the protected-left "safe" signal program with the
# keep-the-box-clear rule, and "severe"/"gridlock_stress" add a downstream restriction (one exit lane, 10 km/h).
_ACCUMULATION = {"ADMISSION": "accumulation", "GATE_FRACTION": 99.0, "GATE_BY_CLASS": None, "PENDING_PER_GATEWAY": 0, "MAX_PENDING": 10**6,
                 "MAX_PENDING_TOTAL": 10**6, "MIN_THROTTLE": 1.0, "THROTTLE_SLOPE": 0.0, "PATH_GATE": None, "MAX_HOLD_S": 1800.0, "PENDING_PER_ORIGIN": 2}
_GUARD = {"margin": 2.0, "window": 60.0}
_BOTTLENECK = {"lanes": 1, "speed_kmh": 10}
ENGINE_OPTIONS = {
    # Inbound (AM) commuter peak. The accumulation saturates near 450 vehicles: gateway insertion is the limit, not the target.
    "rush_hour": {"params": {"demand": 3200, "traffic": 1.6, "peak": 2.5, "period": "am"},
                  "signal_program": "safe", "box_guard": _GUARD, "outlets": {}, "demand_overrides": {**_ACCUMULATION, "TARGET_ACTIVE": 600},
                  "sumo_options": {"--max-depart-delay": "-1"}},
    # Same inbound peak plus drivers circling for parking (80 % of district-bound trips pass 2-5 more blocks first): more interior load, same entry flow.
    "severe": {"params": {"demand": 2400, "traffic": 1.6, "peak": 2.5, "period": "am"},
               "signal_program": "safe", "box_guard": _GUARD, "outlets": {},
               "demand_overrides": {**_ACCUMULATION, "TARGET_ACTIVE": 800, "CRUISE_SHARE": 0.8, "CRUISE_LEGS": [2, 5]},
               "sumo_options": {"--max-depart-delay": "-1"}},
    # Outbound (PM) peak against a downstream restriction (one exit lane, 10 km/h), held at the district's measured storage ceiling.
    # SUMO's deadlock rescue (teleport) is off for the gridlock test and every report says so; the 'relieve' control restores the
    # exit roads and meters the accumulation down so the jam discharges.
    "gridlock_stress": {"params": {"demand": 3200, "traffic": 1.6, "peak": 2.5, "period": "pm"},
                        "signal_program": "safe", "box_guard": _GUARD, "outlets": dict(_BOTTLENECK), "demand_overrides": {**_ACCUMULATION, "TARGET_ACTIVE": 1400},
                        "sumo_options": {"--max-depart-delay": "-1", "--time-to-teleport": "-1"}},
}


# Warm-up the district needs to reach each preset's accumulation (a prepared run hides it; see performance/prepared.py).
WARMUP = {"rush_hour": 600, "severe": 600, "gridlock_stress": 1000}


def engine_options(preset: str | None) -> dict:
    """RunConfig keyword arguments of a congestion preset ({} for presets with no engine-level options)."""
    return {k: deepcopy(v) for k, v in ENGINE_OPTIONS.get(preset or "", {}).items() if k != "params"}


def engine_params(preset: str | None) -> dict:
    """Scenario parameters (demand, period...) the preset's engine options were measured with ({} when it has none)."""
    return deepcopy(ENGINE_OPTIONS.get(preset or "", {}).get("params", {}))


def list_presets():
    return [{"id": key, "label": label, "version": VERSION,
             "params": {"duration": 5, "demand": demand, "traffic": traffic}, "warmup": WARMUP.get(key, 180),
             "rushHourPeak": 3.0 if key == "gridlock_stress" else 2.5 if key == "severe" else 2.0 if key == "rush_hour" else 1.2,
             "stressTest": stress, "engine": key in ENGINE_OPTIONS, "calibrated": False, "safetyValidated": False,
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
            defaults.update(engine_params(preset))  # the demand direction the engine options were measured with
    resolved = {**defaults, **deepcopy(params)}
    return resolved, (selected["warmup"] if selected else 90.0), selected
