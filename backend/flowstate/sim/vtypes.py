"""Vehicle and pedestrian types with seeded driver heterogeneity.

Behaviour uses models built into SUMO:

* **Car following — IDM** (Treiber, Hennecke & Helbing 2000). Smooth,
  collision-free acceleration with a desired time headway ``tau``, maximum
  acceleration ``accel``, comfortable deceleration ``decel`` and standstill gap
  ``minGap``. Queues, shockwaves and capacity drops emerge from these.
* **Lane changing — SL2015** (activated by the sublane model). Continuous
  lateral movement, so cyclists keep right and cars pass them within a lane.
* **Desired speed** — SUMO draws a per-vehicle ``speedFactor`` from
  ``normc(mean, dev, min, max)``; drivers do not all target the limit.
* **Emergency vehicles** — ``vClass=emergency`` with SUMO's *bluelight*
  device: surrounding drivers yield and the vehicle may cautiously pass red.
  With its lights off a unit switches to its ``@quiet`` variant, which stops
  at red lights and keeps to the limit.
  ``sim/emergency.py`` adds lane clearing, slowing and junction holds around
  it, and ``sim/signals.py`` pre-empts the signals ahead.

Each run draws ``PROFILES`` driver profiles per class from bounded normal
distributions using the run seed (``Class@k`` type ids), so two drivers of the
same vehicle class react differently while the run stays reproducible.
Dimensions follow North-American fleet averages.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROFILES = 8
QUIET = "@quiet"  # emergency type variant used once a unit's lights are off: obeys red lights like any driver


@dataclass
class TypeDef:
    id: str
    vclass: str
    length: float
    width: float
    height: float
    max_speed: float
    accel: tuple[float, float]  # mean, sd
    decel: tuple[float, float]
    tau: tuple[float, float]
    min_gap: tuple[float, float]
    speed_factor: str
    extra: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    lc: dict[str, tuple[float, float]] = field(default_factory=dict)
    profiles: int = PROFILES
    color: str | None = None


_LC_CAR = {"lcSpeedGain": (1.0, 0.35), "lcCooperative": (0.85, 0.1), "lcAssertive": (1.2, 0.3), "lcPushy": (0.15, 0.1), "lcImpatience": (0.3, 0.2)}
_CAR_EXTRA = {"latAlignment": "center", "minGapLat": "0.5", "maxSpeedLat": "1.0", "lcSublane": "1.0", "jmCrossingGap": "8"}

TYPES: list[TypeDef] = [
    TypeDef("sedan", "passenger", 4.75, 1.84, 1.45, 50, (2.6, 0.35), (4.5, 0.4), (1.2, 0.25), (2.5, 0.5), "normc(1,0.1,0.75,1.25)", _CAR_EXTRA, lc=_LC_CAR),
    TypeDef("hatch", "passenger", 4.15, 1.77, 1.48, 48, (2.6, 0.35), (4.5, 0.4), (1.2, 0.25), (2.4, 0.5), "normc(1,0.1,0.75,1.25)", _CAR_EXTRA, lc=_LC_CAR),
    TypeDef("suv", "passenger", 4.85, 1.95, 1.75, 50, (2.4, 0.3), (4.4, 0.4), (1.25, 0.25), (2.6, 0.5), "normc(1,0.1,0.75,1.25)", _CAR_EXTRA, lc=_LC_CAR),
    TypeDef("pickup", "passenger", 5.6, 2.05, 1.9, 48, (2.3, 0.3), (4.3, 0.4), (1.3, 0.25), (2.7, 0.5), "normc(1,0.1,0.75,1.25)", _CAR_EXTRA, lc=_LC_CAR),
    TypeDef("van", "delivery", 5.4, 2.02, 2.3, 40, (2.0, 0.25), (4.2, 0.35), (1.4, 0.25), (2.8, 0.5), "normc(0.97,0.08,0.75,1.15)", _CAR_EXTRA, lc=_LC_CAR),
    TypeDef("truck", "truck", 8.6, 2.5, 3.5, 30, (1.3, 0.15), (3.8, 0.3), (1.6, 0.25), (3.0, 0.5), "normc(0.94,0.06,0.75,1.1)",
            {"latAlignment": "center", "minGapLat": "0.6", "maxSpeedLat": "0.8", "jmCrossingGap": "10"}, lc={"lcSpeedGain": (0.6, 0.2), "lcCooperative": (0.95, 0.05)}),
    TypeDef("bus", "bus", 12.2, 2.55, 3.2, 25, (1.2, 0.1), (3.6, 0.2), (1.4, 0.15), (3.0, 0.3), "normc(0.95,0.04,0.85,1.05)",
            {"latAlignment": "right", "minGapLat": "0.6", "maxSpeedLat": "0.8", "boardingDuration": "1.8", "jmCrossingGap": "10"}, lc={"lcSpeedGain": (0.3, 0.1)}, profiles=3),
    TypeDef("bike", "bicycle", 1.75, 0.65, 1.7, 8.0, (1.0, 0.2), (3.0, 0.3), (1.0, 0.2), (1.2, 0.3), "normc(0.75,0.12,0.5,1.0)",
            {"latAlignment": "right", "minGapLat": "0.35", "maxSpeedLat": "0.8", "jmCrossingGap": "4"}, lc={"lcSpeedGain": (0.8, 0.3)}),
    TypeDef("ambulance", "emergency", 6.6, 2.3, 2.8, 30, (2.4, 0.0), (5.0, 0.0), (0.9, 0.0), (2.0, 0.0), "normc(1.25,0.02,1.2,1.3)",
            {"latAlignment": "center", "minGapLat": "0.5", "maxSpeedLat": "1.5", "lcStrategic": "2", "lcSpeedGain": "2", "jmDriveAfterRedTime": "300", "jmDriveRedSpeed": "4"},
            params={"has.bluelight.device": "true", "device.bluelight.reactiondist": "70"}, profiles=1),
    TypeDef("police", "emergency", 5.05, 1.95, 1.55, 40, (3.0, 0.0), (5.5, 0.0), (0.8, 0.0), (2.0, 0.0), "normc(1.3,0.02,1.25,1.35)",
            {"latAlignment": "center", "minGapLat": "0.5", "maxSpeedLat": "1.5", "lcStrategic": "2", "lcSpeedGain": "2", "jmDriveAfterRedTime": "300", "jmDriveRedSpeed": "5"},
            params={"has.bluelight.device": "true", "device.bluelight.reactiondist": "60"}, profiles=1),
    TypeDef("suspect", "passenger", 4.75, 1.84, 1.45, 40, (3.2, 0.0), (5.5, 0.0), (0.7, 0.0), (1.6, 0.0), "normc(1.3,0.03,1.2,1.4)",
            {"latAlignment": "center", "minGapLat": "0.4", "maxSpeedLat": "1.6", "lcAssertive": "3", "lcSpeedGain": "3", "lcStrategic": "1.5", "impatience": "1"}, profiles=1),
    TypeDef("wreck", "passenger", 4.75, 1.84, 1.45, 1, (0.5, 0.0), (4.5, 0.0), (1.0, 0.0), (2.5, 0.0), "1", profiles=1),
]
TYPE_BY_ID = {t.id: t for t in TYPES}

TYPE_CODES: dict[str, int] = {
    "sedan": 0, "suv": 1, "truck": 2, "bus": 3, "bike": 4, "ambulance": 5, "police": 6,
    "suspect": 7, "wreck": 8, "hatch": 9, "van": 10, "pickup": 11,
}
EMERGENCY_TYPES = {"ambulance", "police"}
PASSENGER_MIX = {"sedan": 0.40, "suv": 0.28, "hatch": 0.16, "pickup": 0.08, "van": 0.08}
MODE_OF = {"sedan": "car", "suv": "car", "hatch": "car", "pickup": "car", "van": "car", "suspect": "car", "truck": "truck",
           "bus": "bus", "bike": "bike", "ambulance": "emergency", "police": "emergency", "wreck": "incident"}


def base_type(type_id: str) -> str:
    return type_id.split("@", 1)[0]


@dataclass
class Conditions:
    """Multipliers applied to every driver profile (used by the weather scenario)."""

    speed: float = 1.0  # desired speed
    accel: float = 1.0
    decel: float = 1.0
    tau: float = 1.0
    min_gap: float = 1.0
    bike_speed: float = 1.0

    def to_dict(self) -> dict[str, float]:
        return self.__dict__.copy()


def _draw(rng: random.Random, mean: float, sd: float, lo_frac: float = 0.6, hi_frac: float = 1.5) -> float:
    if sd <= 0:
        return mean
    v = rng.gauss(mean, sd)
    return max(mean * lo_frac, min(mean * hi_frac, v))


def _scale_speed_factor(expr: str, k: float) -> str:
    if k == 1.0 or not expr.startswith("normc("):
        return expr
    mean, dev, lo, hi = (float(x) for x in expr[6:-1].split(","))
    return f"normc({mean * k:.3f},{dev * k:.3f},{lo * k:.3f},{hi * k:.3f})"


def build_vtypes(seed: int, cond: Conditions | None = None) -> tuple[str, dict[str, list[str]]]:
    """Return (additional XML, {base type: [profile type ids]})."""
    cond = cond or Conditions()
    rng = random.Random(seed * 1000003 + 17)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<additional>"]
    profiles: dict[str, list[str]] = {}
    for t in TYPES:
        ids = []
        n = t.profiles
        for k in range(n):
            tid = t.id if n == 1 else f"{t.id}@{k}"
            ids.append(tid)
            ped_bike = t.vclass == "bicycle"
            sf = _scale_speed_factor(t.speed_factor, cond.bike_speed if ped_bike else (1.0 if t.vclass == "emergency" else cond.speed))
            attrs = {
                "id": tid, "vClass": t.vclass, "length": f"{t.length}", "width": f"{t.width}", "height": f"{t.height}",
                "maxSpeed": f"{t.max_speed}", "carFollowModel": "IDM", "laneChangeModel": "SL2015",
                "accel": f"{_draw(rng, *t.accel) * cond.accel:.2f}",
                "decel": f"{_draw(rng, *t.decel, 0.75, 1.3) * cond.decel:.2f}",
                "emergencyDecel": f"{max(7.0, t.decel[0] * 2):.1f}",
                "tau": f"{_draw(rng, *t.tau, 0.65, 1.8) * cond.tau:.2f}",
                "minGap": f"{_draw(rng, *t.min_gap, 0.6, 1.6) * cond.min_gap:.2f}",
                "delta": "4", "speedFactor": sf,
            }
            for key, (mean, sd) in t.lc.items():
                attrs[key] = f"{max(0.0, _draw(rng, mean, sd, 0.2, 2.5)):.2f}"
            attrs.update({k: v for k, v in t.extra.items() if k not in attrs})
            if t.color:
                attrs["color"] = t.color
            body = " ".join(f'{k}="{v}"' for k, v in attrs.items())
            if t.params:
                inner = "".join(f'<param key="{k}" value="{v}"/>' for k, v in t.params.items())
                lines.append(f"  <vType {body}>{inner}</vType>")
            else:
                lines.append(f"  <vType {body}/>")
            if t.vclass == "emergency":
                quiet = {k: v for k, v in attrs.items() if k not in ("jmDriveAfterRedTime", "jmDriveRedSpeed")}
                quiet.update({"id": f"{tid}{QUIET}", "speedFactor": "normc(1,0.05,0.9,1.1)"})
                lines.append("  <vType " + " ".join(f'{k}="{v}"' for k, v in quiet.items()) + "/>")
        profiles[t.id] = ids
    ped_sf = _scale_speed_factor("normc(1,0.15,0.6,1.4)", min(1.0, 0.5 + cond.speed / 2))
    lines.append(f'  <vType id="walker" vClass="pedestrian" length="0.3" width="0.55" maxSpeed="1.6" speedFactor="{ped_sf}"/>')
    lines.append("</additional>")
    return "\n".join(lines) + "\n", profiles


def write_vtypes(path: Path, seed: int, cond: Conditions | None = None) -> dict[str, list[str]]:
    xml, profiles = build_vtypes(seed, cond)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return profiles


def type_summary() -> list[dict[str, Any]]:
    return [{"id": t.id, "vclass": t.vclass, "length": t.length, "width": t.width, "code": TYPE_CODES[t.id]} for t in TYPES]
