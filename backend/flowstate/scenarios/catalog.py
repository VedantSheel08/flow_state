"""Scenario definitions: parameters + recipe (demand level and components)."""

from __future__ import annotations

import random
from typing import Any, Callable

from .base import ParamSpec, Recipe, Scenario, ScenarioSpec
from .components import (
    WEATHER,
    Accident,
    Construction,
    CorridorMonitor,
    DemandSpike,
    EmergencyDispatch,
    Pursuit,
    RushHour,
    SchoolDismissal,
    Transit,
    VenueSurge,
    Weather,
)

S = "Simulation"


def opt(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"value": v, "label": l} for v, l in pairs]


def common(demand: int = 1500, duration: int = 10, bikes: int = 5, preemption: bool | None = None) -> list[ParamSpec]:
    out = [
        ParamSpec(key="duration", label="Run length", type="select", default=duration, group=S,
                  options=[{"value": v, "label": f"{v} min"} for v in (5, 10, 15, 30, 60)], description="Simulated time after a 90 s warm-up."),
        ParamSpec(key="demand", label="Base demand", type="int", default=demand, min=400, max=3200, step=100, unit="veh/h", group=S,
                  description="Background trips per hour entering or starting in the district."),
        ParamSpec(key="traffic", label="Traffic level", type="float", default=1.0, min=0.4, max=1.6, step=0.05, unit="×", live=True, group=S,
                  description="Live multiplier on all background demand."),
        ParamSpec(key="bike_share", label="Cyclists", type="int", default=bikes, min=0, max=30, step=1, unit="%", group=S),
        ParamSpec(key="pedestrians", label="Pedestrians", type="bool", default=True, group=S, description="Walkers on sidewalks and crosswalks."),
        ParamSpec(key="signals", label="Signal control", type="select", default="fixed", group=S,
                  options=opt(("fixed", "Fixed-time"), ("actuated", "Actuated"))),
    ]
    if preemption is not None:
        out.append(ParamSpec(key="preemption", label="Emergency pre-emption", type="bool", default=preemption, group=S,
                             description="Hold or advance signals for approaching emergency vehicles (never skips amber, all-red or walk clearance)."))
        out.append(ParamSpec(key="yield_to_ev", label="Drivers yield to sirens", type="bool", default=True, group=S,
                             description="Drivers near a unit with its lights on move out of its lane, slow down beside it and hold back at the junction it is entering."))
    return out


def loc(label: str = "Location") -> ParamSpec:
    return ParamSpec(key="location", label=label, type="edge", default="random", group="Scenario",
                     description="A mid-block street segment, or random. Pick one on the map.")


Build = Callable[[Scenario], Recipe]
CATALOG: dict[str, tuple[ScenarioSpec, Build]] = {}


def scenario(spec: ScenarioSpec):
    def deco(fn: Build) -> Build:
        CATALOG[spec.id] = (spec, fn)
        return fn

    return deco


@scenario(ScenarioSpec(
    id="rush_hour", title="Rush Hour", category="Demand", tagline="Directional commuter peak",
    description="A commuter peak builds, holds and eases. Peak intensity, timing and the origin–destination pattern are drawn from the seed; queues form where demand exceeds signal capacity.",
    params=[*common(1600, 15),
            ParamSpec(key="period", label="Period", type="select", default="am", options=opt(("am", "Morning (inbound)"), ("pm", "Evening (outbound)"))),
            ParamSpec(key="peak", label="Peak intensity", type="float", default=2.0, min=1.2, max=3.0, step=0.1, unit="×"),
            ParamSpec(key="variability", label="Variability", type="float", default=0.5, min=0.0, max=1.0, step=0.1, description="Spread of the random draws (0 = nominal).")],
    measures=["Mean delay and travel time", "Queues at signals", "Throughput"],
    assumptions=["Demand is a time-varying OD model, not observed counts.", "Downtown blocks attract morning trips; the evening reverses the pattern."],
))
def _rush(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [RushHour(p["period"], p["peak"], p["variability"])])


@scenario(ScenarioSpec(
    id="accident", title="Road Accident", category="Incident", tagline="Collision blocks lanes, responders attend",
    description="A collision at a random valid location blocks one or more lanes with crashed vehicles. Police (and paramedics if serious) are dispatched from their stations; aware drivers re-route; the scene clears after a drawn clearance time.",
    params=[*common(1700, 15, preemption=True), loc("Collision location"),
            ParamSpec(key="severity", label="Severity", type="select", default="random",
                      options=opt(("random", "Random"), ("minor", "Minor · 1 lane"), ("moderate", "Moderate · most lanes"), ("severe", "Severe · closed")))],
    measures=["Queue behind the scene", "Detours", "Responder travel"],
    assumptions=["Crashed vehicles physically occupy the lanes.", "60 % of drivers headed for a closed street re-plan (navigation/signage); 35 % of all vehicles carry SUMO's periodic re-routing device."],
))
def _accident(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [Accident(p["location"], p["severity"])])


@scenario(ScenarioSpec(
    id="ambulance", title="Ambulance Dispatch", category="Emergency", tagline="Calls from St. Mary's Hospital",
    description="Emergency calls arrive at random times and places. Each ambulance drives a real SUMO route from the hospital to the scene, stays on scene, then returns. Response time is measured against the free-flow travel time.",
    params=[*common(1600, 10, preemption=True),
            ParamSpec(key="calls", label="Calls", type="int", default=2, min=1, max=4, step=1)],
    measures=["Response time vs. free-flow", "Signal pre-emptions"],
    assumptions=["Ambulances use SUMO's bluelight device and red lights may be passed slowly; nearby drivers clear its lane, slow beside it and hold at the junction it enters.", "Free-flow time assumes 125 % of the posted limit."],
))
def _ambulance(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [EmergencyDispatch(int(p["calls"]))])


@scenario(ScenarioSpec(
    id="pursuit", title="Police Pursuit", category="Emergency", tagline="Evading vehicle, converging units",
    description="A stolen vehicle flees, changing destination every 20–35 s. Police units pursue its live position through the street network. Contact for 6 s stops the suspect; long pursuits are called off.",
    params=[*common(1400, 10, preemption=True),
            ParamSpec(key="units", label="Police units", type="int", default=2, min=1, max=4, step=1),
            ParamSpec(key="max_minutes", label="Call off after", type="int", default=6, min=2, max=20, step=1, unit="min")],
    measures=["Time to stop", "Closest approach", "Pre-emptions"],
    assumptions=["Simplified pursuit model: the suspect obeys lane rules but drives aggressively; units follow its current or next street."],
))
def _pursuit(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [Pursuit(int(p["units"]), float(p["max_minutes"]))])


@scenario(ScenarioSpec(
    id="bike_lanes", title="Bike Lane Conversion", category="Infrastructure", tagline="King Street road diet",
    description="Compare King Street as built (two general lanes, planted median and left-turn bays each way) with a reallocation that removes the median and bays for a 2 m protected bike lane in each direction. Left turns then share the inside lane. Cyclists route on the actual bicycle lanes.",
    networks=["district", "district_bike"],
    params=[*common(1500, 10, bikes=14),
            ParamSpec(key="network", label="King Street", type="select", default="district_bike", options=opt(("district", "As built"), ("district_bike", "Protected bike lanes")))],
    measures=["Cyclist speed and trips", "Car travel time", "King St corridor speed"],
    assumptions=["Bicycles may only use lanes that permit them; on the conversion they use the separated lane."],
))
def _bikes(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [CorridorMonitor("king")])


@scenario(ScenarioSpec(
    id="construction", title="Construction", category="Incident", tagline="Lane or full closure with work zone",
    description="A work zone is in place from the start: either a single lane closure (merge) or a full closure with detour, with a reduced speed limit through the zone.",
    params=[*common(1500, 10), loc("Work zone"),
            ParamSpec(key="closure", label="Closure", type="select", default="random", options=opt(("random", "Random"), ("lane", "One lane"), ("full", "Full closure"))),
            ParamSpec(key="limit", label="Work-zone limit", type="int", default=30, min=20, max=50, step=5, unit="km/h")],
    measures=["Queue at the merge", "Detour travel"],
))
def _construction(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [Construction(p["location"], p["closure"], float(p["limit"]))])


@scenario(ScenarioSpec(
    id="stadium", title="Stadium Event", category="Demand", tagline="Arrival or departure surge",
    description="A concentrated surge of vehicles and pedestrians to or from Riverside Stadium. Attendance and timing vary with the seed.",
    params=[*common(1200, 15),
            ParamSpec(key="phase", label="Phase", type="select", default="departure", options=opt(("arrival", "Arrivals (before the game)"), ("departure", "Departures (after the game)"))),
            ParamSpec(key="attendance", label="Event vehicles", type="int", default=500, min=100, max=1200, step=50, unit="veh")],
    measures=["Queues near the venue", "Network delay"],
))
def _stadium(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [VenueSurge("stadium", p["phase"], int(p["attendance"]))])


@scenario(ScenarioSpec(
    id="school", title="School Dismissal", category="Demand", tagline="Pick-up queues and a school zone",
    description="At the dismissal bell students walk out over the crosswalks while parents' vehicles stop at the curb to pick up. A reduced school-zone limit applies on the frontage streets.",
    params=[*common(1100, 10),
            ParamSpec(key="pickups", label="Pick-up vehicles", type="int", default=60, min=10, max=150, step=5),
            ParamSpec(key="zone_kmh", label="School-zone limit", type="int", default=30, min=20, max=40, step=5, unit="km/h")],
    measures=["Frontage queues", "Pedestrian volumes"],
    assumptions=["Pick-up stops are made in the curb lane for 25–90 s."],
))
def _school(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [SchoolDismissal(int(p["pickups"]), float(p["zone_kmh"]))])


@scenario(ScenarioSpec(
    id="transit", title="Transit Priority", category="Transit", tagline="Scheduled buses with signal priority",
    description="Bus lines 501 Queen and 6 Main run on schedules with stops and dwell times. Transit signal priority extends greens for approaching buses; compare runs with it on and off at the same seed.",
    params=[*common(1500, 15),
            ParamSpec(key="headway", label="Headway", type="int", default=5, min=3, max=15, step=1, unit="min"),
            ParamSpec(key="lines", label="Lines", type="select", default="all", options=opt(("all", "501 Queen + 6 Main"), ("501", "501 Queen"), ("6", "6 Main"))),
            ParamSpec(key="tsp", label="Transit signal priority", type="bool", default=True, description="Green extension of up to 15 s for buses within 90 m.")],
    measures=["Bus travel time and schedule delay", "Green extensions granted", "General traffic delay"],
    assumptions=["Dwell time = 6 s + 2.4 s per boarding; boardings are drawn per stop."],
))
def _transit(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [Transit(float(p["headway"]), p["lines"])])


@scenario(ScenarioSpec(
    id="weather", title="Severe Weather", category="Environment", tagline="Rain, snow or fog",
    description="Poor weather modifies every driver profile: lower desired speed, gentler acceleration and braking, longer time headways and gaps; fewer people cycle or walk.",
    params=[*common(1500, 10),
            ParamSpec(key="condition", label="Condition", type="select", default="random",
                      options=opt(("random", "Random"), *[(k, v["label"]) for k, v in WEATHER.items()]))],
    measures=["Mean speed", "Delay vs. clear weather (compare at the same seed)"],
    assumptions=["Simplified: factors fall within ranges reported in road-weather literature (e.g. FHWA: arterial speeds −10 to −25 % in snow, saturation flow −10 to −20 %); not calibrated for a specific city."],
))
def _weather(sc: Scenario) -> Recipe:
    p = sc.params
    return Recipe(p["demand"], [Weather(p["condition"])])


@scenario(ScenarioSpec(
    id="disruption", title="Random Urban Disruption", category="Mixed", tagline="Seeded mix of incidents and surges",
    description="Two or three disruptions drawn from collisions, work zones, ambulance calls and demand spikes, on distinct streets so they remain valid together.",
    params=[*common(1600, 15, preemption=True),
            ParamSpec(key="intensity", label="Disruptions", type="int", default=3, min=2, max=4, step=1)],
    measures=["Network delay", "Queues", "Emergency response"],
))
def _disruption(sc: Scenario) -> Recipe:
    p = sc.params
    rng = random.Random(sc.seed * 7 + 3)
    pool = ["accident", "construction", "ems", "spike", "accident2"]
    picks = rng.sample(pool, int(p["intensity"]))
    comps = []
    for k in picks:
        if k.startswith("accident"):
            comps.append(Accident(window=(0.1, 0.6)))
        elif k == "construction":
            comps.append(Construction(at_start=rng.random() < 0.5))
        elif k == "ems":
            comps.append(EmergencyDispatch(1, window=(0.15, 0.6)))
        else:
            comps.append(DemandSpike())
    return Recipe(p["demand"], comps)


ORDER = ["rush_hour", "accident", "ambulance", "pursuit", "bike_lanes", "construction", "stadium", "school", "transit", "weather", "disruption"]


def list_specs() -> list[dict[str, Any]]:
    return [CATALOG[k][0].model_dump() for k in ORDER if k in CATALOG]


def create(scenario_id: str, params: dict[str, Any] | None = None) -> Scenario:
    if scenario_id not in CATALOG:
        raise KeyError(f"unknown scenario '{scenario_id}'")
    spec, build = CATALOG[scenario_id]
    return Scenario(spec, build, params or {})
