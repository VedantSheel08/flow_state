"""Demo scenario specifications and their seeded demand generator for the lab's 2x2 network.

This module is pure data plus a route writer and imports nothing from ``flowstate.rl`` at import time (``rl.demand`` registers
these scenarios at its own import, so a top-level import here would be circular).

A scenario is a set of *flows* (entry edge, destination weights, piecewise-linear demand profile in veh/h) and optional
*incidents* (a real vehicle that stops in a lane for a fixed time). Everything that varies the traffic lives in the route file,
which is a pure function of (scenario, seed, intensity): every controller sees the same vehicles at the same times, and the
incident vehicles are part of that file, so they appear at the same time whatever the controller does.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --- 2x2 network geometry (see flowstate.rl.network): A0 SW, B0 SE, A1 NW, B1 NE; 179-200 m two-lane links ---------------------
ENTRIES = {"W": ("left0A0", "left1A1"), "E": ("right0B0", "right1B1"), "S": ("bottom0A0", "bottom1B0"), "N": ("top0A1", "top1B1")}
EXITS = {"W": ("A0left0", "A1left1"), "E": ("B0right0", "B1right1"), "S": ("A0bottom0", "B0bottom1"), "N": ("A1top0", "B1top1")}
ALL_ENTRIES = tuple(e for v in ENTRIES.values() for e in v)
ALL_EXITS = tuple(e for v in EXITS.values() for e in v)
TRUCK_SHARE = 0.05
EMERGENCY_SHARE = 0.008
Profile = tuple[tuple[float, float], ...]  # (t_s, multiplier), linear in between, flat outside


@dataclass(frozen=True)
class Flow:
    entry: str
    vph: float  # veh/h at profile multiplier 1.0
    dests: tuple[tuple[str, float], ...] = ()  # exit edge -> weight; empty = uniform over reachable exits
    profile: Profile = ((0.0, 1.0),)
    note: str = ""


@dataclass(frozen=True)
class Incident:
    edge: str
    lane: int  # 0 = right lane
    t_s: float  # when the vehicle is inserted (it stops 25 m further on)
    duration_s: float
    pos_m: float = 70.0  # insertion position along the edge
    label: str = "breakdown"


@dataclass(frozen=True)
class DemoSpec:
    id: str
    title: str
    description: str
    flows: tuple[Flow, ...]
    incidents: tuple[Incident, ...] = ()
    horizon_s: float = 900.0  # demand window; the lab then drains for 600 s with no new demand
    drain_s: float = 600.0
    intensity: float = 1.0  # calibrated at 1.0; the lab's UI range is 0.5-1.4
    expected: tuple[str, ...] = ()
    phases: tuple[tuple[str, float, float, str], ...] = ()  # (id, from_s, to_s, what happens)

    @property
    def base_vph(self) -> float:
        return sum(f.vph for f in self.flows)


def _interp(p: Profile, t: float) -> float:
    if t <= p[0][0]:
        return p[0][1]
    for (t0, a), (t1, b) in zip(p, p[1:]):
        if t <= t1:
            return a + (b - a) * (t - t0) / (t1 - t0) if t1 > t0 else b
    return p[-1][1]


def bias(towards: tuple[str, ...], share: float) -> tuple[tuple[str, float], ...]:
    """Destination weights sending ``share`` of the flow to ``towards`` and the rest uniformly to every other exit."""
    others = [d for d in ALL_EXITS if d not in towards]
    return tuple([(d, share / len(towards)) for d in towards] + [(d, (1 - share) / len(others)) for d in others])


# ---------------------------------------------------------------------------------------------------------------- scenarios
# Calibrated on design seeds 9001-9003 only (never on the benchmark seeds): flow rates are veh/h per entry at profile multiplier 1.
RUSH_SCALE, SPILL_SCALE, INCIDENT_SCALE = 1.12, 1.9, 1.7
_DOWNTOWN = ("B1top1", "B1right1")  # the north-east corner is the commuters' destination

RUSH_PROFILE: Profile = ((0, 0.75), (90, 1.0), (240, 1.1), (540, 1.1), (700, 0.6), (900, 0.3))
RUSH = DemoSpec(
    id="demo_rush_hour", title="Heavy rush hour (inbound to the north-east)",
    description=("Morning commute: the south and west entries carry about twice the demand of the north and east entries, and 65 % of "
                 "it heads for the north-east corner. Demand is already high when the run starts, peaks from minute 4 to 10 and "
                 "eases after minute 13. The queue starts at the junctions that feed that corner."),
    flows=tuple([Flow(e, round(620 * RUSH_SCALE), bias(_DOWNTOWN, 0.65), RUSH_PROFILE, "inbound commuters") for e in ENTRIES["W"] + ENTRIES["S"]]
                + [Flow(e, round(300 * RUSH_SCALE), (), RUSH_PROFILE, "outbound / cross traffic") for e in ENTRIES["E"] + ENTRIES["N"]]),
    expected=("Queues appear within 2 minutes and the network is saturated by minute 5",
              "Stop-and-go on the approaches to the north-east corner (B1), with spillback into the links that feed it",
              "Queues do not clear until well after demand eases (minute 13)"),
    phases=(("buildup", 0, 300, "queues form at the junctions feeding the north-east corner"),
            ("peak", 300, 780, "saturated: standing queues, spillback between neighbouring junctions"),
            ("recovery", 780, 1500, "demand eases; the queues drain only if the signals discharge them efficiently")),
)

SPILL_PROFILE: Profile = ((0, 0.65), (150, 1.0), (600, 1.0), (800, 0.5), (900, 0.3))
SURGE: Profile = ((0, 0.65), (150, 1.0), (280, 1.0), (340, 1.6), (540, 1.6), (620, 1.0), (700, 0.8), (800, 0.5), (900, 0.3))
SPILLBACK = DemoSpec(
    id="demo_spillback", title="Spillback-prone asymmetric demand",
    description=("A heavy northbound stream enters at the south-east (B0) and almost all of it turns into the short link B0-B1, "
                 "while strong westbound cross traffic and a left-turning flow load junction B1. A surge on the northbound stream "
                 "between minutes 5.5 and 10.5 pushes the B0-B1 link past its storage, so the queue spills back into B0 and "
                 "then along the south corridor. Total demand is moderate: the problem is the geometry, not the volume."),
    flows=(Flow("bottom1B0", round(700 * SPILL_SCALE), bias(("B1top1",), 0.88), SURGE, "northbound stream into B1 (surges)"),
           Flow("right1B1", round(560 * SPILL_SCALE), bias(("A1left1", "A0left0"), 0.8), SPILL_PROFILE, "westbound cross traffic at B1"),
           Flow("left0A0", round(540 * SPILL_SCALE), bias(("B0right0", "B1right1"), 0.75), SPILL_PROFILE, "eastbound corridor along the south"),
           Flow("top0A1", round(430 * SPILL_SCALE), bias(("B1right1",), 0.6), SPILL_PROFILE, "left turns onto the A1-B1 link"),
           Flow("left1A1", round(300 * SPILL_SCALE), (), SPILL_PROFILE), Flow("top1B1", round(300 * SPILL_SCALE), (), SPILL_PROFILE),
           Flow("bottom0A0", round(280 * SPILL_SCALE), (), SPILL_PROFILE), Flow("right0B0", round(280 * SPILL_SCALE), (), SPILL_PROFILE)),
    expected=("The B0-B1 link fills first (peak at the surge), then queues spill back into junction B0",
              "Eastbound corridor traffic is blocked at B0 although it has green: a queue that is not at its own stop line",
              "Recovery begins once the surge ends, around minute 11"),
    phases=(("buildup", 0, 340, "asymmetric demand loads B1 and the B0-B1 link"),
            ("surge", 340, 640, "northbound surge: B0-B1 overflows, spillback into B0"),
            ("recovery", 640, 1500, "demand returns to base; spillback dissolves from downstream to upstream")),
)

INCIDENT_PROFILE: Profile = ((0, 0.8), (120, 1.0), (520, 1.0), (680, 0.5), (900, 0.25))
INCIDENT = DemoSpec(
    id="demo_incident", title="Serious incident, near-gridlock",
    description=("Busy, roughly balanced rush-hour demand. At minute 2.5 a heavy vehicle breaks down in the left lane of the central "
                 "link B0-B1, and at minute 4 a second one stops in the right lane: the link is fully blocked for about 3 minutes "
                 "(kept under SUMO's 240 s deadlock limit), then half-blocked until minute 9. Queues spill back through B0 and "
                 "the south corridor into several links at once. Breakdowns are real stopped vehicles inserted into the lane "
                 "(within about 30 s of schedule)."),
    flows=tuple(Flow(e, round(420 * INCIDENT_SCALE), (), INCIDENT_PROFILE) for e in ALL_ENTRIES),
    incidents=(Incident("B0B1", 1, 150, 390, 70, "left-lane breakdown"), Incident("B0B1", 0, 240, 180, 70, "right-lane breakdown")),
    expected=("Ordinary congestion for the first 2.5 minutes, then queues grow fast once the central link is blocked",
              "Spillback in about four or five links at once and near-standstill traffic for minutes (the worst of the three scenarios)",
              "Recovery only after both breakdowns clear and demand eases; some vehicles are still queued at minute 25 in a fraction of runs",
              "SUMO may rescue a few vehicles from real deadlocks (teleports): they are counted and reported, never hidden"),
    phases=(("buildup", 0, 150, "heavy demand, ordinary congestion"),
            ("incident", 150, 560, "left lane blocked from 2.5 min, fully blocked 4.2-7.0 min, left lane still blocked to 9 min"),
            ("recovery", 560, 1500, "lanes reopened; demand easing; queues discharge")),
)

SPECS: dict[str, DemoSpec] = {s.id: s for s in (RUSH, SPILLBACK, INCIDENT)}


def _thin_arrivals(rng: random.Random, f: Flow, horizon: float, mult: float) -> list[float]:
    peak = max(m for _, m in f.profile) * f.vph * mult / 3600.0
    out, t = [], 0.0
    while True:
        t += rng.expovariate(peak)
        if t >= horizon:
            return out
        if rng.random() < _interp(f.profile, t) * f.vph * mult / 3600.0 / peak:
            out.append(t)


def write_routes(path: Path, net_path: Path, scenario: str, seed: int, horizon_s: float, intensity: float, vtypes_xml: str,
                 seed_multiplier: float) -> dict[str, Any]:
    """Write the route file and return the same manifest shape as ``rl.demand.write_routes`` (plus incident ids)."""
    import sumolib

    spec = SPECS[scenario]
    net = sumolib.net.readNet(str(net_path), withInternal=False)
    rng = random.Random(f"{scenario}-{seed}")
    mult = intensity * seed_multiplier
    cache: dict[tuple[str, str], list[str] | None] = {}

    def route(o: str, d: str) -> list[str] | None:
        if (o, d) not in cache:
            r, _ = net.getShortestPath(net.getEdge(o), net.getEdge(d))
            cache[(o, d)] = [e.getID() for e in r] if r else None
        return cache[(o, d)]

    veh: list[tuple[float, str, Any]] = []  # (t, kind, payload)
    for f in spec.flows:
        reach = [d for d in ALL_EXITS if (route(f.entry, d) or []) and len(route(f.entry, d)) >= 3]
        w = {d: wt for d, wt in f.dests if d in reach} or {d: 1.0 for d in reach}
        names, weights = list(w), list(w.values())
        for t in _thin_arrivals(rng, f, horizon_s, mult):
            veh.append((t, "flow", (f.entry, rng.choices(names, weights)[0])))
    for inc in spec.incidents:
        veh.append((inc.t_s, "incident", inc))
    veh.sort(key=lambda v: (v[0], v[1], str(v[2])))
    lines = ["<routes>", vtypes_xml.strip("\n")]
    manifest: dict[str, Any] = {"scenario": scenario, "seed": seed, "vehicles": 0, "emergency": 0, "intensity": round(mult, 4)}
    sched: dict[str, float] = {}
    emergency_ids: list[str] = []
    incident_ids: list[str] = []
    for k, (t, kind, p) in enumerate(veh):
        vid = f"v{k}"
        sched[vid] = round(t, 2)
        manifest["vehicles"] += 1
        if kind == "incident":
            incident_ids.append(vid)
            end = p.pos_m + 25.0
            lines.append(f'  <vehicle id="{vid}" type="truck" depart="{t:.2f}" departLane="{p.lane}" departPos="{p.pos_m:g}" departSpeed="0">'
                         f'<route edges="{p.edge}"/><stop lane="{p.edge}_{p.lane}" endPos="{end:g}" duration="{p.duration_s:g}"/></vehicle>')
            continue
        o, d = p
        u = rng.random()
        vt = "emergency" if u < EMERGENCY_SHARE else "truck" if u < EMERGENCY_SHARE + TRUCK_SHARE else "car"
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
    manifest["incident_ids"] = incident_ids
    manifest["incident_vehicles"] = len(incident_ids)
    return manifest
