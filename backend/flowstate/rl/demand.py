"""Seeded, controller-independent demand for the RL network.

The demand file is a pure function of (scenario, seed): every controller sees *exactly* the same vehicles
departing at exactly the same times. Controllers cannot change how many vehicles want to travel, only how
well the signals serve them, so a policy cannot look better by keeping vehicles out of the simulation.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from pathlib import Path

from . import config as C
from .network import GridInfo

TRUCK_SHARE = 0.05
EMERGENCY_SHARE = 0.008


# Standard demand: a busy peak (SUMO measures about 55-60 s mean waiting under fixed-time control). Intensity 1.18 is the
# over-saturated stress level (about 100 s), 0.7 is light traffic.
BASE_VPH = 440.0
STRESS_INTENSITY = 1.18


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    base_vph: float  # per entry, veh/h, before the profile
    horizon_s: float = 900.0


SCENARIOS: dict[str, Scenario] = {
    "balanced": Scenario("balanced", "Balanced", "Equal demand on all eight entries.", BASE_VPH),
    "ns_heavy": Scenario("ns_heavy", "North-south heavy", "North and south entries carry about three times the east-west demand.", BASE_VPH),
    "ew_heavy": Scenario("ew_heavy", "East-west heavy", "East and west entries carry about three times the north-south demand.", BASE_VPH),
    "pulse": Scenario("pulse", "Alternating pulses", "The dominant direction flips every 240 s.", BASE_VPH),
    "rush_ramp": Scenario("rush_ramp", "Rush-hour ramp (held out)", "All entries ramp up to 1.5x, hold and ease. Never used for training.", BASE_VPH),
}
TRAIN_SCENARIOS = ("balanced", "ns_heavy", "ew_heavy", "pulse")
HELD_OUT_SCENARIOS = ("rush_ramp",)


def _side(edge: str) -> str:
    """Compass side an entry edge sits on (``left0A0`` -> W, ``top0A1`` -> N, ...)."""
    return {"left": "W", "right": "E", "top": "N", "bottom": "S"}[next(k for k in ("left", "right", "top", "bottom") if edge.startswith(k))]


def rate_multiplier(scenario: str, side: str, t: float, horizon: float) -> float:
    ns = side in ("N", "S")
    if scenario == "balanced":
        return 1.0
    if scenario == "ns_heavy":
        return 1.45 if ns else 0.5
    if scenario == "ew_heavy":
        return 0.5 if ns else 1.45
    if scenario == "pulse":
        first_half = int(t // 240) % 2 == 0
        return 1.5 if (ns == first_half) else 0.45
    if scenario == "rush_ramp":
        x = t / horizon
        shape = 0.45 + 1.05 * math.sin(math.pi * min(1.0, max(0.0, x))) ** 1.3
        return shape
    raise KeyError(scenario)


# tau is never below the 1 s simulation step: a Krauss follower with tau < step length cannot brake in time
VTYPES = """<additional>
  <vType id="car" vClass="passenger" length="4.5" minGap="2.5" accel="2.6" decel="4.5" tau="1.0" sigma="0.5" speedFactor="normc(1.0,0.1,0.8,1.2)"/>
  <vType id="truck" vClass="truck" length="8.0" minGap="2.5" accel="1.3" decel="3.8" tau="1.2" sigma="0.5" maxSpeed="11.1" speedFactor="normc(0.95,0.05,0.8,1.05)"/>
  <vType id="emergency" vClass="emergency" length="6.0" minGap="2.0" accel="3.0" decel="5.0" tau="1.0" sigma="0.2" speedFactor="1.2"/>
</additional>
"""


def intensity_multiplier(seed: int) -> float:
    """Day-to-day variation of the whole demand level (+/- 15 %), drawn from the seed."""
    return random.Random(f"intensity-{seed}").uniform(0.85, 1.15)


def write_routes(path: Path, grid: GridInfo, net_path: Path, scenario: str, seed: int, horizon_s: float | None = None,
                 intensity: float = 1.0) -> dict:
    """Write the route file; returns a manifest (counts and a content hash used to prove identical demand)."""
    import sumolib

    sc = SCENARIOS[scenario]
    horizon = horizon_s or sc.horizon_s
    net = sumolib.net.readNet(str(net_path), withInternal=False)
    rng = random.Random(f"{scenario}-{seed}")
    mult = intensity * intensity_multiplier(seed)
    routes: dict[tuple[str, str], list[str] | None] = {}

    def route(o: str, d: str) -> list[str] | None:
        if (o, d) not in routes:
            r, _ = net.getShortestPath(net.getEdge(o), net.getEdge(d))
            routes[(o, d)] = [e.getID() for e in r] if r else None
        return routes[(o, d)]

    vehicles: list[tuple[float, str, str, list[str]]] = []
    for o in grid.entry_edges:
        side = _side(o)
        t = rng.expovariate(1.0 / 3.0)
        dests = [d for d in grid.exit_edges if route(o, d) and len(route(o, d)) >= 3]
        while t < horizon:
            rate = sc.base_vph * mult * rate_multiplier(scenario, side, t, horizon) / 3600.0
            if rate <= 0:
                t += 1.0
                continue
            vehicles.append((t, o, rng.choice(dests), []))
            t += rng.expovariate(rate)
    vehicles.sort(key=lambda v: (v[0], v[1]))
    lines = ['<routes>', VTYPES.replace("<additional>", "").replace("</additional>", "").strip("\n")]
    manifest = {"scenario": scenario, "seed": seed, "vehicles": 0, "emergency": 0, "intensity": round(mult, 4)}
    sched: dict[str, float] = {}
    emergency_ids: list[str] = []
    for k, (t, o, d, _) in enumerate(vehicles):
        u = rng.random()
        vt = "emergency" if u < EMERGENCY_SHARE else "truck" if u < EMERGENCY_SHARE + TRUCK_SHARE else "car"
        vid = f"v{k}"
        sched[vid] = round(t, 2)
        manifest["vehicles"] += 1
        manifest["emergency"] += vt == "emergency"
        if vt == "emergency":
            emergency_ids.append(vid)
        lines.append(f'  <vehicle id="{vid}" type="{vt}" depart="{t:.2f}" departLane="best" departSpeed="max">'
                     f'<route edges="{" ".join(route(o, d))}"/></vehicle>')
    lines.append("</routes>")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    manifest["sha256"] = hashlib.sha256(text.encode()).hexdigest()[:16]
    manifest["scheduled"] = sched
    manifest["emergency_ids"] = emergency_ids
    return manifest
