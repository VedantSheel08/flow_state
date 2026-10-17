"""Stochastic trip generation.

Trips are drawn every simulation step from the scenario's current
:class:`~flowstate.scenarios.base.DemandProfile`:

* the number of new trips per step is Poisson distributed with the profile
  rate (veh/h), so arrivals are random rather than evenly spaced;
* trip ends are chosen from the network fringe (by compass side) or from city
  blocks (driveways / on-street parking along the block frontage), weighted by
  the profile's origin/destination weights, downtown bias and venue
  attractors — an origin–destination matrix that changes over time;
* within a compass side, fringe entries and exits are weighted by street class
  (``APPROACH_VOLUME_INDEX``: arterial 1.0, collector 0.35, local 0.12). A
  gateway to a 2-lane arterial carries several times the traffic of a 1-lane
  residential street; weighting them equally (as before) overloaded the
  collectors and locals and gridlocked them while the arterials ran empty.
  The same indices size the signal green splits (sim/signals.py), so supply
  and demand are consistent. These are *assumed* ratios in line with typical
  urban hierarchies, not counts from a real city;
* the vehicle class follows the profile mix and each vehicle receives one of
  the seeded driver profiles of its class;
* routes are SUMO shortest-time routes (``simulation.findRoute``); vehicles
  with a navigation device additionally re-route en route;
* **guardrails** against unrealistic gridlock (SUMO resolves a true deadlock
  by teleporting vehicles, and jammed sublane queues are where collisions
  occur), none of which the simulator should be allowed to hide:

  - a trip is not started on an edge whose approach queue has already filled
    ``GATE_FRACTION`` of its jam capacity (7.5 m per vehicle per lane), nor
    while SUMO has more than ``MAX_PENDING`` vehicles waiting to be inserted;
    the attempt is counted in ``metered`` and another origin is drawn;
  - a network-wide *throttle*: every block whose halting queue fills
    ``SPILL_FRACTION`` of its jam capacity (the queue has reached the
    upstream intersection) reduces the generation rate by ``THROTTLE_SLOPE``,
    down to ``MIN_THROTTLE``. Real drivers avoid, delay or reroute trips into
    a jammed district (peak spreading); without it the open-loop Poisson
    demand would keep feeding a blocked grid. Deferred demand is accumulated
    in ``deferred`` (vehicles) so results can state how much was held back.

Pedestrians walk between block frontages over sidewalks, crosswalks and
walking areas using SUMO's striping model.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Any

from .signals import APPROACH_VOLUME_INDEX
from .vtypes import PASSENGER_MIX

if TYPE_CHECKING:  # pragma: no cover
    from ..network.info import NetInfo
    from ..scenarios.base import DemandProfile
    from .engine import Engine


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def weighted(rng: random.Random, items: list[Any], weights: list[float]) -> Any:
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    r = rng.random() * total
    for it, w in zip(items, weights):
        r -= w
        if r <= 0:
            return it
    return items[-1]


class DemandGenerator:
    MIN_TRIP_M = 160.0
    MAX_PERSONS = 450
    JAM_SPACING_M = 7.5  # vehicle length + standstill gap
    GATE_FRACTION = 0.55  # halting vehicles / jam capacity above which a gateway is closed
    MAX_PENDING = 10  # SUMO insertion backlog above which no new trips are started
    SPILL_FRACTION = 0.6  # block queue / jam capacity at which the queue has reached the upstream junction
    THROTTLE_SLOPE = 0.12  # generation-rate reduction per spilled-back block
    MIN_THROTTLE = 0.45

    def __init__(self, engine: "Engine", rng: random.Random, profiles: dict[str, list[str]]):
        self.engine = engine
        self.net: "NetInfo" = engine.net
        self.rng = rng
        self.profiles = profiles
        self.counter = 0
        self.ped_counter = 0
        self.failed = 0
        self.metered = 0  # trip starts refused at a full gateway (another origin is drawn)
        self.deferred = 0.0  # expected trips held back by the throttle (vehicles)
        self.throttle = 1.0
        self._throttle_t = -1e9
        self._pending = 0
        self._pending_t = -1e9
        self._route_n = 0
        self._route_cache: dict[tuple[str, str, str], tuple[str, float] | None] = {}
        self._ped_edges = [e for e in self.net.street_edges if any(self.net.lanes[l].allows_ped for l in self.net.edges[e].lanes)]
        self._zones = [z for z in self.net.zones if z.edges]
        self._gate_cap: dict[str, float] = {}
        self._segment: dict[str, list[str]] = {}
        blocks: dict[tuple[str, str, int], list[str]] = {}
        for e in self.net.edges.values():
            if e.kind in ("street", "entry") and e.street:
                blocks.setdefault((e.street, e.dir, e.seg), []).append(e.id)
        self._blocks = list(blocks.values())
        self._gateway_w: dict[str, float] = {e: APPROACH_VOLUME_INDEX.get(self.net.edges[e].cls, 0.12) for e in self.net.entry_edges + self.net.exit_edges}

    # -- trip ends ---------------------------------------------------------
    def _side_edge(self, side: str, entry: bool) -> str | None:
        lst = (self.net.entries if entry else self.net.exits).get(side, [])
        if not lst:
            return None
        return weighted(self.rng, lst, [self._gateway_w.get(e, 0.12) for e in lst])

    # -- guardrails --------------------------------------------------------
    def _capacity(self, edge: str) -> float:
        """Jam capacity (vehicles) of the street segment that ends at the signal/stop this edge feeds."""
        cap = self._gate_cap.get(edge)
        if cap is None:
            cap = sum(self.net.edges[e].length * max(1, len(self.net.car_lanes(e))) for e in self._seg(edge)) / self.JAM_SPACING_M
            self._gate_cap[edge] = cap
        return cap

    def _seg(self, edge: str) -> list[str]:
        seg = self._segment.get(edge)
        if seg is None:
            seg = self._segment[edge] = self.net.street_segment(edge)
        return seg

    def crowded(self, edge: str) -> bool:
        """True when the approach queue on ``edge`` (and the blocks upstream of it) is nearly full."""
        stats = self.engine.edge_stats
        halting = sum(stats[e][1] for e in self._seg(edge) if e in stats)
        return halting >= self.GATE_FRACTION * self._capacity(edge)

    def update_throttle(self, t: float) -> float:
        """Recompute (at 1 Hz) the generation-rate factor from the number of spilled-back blocks."""
        if t - self._throttle_t >= 1.0:
            self._throttle_t = t
            stats = self.engine.edge_stats
            spilled = 0
            for block in self._blocks:
                halting = sum(stats[e][1] for e in block if e in stats)
                if halting and halting >= self.SPILL_FRACTION * self._capacity(block[0]):
                    spilled += 1
            self.throttle = max(self.MIN_THROTTLE, 1.0 - self.THROTTLE_SLOPE * spilled)
        return self.throttle

    def backlogged(self, t: float) -> bool:
        if t - self._pending_t >= 1.0:
            self._pending_t = t
            self._pending = len(self.engine.T.simulation.getPendingVehicles())
        return self._pending > self.MAX_PENDING

    def _zone_edge(self, core_bias: float) -> str:
        z = weighted(self.rng, self._zones, [core_bias if z.core else 1.0 for z in self._zones])
        return self.rng.choice(z.edges)

    def _end(self, weights: dict[str, float], core_bias: float, entry: bool) -> tuple[str | None, bool]:
        keys = list(weights)
        k = weighted(self.rng, keys, [weights[x] for x in keys])
        if k == "internal":
            return self._zone_edge(core_bias), True
        return self._side_edge(k, entry), False

    # -- routing -----------------------------------------------------------
    def route(self, o: str, d: str, vtype: str) -> tuple[str, float] | None:
        vclass_key = vtype.split("@")[0]
        key = (o, d, vclass_key)
        if key in self._route_cache:
            return self._route_cache[key]
        T = self.engine.T
        try:
            r = T.simulation.findRoute(o, d, vType=vtype)
        except Exception:  # noqa: BLE001
            r = None
        out = None
        if r is not None and r.edges and r.length >= self.MIN_TRIP_M:
            self._route_n += 1
            rid = f"r{self._route_n}"
            T.route.add(rid, list(r.edges))
            out = (rid, r.length)
        self._route_cache[key] = out
        return out

    def pick_type(self, prof: "DemandProfile") -> str:
        u = self.rng.random()
        if u < prof.bike_share:
            base = "bike"
        elif u < prof.bike_share + prof.truck_share:
            base = "truck"
        else:
            mix = prof.mix or PASSENGER_MIX
            keys = list(mix)
            base = weighted(self.rng, keys, [mix[k] for k in keys])
        return self.rng.choice(self.profiles[base])

    # -- per step ----------------------------------------------------------
    def step(self, t: float, dt: float, prof: "DemandProfile", density: float) -> None:
        lam = prof.rate * density * dt / 3600.0
        k = self.update_throttle(t)
        self.deferred += lam * (1.0 - k)
        n = poisson(self.rng, lam * k)
        for _ in range(n):
            self.spawn_trip(t, prof)
        if self._ped_edges and prof.ped_rate > 0 and self.engine.person_count < self.MAX_PERSONS:
            for _ in range(poisson(self.rng, prof.ped_rate * dt / 3600.0)):
                self.spawn_pedestrian(prof)

    def spawn_trip(self, t: float, prof: "DemandProfile", vtype: str | None = None, tag: str | None = None) -> str | None:
        rng = self.rng
        vtype = vtype or self.pick_type(prof)
        for _attempt in range(4):
            if prof.generator_edges and rng.random() < prof.generator_share:
                o, o_int = rng.choice(prof.generator_edges), True
            else:
                o, o_int = self._end(prof.origin_w, prof.core_bias, True)
            if prof.attractor_edges and rng.random() < prof.attractor_share:
                d, d_int = rng.choice(prof.attractor_edges), True
            else:
                d, d_int = self._end(prof.dest_w, prof.core_bias, False)
            if not o or not d or o == d or self.net.reverse(o) == d:
                continue
            if tag is None and (self.crowded(o) or self.backlogged(t)):  # scenario-tagged trips are never deferred
                self.metered += 1
                continue
            r = self.route(o, d, vtype)
            if r is None:
                continue
            vid = f"v{self.counter}"
            self.counter += 1
            self.engine.add_vehicle(
                vid, r[0], vtype,
                depart_pos="random_free" if o_int else "base",
                depart_speed="0" if o_int else "desired",
                arrival_pos="random" if d_int else "max",
                tag=tag,
            )
            return vid
        self.failed += 1
        return None

    def spawn_pedestrian(self, prof: "DemandProfile") -> None:
        rng = self.rng
        T = self.engine.T
        if prof.ped_generator_edges and rng.random() < prof.ped_venue_share:
            a = rng.choice(prof.ped_generator_edges)
        else:
            a = rng.choice(self._ped_edges)
        if prof.ped_attractor_edges and rng.random() < prof.ped_venue_share:
            b = rng.choice(prof.ped_attractor_edges)
        else:
            b = rng.choice(self._ped_edges)
        if a == b:
            return
        try:
            stages = T.simulation.findIntermodalRoute(a, b, modes="", pType="walker")
        except Exception:  # noqa: BLE001
            return
        if not stages:
            return
        pid = f"p{self.ped_counter}"
        self.ped_counter += 1
        length = self.net.edges[a].length
        try:
            T.person.add(pid, a, rng.uniform(2.0, max(3.0, length - 2.0)), typeID="walker")
            for s in stages:
                T.person.appendStage(pid, s)
        except Exception:  # noqa: BLE001
            try:
                T.person.remove(pid)
            except Exception:  # noqa: BLE001
                pass
