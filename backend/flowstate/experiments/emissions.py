"""Simulated emissions and idling from SUMO's own emission models.

What is measured: SUMO computes CO2, CO, HC, NOx, PMx and fuel mass per vehicle, per time step, from the vehicle's
instantaneous speed and acceleration with the HBEFA3 continuous model that ships with SUMO (``emissionClass``). The
numbers therefore respond to what the controller does to the traffic (stops, accelerations, queues): comparing two
controllers on the same seed compares *how much running, accelerating and idling the signals caused*.

What this is not: an inventory of real urban emissions. The model classes are an assumption about the fleet (below),
the synthetic district is not a real network, and HBEFA is a statistical model of an average vehicle of each class.
Report the *difference* between paired runs; never quote an absolute tonnage as Toronto's.

FlowState's vehicle types carry no ``emissionClass``, so SUMO would apply its default (a gasoline EU4 passenger car) to
*every* vehicle, including trucks, buses and bicycles. ``assign_emission_classes`` sets a class per vehicle type instead.
"""

from __future__ import annotations

from typing import Any

MODEL = "HBEFA3"

# FlowState base type -> HBEFA3 class. A fleet assumption, not Toronto fleet data.
CLASS_BY_TYPE: dict[str, str] = {
    "sedan": "PC_G_EU4", "hatch": "PC_G_EU4", "suv": "PC_G_EU4", "pickup": "PC_G_EU4",
    "van": "LDV_D_EU4", "truck": "HDV_D_EU4", "bus": "Bus",
    "ambulance": "LDV_D_EU4", "police": "PC_G_EU4", "suspect": "PC_G_EU4",
    "bike": "Zero", "wreck": "Zero",
}

# SUMO >= 1.14 reports fuel as mass (mg/s); a volume needs a density assumption, so summaries stay in grams.
GASOLINE_KG_PER_L = 0.74
DIESEL_KG_PER_L = 0.832

EMISSION_GETTERS = {
    "co2": "getCO2Emission", "co": "getCOEmission", "hc": "getHCEmission",
    "nox": "getNOxEmission", "pmx": "getPMxEmission", "fuel": "getFuelConsumption",
}


def emission_class(base_type: str, model: str = MODEL) -> str:
    cls = CLASS_BY_TYPE.get(base_type, "PC_G_EU4")
    return "Zero" if cls == "Zero" else f"{model}/{cls}"


def assign_emission_classes(T: Any, model: str = MODEL) -> dict[str, str]:
    """Give every vehicle type of the running simulation its HBEFA class. Returns ``type id -> class`` as applied."""
    from ..sim.vtypes import base_type

    applied: dict[str, str] = {}
    for tid in T.vehicletype.getIDList():
        cls = emission_class(base_type(tid), model)
        try:
            T.vehicletype.setEmissionClass(tid, cls)
            applied[tid] = cls
        except Exception:  # noqa: BLE001 - a class this SUMO build does not know: leave the type on SUMO's default
            applied[tid] = "default"
    return applied


def grams(mg: float | None) -> float | None:
    return None if mg is None else mg / 1000.0


def fuel_litres(fuel_g: float, diesel_share: float = 0.0) -> float:
    """Volume from mass under an explicit density assumption (a mix of gasoline and diesel)."""
    density = (1 - diesel_share) * GASOLINE_KG_PER_L + diesel_share * DIESEL_KG_PER_L
    return fuel_g / 1000.0 / density


class EmissionAccumulator:
    """Integrates the per-second emission rates sampled from the vehicles in the network."""

    KEYS = ("co2", "co", "hc", "nox", "pmx", "fuel")

    def __init__(self) -> None:
        self.rate = {k: 0.0 for k in self.KEYS}  # mg/s now
        self.idle_rate = {"co2": 0.0, "fuel": 0.0}
        self.idling = 0
        self.total_mg = {k: 0.0 for k in self.KEYS}
        self.idle_mg = {"co2": 0.0, "fuel": 0.0}
        self.idle_vehicle_s = 0.0
        self.distance_m = 0.0
        self.by_edge: dict[str, dict[str, float]] = {}

    def begin(self) -> None:
        self.rate = {k: 0.0 for k in self.KEYS}
        self.idle_rate = {"co2": 0.0, "fuel": 0.0}
        self.idling = 0
        self.by_edge = {}
        self._speed_sum = 0.0

    def add(self, edge: str | None, speed: float, e: dict[str, float], idling: bool) -> None:
        for k in self.KEYS:
            self.rate[k] += e[k]
        self._speed_sum += speed
        if idling:
            self.idling += 1
            self.idle_rate["co2"] += e["co2"]
            self.idle_rate["fuel"] += e["fuel"]
        if edge:
            b = self.by_edge.setdefault(edge, {"co2": 0.0, "fuel": 0.0})
            b["co2"] += e["co2"]
            b["fuel"] += e["fuel"]

    def end(self, dt: float) -> None:
        """Close one sample interval of ``dt`` seconds (rectangle rule; the sample interval is 1 s)."""
        for k in self.KEYS:
            self.total_mg[k] += self.rate[k] * dt
        self.idle_mg["co2"] += self.idle_rate["co2"] * dt
        self.idle_mg["fuel"] += self.idle_rate["fuel"] * dt
        self.idle_vehicle_s += self.idling * dt
        self.distance_m += self._speed_sum * dt
