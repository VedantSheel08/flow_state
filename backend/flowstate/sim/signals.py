"""Traffic-signal design and operation.

Two base plans, both executed by SUMO itself:

* ``fixed``    – conventional coordinated pre-timed programs. ``netconvert``
                  supplies the phase *structure* (which movements run together:
                  through + permissive right/left, protected lagging left,
                  pedestrian walk); :func:`retime_net` then replaces every
                  duration and adds a cycle offset (see "Pre-timed design").
* ``actuated`` – the same phase sequence run by SUMO's gap-based actuated
                  controller with automatically placed loop detectors; greens
                  extend between ``minDur`` and ``maxDur`` with demand.
                  Pedestrian clearance, amber and all-red intervals are never
                  shortened.

Pre-timed design (``fixed``)
----------------------------
Everything below is textbook practice, *not* a calibration to measured
counts of a real city (none is available for this synthetic district).

* **Yellow** – ITE kinematic formula ``Y = t + v / (2a)`` with perception
  time ``t`` = 1.0 s, comfortable deceleration ``a`` = 3.05 m/s² (10 ft/s²,
  level grade) and ``v`` = 1.1 × the posted limit (85th-percentile proxy);
  clamped to 3.0–5.0 s and rounded up to 0.5 s. Left-turn yellow 3.0 s.
* **All-red** – ``AR = (W + L) / v`` with ``W`` the curb-to-curb width of the
  street being crossed (taken from the pedestrian-crossing length in the
  network), ``L`` = 6 m vehicle length; at least 2.0 s (the former generic value).
* **Pedestrians** – WALK ≥ 7 s; pedestrian change interval = crossing length
  / 1.07 m/s (MUTCD 3.5 ft/s). The flashing-don't-walk phase is what is left
  after the amber and all-red are credited, keeping a 1 s buffer before the
  conflicting green: ``FDW = ceil(len/1.07 + 1 − Y − AR)``. SUMO's walkers
  are faster (≈1.4 m/s), so this is conservative. This clearance is a hard
  floor for the green of the *cross* street, which is why minor streets
  crossing a wide arterial still receive ~25 s of green.
* **Cycle length by intersection type** – 80 s for every signal on a
  coordinated corridor (arterial × arterial, arterial × collector, arterial ×
  local; a common cycle is what makes progression possible), 40 s (half
  cycle, still compatible) for collector × collector, which has too little
  traffic to justify a long cycle. Green is split in proportion to an
  assumed critical flow ratio per stage (``APPROACH_VOLUME_INDEX`` ÷ through
  lanes), after the pedestrian and minimum-green floors, Webster-style.
  Protected lagging lefts are 8 s (≈ 4 vehicles per cycle at 2 s saturation
  headway; the district's bays see roughly 10 % lefts). The cycle length was
  chosen by simulation, see "Measured" below.
* **Split phasing on collectors** – a one-lane collector approach shares its
  lane between left, through and right. With the permissive lefts of the
  netconvert phases, opposing left-turners from long queues meet inside the
  junction and deadlock (SUMO then teleports them after 240 s). At
  arterial × collector signals the collector's two directions therefore get
  consecutive greens (``split_minor``); pedestrians keep their WALK across
  both. Local streets (all-way stop priority, low volume) keep the cheaper
  shared phase.
* **Offsets** – for each corridor (a collector/arterial street with ≥ 2
  signals) the through-green windows are propagated at 85 % of the limit.
  A deterministic search minimises the (squared, so both directions are
  served) waiting-plus-stop cost of through traffic over all corridors
  jointly; the same intersection belongs to two corridors, the classic
  grid-offset compromise. Offsets are stored in the ``tlLogic`` ``offset``
  attribute, so SUMO runs the plan open-loop with no controller.

Measured in SUMO (``baseline`` = the previous generic ~90 s plans with all
offsets 0, run with the same code otherwise; time loss is the mean per car):

* ``rush_hour`` 15 min, seeds 42/7/99 – AM: time loss 104.9 → 92.5 s, peak
  queued vehicles 97 → 75, teleports 7 → 0, completed trips 542 → 558. PM:
  99.1 → 96.4 s, peak queue 91 → 79, teleports 1 → 0. The remaining
  collisions (2, AM) are sublane lane-change contacts at stop-line discharge
  (adjacent through lanes, 2–3 s after green), which depend on driver
  lane-change parameters rather than on timing.
* Free-flow corridor probe (one car every 7 s end to end, no other traffic):
  mean delay 81 → 66 s, stops per car 2.31 → 1.84. Progression is uneven:
  King EB 108 s / 3.1 stops against King WB 49 s / 1.5.
* Cycle sweep (rush hour, four seeds, an earlier iteration): time loss 91.5 s
  at C=80, 96.8 s at C=90, 102 s at C=100; without offsets C=80 gives 94.9 s.
* The other nine scenarios (3 seeds each) are 0–10 % *slower* per car than
  baseline (e.g. light load 76 → 79 s): legal pedestrian clearance (the old
  plan allowed 9 s to cross 19.8 m, i.e. ≥ 2.2 m/s) takes green from the
  arterials. Teleports in the incident scenarios are not fixed by timing:
  accident 7 → 13, disruption 17 → 12, ambulance 0 → 6 (bluelight vehicle
  and a left-turner stopped in the same junction box); over all 70 runs
  36 → 33.

Known limits: priority strategies (pre-emption, TSP) displace a signal from
its offset permanently — there is no transition back to the reference
pattern; demand is not measured, so splits are design-stage estimates;
offsets give only partial two-way progression on this 200 m grid because the
arterials' through greens are ~25-30 % of the cycle (pedestrian clearance
across a 20 m arterial and protected lefts take the rest); one-lane
collector approaches still see permissive lefts at collector × collector.

On top of either plan two optional priority strategies act through TraCI by
*holding* or *ending* the current phase early. Neither ever skips an amber,
all-red or pedestrian-clearance interval, so every signal sequence remains
legal:

* **Emergency pre-emption** – every signal an emergency vehicle will reach
  within ``PREEMPT_HORIZON_S`` (via ``vehicle.getNextTLS``) holds the green
  that serves it, or ends conflicting greens once they have run a minimum
  time – the first after ``MIN_GREEN_BEFORE_PREEMPT``, the ones that follow
  on the way to the serving phase after ``MIN_GREEN_WHILE_ADVANCING`` – so
  the queue in front of the vehicle has discharged by the time it arrives.
* **Transit signal priority** – a bus within reach of a green extends it by
  up to ``tsp_max_extension`` seconds.

New strategies can subclass :class:`SignalPolicy`.
"""

from __future__ import annotations

import math
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Collection, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine

SIGNAL_MODES = {
    "fixed": "Fixed-time (pre-timed)",
    "actuated": "Actuated (gap-based, loop detectors)",
}

# ---------------------------------------------------------------------------
# Design standards (assumptions; see module docstring)
# ---------------------------------------------------------------------------
PERCEPTION_TIME = 1.0  # s, ITE yellow formula
COMFORT_DECEL = 3.05  # m/s² (10 ft/s²)
V85_FACTOR = 1.10  # 85th-percentile approach speed / posted limit
VEHICLE_LENGTH = 6.0  # m, clearance formula
MIN_YELLOW, MAX_YELLOW, LEFT_YELLOW = 3.0, 5.0, 3.0
MIN_ALL_RED = 2.0
PED_SPEED = 1.07  # m/s = 3.5 ft/s (MUTCD)
WALK_MIN = 7.0  # s
PED_BUFFER = 1.0  # s between the end of pedestrian clearance and the conflicting green
STARTUP_LOST = 2.0  # s lost to queue start-up at the beginning of a through green
YELLOW_CREDIT = 1.0  # s of the amber that is still usable by a platoon
PROGRESSION_FACTOR = 0.85  # platoon speed / posted limit

STREET_RANK = {"arterial": 3, "arterial_bike": 3, "collector": 2, "local": 1}
# Relative peak-hour volume of one approach by street class (arterial = 1). Used for green splits here
# and for entry-point weights in demand.py so that signal design and demand are mutually consistent.
APPROACH_VOLUME_INDEX = {"arterial": 1.0, "arterial_bike": 1.0, "collector": 0.35, "local": 0.12}


@dataclass(frozen=True)
class TypePlan:
    """Timing parameters shared by all intersections of one type."""

    cycle: float  # s
    left_green: float  # s of protected left per stage (only where the geometry has a left phase)
    min_green: float  # s, minimum vehicle green (walk + clearance counts)
    split_minor: bool = False  # give each direction of a left-less (single shared lane) street its own green


TYPE_PLANS: dict[str, TypePlan] = {
    "arterial_arterial": TypePlan(cycle=80, left_green=8.0, min_green=12.0),
    "arterial_collector": TypePlan(cycle=80, left_green=8.0, min_green=10.0, split_minor=True),
    "arterial_local": TypePlan(cycle=80, left_green=8.0, min_green=10.0),
    "collector_collector": TypePlan(cycle=40, left_green=8.0, min_green=8.0),
    "collector_local": TypePlan(cycle=40, left_green=8.0, min_green=8.0),
    "local_local": TypePlan(cycle=40, left_green=8.0, min_green=8.0),
}
CORRIDOR_WEIGHT = {3: 1.0, 2: 0.4}  # street rank -> weight in the offset objective


def _ceil_half(x: float) -> float:
    return math.ceil(x * 2 - 1e-9) / 2.0


def yellow_time(speed_limit: float, *, left: bool = False) -> float:
    """ITE kinematic yellow change interval (s) for a posted limit in m/s."""
    if left:
        return LEFT_YELLOW
    v = speed_limit * V85_FACTOR
    return min(MAX_YELLOW, max(MIN_YELLOW, _ceil_half(PERCEPTION_TIME + v / (2 * COMFORT_DECEL))))


def all_red_time(crossed_width: float, speed_limit: float) -> float:
    """All-red clearance (s) for vehicles leaving at ``speed_limit`` across ``crossed_width`` metres."""
    v = max(speed_limit, 1.0) * V85_FACTOR
    return max(MIN_ALL_RED, _ceil_half((crossed_width + VEHICLE_LENGTH) / v))


def pedestrian_change_time(crossing_length: float) -> float:
    return crossing_length / PED_SPEED


def flashing_dont_walk(crossing_length: float, yellow: float, all_red: float) -> int:
    """Pedestrian clearance interval left after the amber and all-red are credited."""
    return max(0, math.ceil(pedestrian_change_time(crossing_length) + PED_BUFFER - yellow - all_red - 1e-9))


# ---------------------------------------------------------------------------
# Phase roles (shared by the pre-timed designer and the actuated writer)
# ---------------------------------------------------------------------------
GREEN, CLEAR, YELLOW, LEFT, LEFT_YELLOW_ROLE, ALL_RED = "green", "clear", "yellow", "left", "left_yellow", "all_red"


def phase_roles(states: Sequence[str], crossing: Collection[int] = ()) -> list[str]:
    """Label each phase of a program: green (walk), clear (pedestrian clearance while vehicles still
    have green), yellow, left (protected left after the through yellow), left_yellow, all_red.

    ``crossing`` lists pedestrian link indices when known; without it a phase is a *clear* phase if it
    equals its predecessor with some ``G`` turned ``r`` (which is exactly what netconvert emits).
    """
    roles: list[str] = []
    for i, s in enumerate(states):
        veh = [c for k, c in enumerate(s) if k not in crossing]
        prev = states[i - 1] if i else None
        if any(c in "yY" for c in veh):
            roles.append(YELLOW if not roles or roles[-1] in (GREEN, CLEAR, ALL_RED) else LEFT_YELLOW_ROLE)
        elif not any(c in "Gg" for c in veh):
            roles.append(ALL_RED)
        elif roles and roles[-1] in (YELLOW, LEFT):
            roles.append(LEFT)
        elif prev is not None and roles and roles[-1] in (GREEN, CLEAR) and len(prev) == len(s) and \
                any(a != b for a, b in zip(prev, s)) and all(a == b or (a == "G" and b == "r") for a, b in zip(prev, s)):
            roles.append(CLEAR)
        else:
            roles.append(GREEN)
    return roles


def infer_crossings(states: Sequence[str]) -> set[int]:
    """Pedestrian link indices of a program: a crossing shows WALK (``G``) but never an amber, whereas
    every vehicle link that is ever green is ambered before it turns red."""
    n = max((len(s) for s in states), default=0)
    return {i for i in range(n) if any(s[i:i + 1] == "G" for s in states) and all(s[i:i + 1] not in ("y", "Y") for s in states)}


def write_actuated_programs(tls_programs: dict[str, list[tuple[float, str]]], path: Path) -> None:
    """Additional file with an actuated variant of every static program.

    Greens run between ``minDur`` and ``maxDur`` (never below the 7 s pedestrian WALK); pedestrian
    clearance, amber and all-red intervals keep their fixed duration so the sequence stays legal.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<additional>"]
    for tid, phases in tls_programs.items():
        lines.append(f'  <tlLogic id="{tid}" type="actuated" programID="actuated" offset="0">')
        lines.append('    <param key="max-gap" value="3.0"/><param key="detector-gap" value="2.5"/><param key="passing-time" value="2.0"/><param key="show-detectors" value="false"/>')
        states = [s for _, s in phases]
        roles = phase_roles(states, infer_crossings(states))
        for (dur, state), role in zip(phases, roles):
            green = role in (GREEN, LEFT)
            if green and dur >= 10:
                lines.append(f'    <phase duration="{dur:g}" minDur="{max(WALK_MIN, dur * 0.35):.0f}" maxDur="{dur * 2:.0f}" state="{state}"/>')
            elif green:
                lines.append(f'    <phase duration="{dur:g}" minDur="{min(dur, 4):g}" maxDur="{dur * 2:.0f}" state="{state}"/>')
            else:
                lines.append(f'    <phase duration="{dur:g}" state="{state}"/>')
        lines.append("  </tlLogic>")
    lines.append("</additional>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Reading signal geometry from a SUMO network
# ---------------------------------------------------------------------------
@dataclass
class Link:
    index: int
    edge: str
    dir: str  # s | l | r | L | R | t
    speed: float
    cls: str
    axis: str  # ew | ns
    street: str
    sign: int = 1  # +1 travelling towards increasing x / y, -1 the opposite way


@dataclass
class SignalDesign:
    id: str
    x: float
    y: float
    states: list[str]  # netconvert phase structure
    links: dict[int, Link]  # vehicle links
    crossings: dict[int, float]  # pedestrian link index -> crossing length (m)
    lanes_through: dict[str, int]  # approach edge -> number of through lanes

    @property
    def streets(self) -> dict[str, tuple[str, int]]:
        """axis -> (street name, class rank)."""
        out: dict[str, tuple[str, int]] = {}
        for l in self.links.values():
            rank = STREET_RANK.get(l.cls, 1)
            if l.axis not in out or rank > out[l.axis][1]:
                out[l.axis] = (l.street, rank)
        return out

    @property
    def kind(self) -> str:
        ranks = sorted((r for _, r in self.streets.values()), reverse=True)
        while len(ranks) < 2:
            ranks.append(1)
        names = {3: "arterial", 2: "collector", 1: "local"}
        return f"{names[ranks[0]]}_{names[ranks[1]]}"


def read_signal_designs(net_path: Path | str) -> dict[str, SignalDesign]:
    root = ET.parse(str(net_path)).getroot()
    edge_attr: dict[str, dict[str, str]] = {}
    lane_len: dict[str, float] = {}
    edge_speed: dict[str, float] = {}
    lane_shape: dict[str, list[tuple[float, float]]] = {}
    for e in root.iter("edge"):
        eid = e.get("id", "")
        edge_attr[eid] = dict(e.attrib)
        for ln in e.iter("lane"):
            lid = ln.get("id", "")
            lane_len[lid] = float(ln.get("length", 0))
            edge_speed[eid] = max(edge_speed.get(eid, 0.0), float(ln.get("speed", 0)))
            pts = [tuple(map(float, p.split(","))) for p in ln.get("shape", "").split()]
            lane_shape[lid] = pts  # type: ignore[assignment]
    junction_xy = {j.get("id"): (float(j.get("x", 0)), float(j.get("y", 0))) for j in root.iter("junction")}
    states: dict[str, list[str]] = {}
    for tl in root.iter("tlLogic"):
        states[tl.get("id", "")] = [p.get("state", "") for p in tl.iter("phase")]
    designs = {tid: SignalDesign(tid, *junction_xy.get(tid, (0.0, 0.0)), states=st, links={}, crossings={}, lanes_through={}) for tid, st in states.items()}
    through_lanes: dict[str, set[str]] = {}
    for c in root.iter("connection"):
        tid, idx = c.get("tl"), c.get("linkIndex")
        if tid is None or idx is None or tid not in designs:
            continue
        frm, to = c.get("from", ""), c.get("to", "")
        d = designs[tid]
        if frm.startswith(":") or to.startswith(":"):
            cross = next((x for x in (frm, to) if "_c" in x and x.startswith(":")), None)
            if cross is not None:
                d.crossings[int(idx)] = lane_len.get(cross + "_0", 0.0)
            continue
        attrs = edge_attr.get(frm, {})
        lane_id = f"{frm}_{c.get('fromLane', 0)}"
        shape = lane_shape.get(lane_id) or []
        if len(shape) >= 2:
            dx, dy = shape[-1][0] - shape[-2][0], shape[-1][1] - shape[-2][1]
        else:
            dx = dy = 0.0
        axis = "ew" if abs(dx) > abs(dy) else "ns"
        sign = 1 if (dx if axis == "ew" else dy) > 0 else -1
        d.links[int(idx)] = Link(int(idx), frm, c.get("dir", "s"), edge_speed.get(frm, 13.89), attrs.get("type", "local"), axis, attrs.get("name", frm), sign)
        if c.get("dir") == "s":
            through_lanes.setdefault(f"{tid}|{frm}", set()).add(c.get("fromLane", "0"))
    for key, lanes in through_lanes.items():
        tid, frm = key.split("|", 1)
        designs[tid].lanes_through[frm] = len(lanes)
    return designs


# ---------------------------------------------------------------------------
# Timing of one signal
# ---------------------------------------------------------------------------
SPLIT_YELLOW, SPLIT_ALL_RED, SPLIT_GREEN = "split_yellow", "split_all_red", "split_green"
PHASE_NAMES = {GREEN: "walk", CLEAR: "ped-clear", YELLOW: "yellow", LEFT: "left", LEFT_YELLOW_ROLE: "left-yellow", ALL_RED: "all-red",
               SPLIT_YELLOW: "yellow", SPLIT_ALL_RED: "all-red", SPLIT_GREEN: "green"}


@dataclass
class Stage:
    items: list[tuple[str, str]]  # (role, state) in order
    crossing: float  # m, longest crossing walked in this stage
    flow_ratio: float  # critical flow ratio estimate
    split: bool = False
    yellow: float = 0.0
    left_yellow: float = LEFT_YELLOW
    all_red: float = 0.0
    split_all_red: float = MIN_ALL_RED
    fdw: int = 0

    def has(self, role: str) -> bool:
        return any(r == role for r, _ in self.items)


@dataclass
class SignalProgram:
    tid: str
    kind: str
    cycle: float
    phases: list[tuple[float, str, str]]  # duration, state, name
    windows: dict[tuple[str, int], list[tuple[float, float]]]  # (axis, travel sign) -> through-green windows
    offset: float = 0.0

    @property
    def states(self) -> list[str]:
        return [s for _, s, _ in self.phases]

    @property
    def durations(self) -> list[float]:
        return [d for d, _, _ in self.phases]


def _set(state: str, idxs: Collection[int], char: str) -> str:
    return "".join(char if i in idxs else c for i, c in enumerate(state))


def _split_items(d: SignalDesign, items: list[tuple[str, str]]) -> list[tuple[str, str]] | None:
    """Sequence the two directions of a left-less street one after the other (split phasing).

    Opposing permissive lefts that share a lane with the through movement meet inside the junction box
    whenever queues are long and block each other (a deadlock that SUMO resolves by teleporting).
    Giving each direction its own green removes the conflict. Pedestrians keep their WALK across both.
    """
    green = items[0][1]
    served = [d.links[i] for i in d.links if i < len(green) and green[i] in "Gg"]
    plus = {l.index for l in served if l.sign > 0}
    minus = {l.index for l in served if l.sign < 0}
    if not plus or not minus or not any(l.dir in "lL" for l in served):
        return None
    out: list[tuple[str, str]] = [(GREEN, _set(green, minus, "r"))]
    a_yellow = "".join("y" if i in plus and green[i] in "Gg" else ("r" if i in d.links else green[i]) for i in range(len(green)))
    out.append((SPLIT_YELLOW, a_yellow))
    out.append((SPLIT_ALL_RED, "".join("r" if i in d.links else green[i] for i in range(len(green)))))
    out.append((SPLIT_GREEN, _set(green, plus, "r")))
    for role, state in items[1:]:
        if role == CLEAR:
            out.append((CLEAR, _set(state, plus, "r")))
        elif role == YELLOW:
            out.append((YELLOW, _set(state, plus, "r")))
        else:
            out.append((role, state))
    return out


def _stages(d: SignalDesign, roles: list[str], plan: TypePlan) -> list[Stage]:
    stages: list[Stage] = []
    for i, r in enumerate(roles):
        if r == GREEN:
            stages.append(Stage([], 0.0, 0.0))
        stages[-1].items.append((r, d.states[i]))
    for st in stages:
        g = st.items[0][1]
        st.crossing = max((d.crossings[i] for i in d.crossings if i < len(g) and g[i] == "G"), default=0.0)
        served = {l.edge: l for i, l in d.links.items() if i < len(g) and g[i] in "Gg" and l.dir in "sS"}
        ratios = [APPROACH_VOLUME_INDEX.get(l.cls, 0.12) / max(1, d.lanes_through.get(l.edge, 1)) for l in served.values()]
        st.flow_ratio = max(ratios, default=0.1)
        yl = next((s for r, s in st.items if r == YELLOW), "")
        clearing = [d.links[i] for i in d.links if i < len(yl) and yl[i] in "yY"] or list(served.values())
        v = max((l.speed for l in clearing), default=13.89)
        st.yellow = yellow_time(v)
        width = st.crossing or 12.0
        st.all_red = all_red_time(width, v)
        if not st.has(LEFT) and plan.split_minor:
            split = _split_items(d, st.items)
            if split is not None:
                st.items, st.split = split, True
        st.fdw = flashing_dont_walk(st.crossing, st.yellow, st.all_red) if st.has(CLEAR) else 0
    return stages


def design_program(d: SignalDesign, plan: TypePlan | None = None) -> SignalProgram:
    """Re-time the phase structure of ``d`` for its intersection type."""
    plan = plan or TYPE_PLANS.get(d.kind, TYPE_PLANS["local_local"])
    stages = _stages(d, phase_roles(d.states, set(d.crossings)), plan)
    if not stages:
        raise ValueError(f"signal {d.id} has no green phase")
    cycle = plan.cycle

    def fixed(st: Stage) -> float:
        t = st.fdw + st.yellow + st.all_red
        if st.has(LEFT):
            t += plan.left_green + st.left_yellow
        if st.split:
            t += st.yellow + st.split_all_red
        return t

    def min_var(st: Stage) -> float:  # time of the walk / green interval(s) that is not fixed by the clearances
        if st.split:
            return plan.min_green + max(4.0, plan.min_green - st.fdw)
        return max(WALK_MIN, plan.min_green - st.fdw)

    need = sum(fixed(s) + min_var(s) for s in stages)
    while need > cycle:  # infeasible with the pedestrian floors: lengthen in 5 s steps
        cycle += 5
    free = cycle - need
    total_y = sum(s.flow_ratio for s in stages)
    var = [math.floor((min_var(s) + free * s.flow_ratio / total_y) * 2) / 2 for s in stages]
    remainder = cycle - sum(fixed(s) + v for s, v in zip(stages, var))
    order = sorted(range(len(stages)), key=lambda i: -stages[i].flow_ratio)
    k = 0
    while remainder > 1e-9:  # hand out the rounding remainder 0.5 s at a time, heaviest stage first
        var[order[k % len(order)]] += 0.5
        remainder -= 0.5
        k += 1
    phases: list[tuple[float, str, str]] = []
    for st, v in zip(stages, var):
        green_a = v
        green_b = v
        if st.split:  # both directions get the same effective green (the second one also owns the clearance interval)
            green_a = max(plan.min_green, math.floor((v + st.fdw) / 2 * 2) / 2)
            green_b = v - green_a
        for role, state in st.items:
            dur = {GREEN: green_a, SPLIT_GREEN: green_b, CLEAR: float(st.fdw), YELLOW: st.yellow, SPLIT_YELLOW: st.yellow,
                   LEFT: plan.left_green, LEFT_YELLOW_ROLE: st.left_yellow, ALL_RED: st.all_red, SPLIT_ALL_RED: st.split_all_red}[role]
            if dur > 0:
                phases.append((dur, state, PHASE_NAMES[role]))
    prog = SignalProgram(d.id, d.kind, cycle, phases, {})
    prog.windows = through_windows(d, prog)
    return prog


def through_windows(d: SignalDesign, prog: SignalProgram) -> dict[tuple[str, int], list[tuple[float, float]]]:
    """Per (axis, direction of travel), the (start, end) program times during which a platoon can pass the
    through movement (queue start-up lost time removed, part of the amber credited)."""
    out: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for axis in ("ew", "ns"):
        for sign in (1, -1):
            idxs = [i for i, l in d.links.items() if l.axis == axis and l.sign == sign and l.dir in "sS"]
            if not idxs:
                continue
            t = 0.0
            runs: list[list[float]] = []
            for dur, state, _name in prog.phases:
                green = any(i < len(state) and state[i] in "Gg" for i in idxs)
                yellow = any(i < len(state) and state[i] in "yY" for i in idxs)
                if green:
                    if runs and abs(runs[-1][1] - t) < 1e-9:
                        runs[-1][1] = t + dur
                    else:
                        runs.append([t, t + dur])
                elif yellow and runs and abs(runs[-1][1] - t) < 1e-9:
                    runs[-1][1] = t + min(dur, YELLOW_CREDIT)
                t += dur
            out[(axis, sign)] = [(s + STARTUP_LOST, e) for s, e in runs if e - s > STARTUP_LOST + 1]
    return out


# ---------------------------------------------------------------------------
# Corridor offsets
# ---------------------------------------------------------------------------
@dataclass
class Corridor:
    name: str
    axis: str
    rank: int
    nodes: list[str]  # ordered along the street (increasing x or y)
    pos: list[float]  # coordinate of each node along the street
    speed: float  # posted limit, m/s


def find_corridors(designs: dict[str, SignalDesign]) -> list[Corridor]:
    groups: dict[tuple[str, str], list[tuple[float, str, int, float]]] = {}
    for tid, d in designs.items():
        speeds: dict[str, float] = {}
        for l in d.links.values():
            speeds[l.axis] = max(speeds.get(l.axis, 0.0), l.speed)
        for axis, (name, rank) in d.streets.items():
            if rank >= 2:
                groups.setdefault((name, axis), []).append((d.x if axis == "ew" else d.y, tid, rank, speeds[axis]))
    out = []
    for (name, axis), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        members.sort()
        out.append(Corridor(name, axis, max(m[2] for m in members), [m[1] for m in members], [m[0] for m in members], max(m[3] for m in members)))
    return out


def _wait_table(windows: list[tuple[float, float]], offset: int, period: int, cycle: int) -> list[float]:
    """Seconds a vehicle arriving at absolute time t (0 <= t < cycle) waits for the through green."""
    table = []
    for t in range(cycle):
        pt = (t - offset) % period
        best = float(cycle)
        for s, e in windows:
            for shift in (-period, 0, period):
                if s + shift <= pt < e + shift:
                    best = 0.0
                    break
                best = min(best, (s + shift - pt) % period)
            if best == 0.0:
                break
        table.append(best)
    return table


STOP_PENALTY = 6.0  # s of cost per stop, on top of the waiting time itself
SAMPLE_STEP = 2  # s between sampled platoon arrival times


def _leg_cost(tables: list[list[float]], travel: list[int], cycle: int) -> tuple[float, float]:
    """Mean downstream waiting time (s) and stops per vehicle for traffic arriving uniformly at the
    first signal and driving the leg without ever being held back by anything but the signals."""
    wait_sum = stop_sum = 0.0
    n = 0
    for tau in range(0, cycle, SAMPLE_STEP):
        t = float(tau)
        for k, tab in enumerate(tables):
            w = tab[int(t) % cycle]
            if k:  # waiting at the first signal is the same for every offset
                wait_sum += w
                stop_sum += 1.0 if w > 0.5 else 0.0
            t += w + (travel[k + 1] - travel[k] if k + 1 < len(travel) else 0)
        n += 1
    return wait_sum / n, stop_sum / n


def optimize_offsets(designs: dict[str, SignalDesign], programs: dict[str, SignalProgram], seed: int = 11, restarts: int = 8) -> tuple[dict[str, int], dict[str, Any]]:
    """Deterministic joint search for the offsets that minimise the weighted two-way corridor cost
    (waiting + a stop penalty, squared so that both directions are served) of through traffic travelling at ``PROGRESSION_FACTOR`` x the limit."""
    corridors = find_corridors(designs)
    cycle = int(max((p.cycle for p in programs.values()), default=100))
    if any(cycle % int(p.cycle) for p in programs.values()):
        raise ValueError("signal cycles must divide the common cycle")
    node_ids = sorted({n for c in corridors for n in c.nodes})
    tables: dict[tuple[str, str], list[list[float]]] = {}
    for c in corridors:
        for n in c.nodes:
            if (n, c.axis, 1) not in tables:
                p = programs[n]
                for sign in (1, -1):
                    tables[(n, c.axis, sign)] = [_wait_table(p.windows.get((c.axis, sign), []), o, int(p.cycle), cycle) for o in range(cycle)]
    legs: list[tuple[Corridor, list[str], list[int], int]] = []
    for c in corridors:
        v = c.speed * PROGRESSION_FACTOR
        for sign, order in ((1, list(range(len(c.nodes)))), (-1, list(range(len(c.nodes) - 1, -1, -1)))):
            legs.append((c, [c.nodes[i] for i in order], [int(round(abs(c.pos[i] - c.pos[order[0]]) / v)) for i in order], sign))
    memo: dict[tuple, tuple[float, float]] = {}

    def leg_cost(li: int, off: dict[str, int]) -> tuple[float, float]:
        c, nodes, tt, sign = legs[li]
        key = (li, *(off[n] for n in nodes))
        if key not in memo:
            memo[key] = _leg_cost([tables[(n, c.axis, sign)][off[n]] for n in nodes], tt, cycle)
        return memo[key]

    legs_of: dict[str, list[int]] = {}
    for li, (c, nodes, _tt, _sg) in enumerate(legs):
        for n in nodes:
            legs_of.setdefault(n, []).append(li)

    def cost(li: int, off: dict[str, int]) -> float:
        w, st = leg_cost(li, off)
        # convex in the per-direction cost, so that one direction is not sacrificed for the other
        return CORRIDOR_WEIGHT.get(legs[li][0].rank, 0.4) * (w + STOP_PENALTY * st) ** 2 / 30.0

    rng = random.Random(seed)
    best_off, best = {n: 0 for n in node_ids}, math.inf
    for r in range(restarts):
        off = {n: (0 if r == 0 else rng.randrange(cycle)) for n in node_ids}
        for _pass in range(5):
            improved = False
            for n in node_ids:
                mine = legs_of.get(n, [])
                keep, cur = off[n], sum(cost(li, off) for li in mine)
                for o in range(cycle):
                    off[n] = o
                    s = sum(cost(li, off) for li in mine)
                    if s < cur - 1e-9:
                        cur, keep, improved = s, o, True
                off[n] = keep
            if not improved:
                break
        total = sum(cost(li, off) for li in range(len(legs)))
        if total < best - 1e-9:
            best, best_off = total, dict(off)
    zero = {n: 0 for n in node_ids}
    report: dict[str, Any] = {}
    for li, (c, nodes, _tt, _sg) in enumerate(legs):
        w, st = leg_cost(li, best_off)
        w0, st0 = leg_cost(li, zero)
        rep = report.setdefault(c.name, {"axis": c.axis, "nodes": c.nodes, "legs": []})
        rep["legs"].append({"from": nodes[0], "waitSec": round(w, 1), "stops": round(st, 2), "waitSecNoOffsets": round(w0, 1), "stopsNoOffsets": round(st0, 2)})
    return best_off, report


# ---------------------------------------------------------------------------
# Applying a design to a network file
# ---------------------------------------------------------------------------
def _fmt(x: float) -> str:
    return f"{x:g}"


def retime_net(net_path: Path | str, offsets: bool = True) -> dict[str, Any]:
    """Replace every pre-timed program in ``net_path`` with the engineered one (in place).

    Returns a summary ``{signals: {id: {...}}, corridors: {...}}`` for the network sidecar.
    """
    path = Path(net_path)
    designs = read_signal_designs(path)
    programs = {tid: design_program(d) for tid, d in designs.items()}
    corridors: dict[str, Any] = {}
    if offsets:
        off, corridors = optimize_offsets(designs, programs)
        for tid, o in off.items():
            programs[tid].offset = float(o)
    text = path.read_text(encoding="utf-8")
    for tid, p in programs.items():
        body = "\n".join(f'        <phase duration="{_fmt(dur)}" state="{state}" name="{name}"/>' for dur, state, name in p.phases)
        block = f'<tlLogic id="{tid}" type="static" programID="0" offset="{_fmt(p.offset)}">\n{body}\n    </tlLogic>'
        pattern = re.compile(rf'<tlLogic id="{re.escape(tid)}"[^>]*>.*?</tlLogic>', re.S)
        text, n = pattern.subn(lambda _m, b=block: b, text, count=1)
        if n != 1:
            raise RuntimeError(f"tlLogic {tid} not found in {path.name}")
    path.write_text(text, encoding="utf-8")
    summary = {
        tid: {"type": p.kind, "cycle": p.cycle, "offset": p.offset, "greens": {f"{a}{'+' if sg > 0 else '-'}": [[round(s, 1), round(e, 1)] for s, e in w] for (a, sg), w in p.windows.items()}}
        for tid, p in programs.items()
    }
    return {"signals": summary, "corridors": corridors}


@dataclass
class TlsInfo:
    id: str
    crossing_links: set[int] = field(default_factory=set)
    phase: int = -1
    phase_start: float = 0.0
    prev_state: str = ""
    state: str = ""
    held_by: str | None = None
    advancing: bool = False  # a conflicting green has already been ended for ``held_by``
    hold_until: float = 0.0
    extended: float = 0.0  # seconds of TSP extension granted in the current phase


class SignalPolicy:
    id = "base"

    def step(self, mgr: "SignalManager", t: float) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class SignalManager:
    MIN_GREEN_BEFORE_PREEMPT = 5.0
    MIN_GREEN_WHILE_ADVANCING = 2.0  # later non-serving greens on the way to the one the vehicle needs
    PREEMPT_DISTANCE = 140.0  # always requested within this distance ...
    PREEMPT_HORIZON_S = 20.0  # ... and further out when the vehicle would get there within this time
    PREEMPT_MAX_DISTANCE = 320.0

    def __init__(self, engine: "Engine", mode: str, preemption: bool, tsp: bool, tsp_max_extension: float = 15.0):
        self.engine = engine
        self.mode = mode
        self.preemption = preemption
        self.tsp = tsp
        self.tsp_max_extension = tsp_max_extension
        self.tls: dict[str, TlsInfo] = {}
        self.policies: list[SignalPolicy] = []
        self.preempt_count = 0
        self.tsp_count = 0
        self.tsp_seconds = 0.0

    @property
    def T(self):
        return self.engine.T

    def start(self) -> None:
        T = self.T
        for tid in T.trafficlight.getIDList():
            info = TlsInfo(tid)
            for idx, links in enumerate(T.trafficlight.getControlledLinks(tid)):
                for frm, to, via in links:
                    if frm.startswith(":") or to.startswith(":") and "_c" in to:
                        info.crossing_links.add(idx)
            if self.mode == "actuated":
                T.trafficlight.setProgram(tid, "actuated")
            self.tls[tid] = info

    # -- phase bookkeeping -------------------------------------------------
    def observe(self, t: float, states: dict[str, str], phases: dict[str, int]) -> None:
        for tid, info in self.tls.items():
            ph = phases.get(tid, info.phase)
            if ph != info.phase:
                info.prev_state = info.state
                info.phase = ph
                info.phase_start = t
                info.extended = 0.0
            info.state = states.get(tid, info.state)

    def _cuttable(self, info: TlsInfo, t: float, min_green: float | None = None) -> bool:
        s = info.state
        if "y" in s.lower() or not any(c in "Gg" for c in s):
            return False  # amber / all-red intervals always run in full
        if any(info.prev_state[i:i + 1] == "G" and s[i] == "r" for i in info.crossing_links if i < len(s)):
            return False  # pedestrian clearance interval
        return t - info.phase_start >= (self.MIN_GREEN_BEFORE_PREEMPT if min_green is None else min_green)

    def step(self, t: float) -> None:
        if self.preemption:
            self._preempt(t)
        if self.tsp:
            self._transit_priority(t)
        for p in self.policies:
            p.step(self, t)

    def _preempt(self, t: float) -> None:
        """First-come, first-served pre-emption: one emergency vehicle per signal at a time.

        A second vehicle whose movement is green in the same phase shares the hold instead of waiting.
        """
        T = self.T
        eng = self.engine
        requests: list[tuple[str, str, int, float, str]] = []
        for vid in eng.emergency_active():
            try:
                upcoming = T.vehicle.getNextTLS(vid)
                speed = T.vehicle.getSpeed(vid)
            except Exception:  # noqa: BLE001
                continue
            # signals further out only while the vehicle is moving, so a unit stuck in a queue does not hold them
            horizon = min(self.PREEMPT_MAX_DISTANCE, max(self.PREEMPT_DISTANCE, speed * self.PREEMPT_HORIZON_S))
            for tid, link, dist, state in upcoming:
                if dist > horizon:
                    break
                if tid in self.tls:
                    requests.append((vid, tid, link, dist, state))
        wanted = {(vid, tid) for vid, tid, *_ in requests}
        # release holds whose vehicle has passed or left
        for tid, info in self.tls.items():
            if info.held_by and (info.held_by, tid) not in wanted:
                info.held_by = None
                info.advancing = False
        for vid, tid, link, dist, state in sorted(requests, key=lambda r: r[3]):
            info = self.tls[tid]
            if info.held_by not in (None, vid) and state not in "Gg":
                continue  # another emergency vehicle is being served here
            if info.held_by is None:
                info.held_by = vid
                info.advancing = False
                self.preempt_count += 1
                verb = "holding green" if state in "Gg" else "advancing phases"
                eng.emit("emergency", f"Pre-emption at {eng.junction_name(tid)}: {verb} for {eng.label(vid)}", "info", tls=tid, vehicle=vid)
            if state in "Gg":
                speed = max(3.0, T.vehicle.getSpeed(vid))
                remaining = T.trafficlight.getNextSwitch(tid) - T.simulation.getTime()
                need = min(30.0, dist / speed + 3.0)
                if remaining < need:
                    T.trafficlight.setPhaseDuration(tid, need)
            elif info.held_by == vid and self._cuttable(info, t, self.MIN_GREEN_WHILE_ADVANCING if info.advancing else None):
                T.trafficlight.setPhaseDuration(tid, 0.0)
                info.advancing = True

    def _transit_priority(self, t: float) -> None:
        T = self.T
        eng = self.engine
        for vid in eng.buses_active():
            try:
                upcoming = T.vehicle.getNextTLS(vid)
            except Exception:  # noqa: BLE001
                continue
            if not upcoming:
                continue
            tid, link, dist, state = upcoming[0]
            info = self.tls.get(tid)
            if info is None or info.held_by or dist > 90 or state not in "Gg":
                continue
            if T.vehicle.getStopState(vid) & 1:  # dwelling at a stop
                continue
            speed = max(4.0, T.vehicle.getSpeed(vid))
            need = dist / speed + 1.5
            remaining = T.trafficlight.getNextSwitch(tid) - T.simulation.getTime()
            if remaining >= need:
                continue
            grant = min(need - remaining, self.tsp_max_extension - info.extended)
            if grant <= 0.5:
                continue
            if info.extended == 0:
                self.tsp_count += 1
                eng.emit("transit", f"Transit priority at {eng.junction_name(tid)}: green extended for {eng.label(vid)}", "info", tls=tid, vehicle=vid)
            info.extended += grant
            self.tsp_seconds += grant
            T.trafficlight.setPhaseDuration(tid, remaining + grant)

    def summary(self) -> dict[str, Any]:
        return {"mode": self.mode, "preemptions": self.preempt_count, "tspExtensions": self.tsp_count, "tspSeconds": round(self.tsp_seconds, 1)}
