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
    in ``deferred`` (vehicles) so results can state how much was held back;
  - **missed-turn recovery** (:meth:`DemandGenerator.unstick`): a vehicle that
    has stood for ``UNSTICK_WAIT_S`` on a lane with no connection to its next
    edge (it could not change into its turn lane through a bumper-to-bumper
    queue) re-plans from what its lane can reach, as a driver would.

Dense, sustained rush hour
--------------------------
A five-minute rush hour used to ramp up *during* the observation and fade
before its end, after a warm-up of ordinary traffic. Short ``rush_hour`` runs
(``SUSTAIN_MAX_S``) therefore hold the commuter peak from the first second of
the warm-up to the end of the run, and switch to the ``DENSE`` settings that
measurement showed to give congestion concentrated on the arterial corridors
that stays near a steady density instead of collapsing into gridlock:

* **route choice is static** – ``findRoute`` prices edges with the current
  congestion, so most origin–destination pairs (first requested after the
  district has jammed) were planned through side streets; a free-flow cost
  with collectors ×1.8 and locals ×3 and 8 s per junction keeps trips on the
  arterials;
* **either side of the street** – an interior trip end may be on either side
  of its street (the cheaper approach is used): with U-turns banned, a driver
  whose destination faces the wrong way otherwise circles the block through
  collectors and locals (+57 % throughput when allowed);
* **class-weighted gateways and trip ends** and a **side balance** that loads
  King, Queen and Main alike (see ``SIDE_BALANCE``);
* **path admission** – a trip is not released if one of its next
  ``PATH_LOOKAHEAD`` blocks is already at ``PATH_GATE`` of its storage (a
  navigation-equipped driver would not enter a jammed street). This is what
  holds the density steady; raising the gate adds vehicles but lowers
  throughput and increases teleports and collisions;
* per-gateway backlog limits (a full Main-south gateway must not silence the
  empty King and Queen gateways) with small limits on collector and local
  gateways, and a throttle driven by junctions with several backed-up
  approaches rather than by any one small block.

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
    GATE_BY_CLASS: dict[str, float] | None = None  # per street class override of GATE_FRACTION
    MAX_PENDING = 10  # SUMO insertion backlog above which no new trips are started (all gateways)
    PENDING_PER_GATEWAY = 0  # if > 0: instead, no new trip at a gateway that already has this many vehicles waiting to enter
    PENDING_BY_CLASS: dict[str, int] | None = None  # per street class override of PENDING_PER_GATEWAY
    MAX_PENDING_TOTAL = 60  # ... with a cap on the whole backlog, since SUMO discards vehicles pending > 180 s
    SPILL_FRACTION = 0.6  # block queue / jam capacity at which the queue has reached the upstream junction
    THROTTLE_SLOPE = 0.12  # generation-rate reduction per spilled-back block (or per risky junction)
    MIN_THROTTLE = 0.45
    THROTTLE_MODE = "block"  # "block": every spilled block counts; "junction": only junctions with >= RISK_BLOCKS spilled approaches
    RISK_BLOCKS = 3
    UNSTICK_WAIT_S: float | None = 20.0  # missed-turn recovery after this long stationary (None: off)
    UNSTICK_PERIOD_S = 5.0
    # Admission mode. "legacy": a refused attempt silently draws another origin (the original behaviour, kept for the
    # interactive scenarios). "open" and "accumulation" keep every request, with the origin and destination it was drawn
    # with, in an explicit external hold queue (vehicles waiting beyond the district boundary / to leave their garage)
    # and release it to SUMO in order; nothing is redrawn or discarded, and every request is counted:
    #   requests = submitted + dropped (no route) + cancelled (held longer than MAX_HOLD_S) + held
    # "open" releases at once; "accumulation" releases only while fewer than TARGET_ACTIVE motor vehicles are in the
    # district (peak spreading / queue storage outside the model), which holds an oversaturated district at a declared
    # accumulation instead of letting it collapse into gridlock.
    ADMISSION = "legacy"
    # Cruising for parking: a share of the trips that end at a curb inside the district first drive past a few other
    # blocks (seeded waypoints near the downtown core) before reaching their destination. Real downtowns carry a large
    # share of such circling traffic (published studies put it at 8-74 % of traffic, ~30 % typical). It loads the
    # interior streets with moving vehicles the way real traffic does, without raising the entry flow or stopping cars.
    CRUISE_SHARE = 0.0
    CRUISE_LEGS = (1, 3)
    TARGET_ACTIVE: int | None = None
    TARGET_SCHEDULE: list[list[float]] | None = None  # [[t, target], ...] from scenario second t on (demand relief / metering schedule)
    MAX_HOLD_S: float | None = None
    PENDING_PER_ORIGIN = 2  # vehicles SUMO may hold pending at one origin edge (bounds per-step insertion work only)
    SUSTAIN_PEAK = True  # hold the rush-hour peak profile through warm-up and observation of a short run
    SUSTAIN_MAX_S = 600.0  # ... for runs up to this length (longer runs keep the full ramp / peak / ease shape)
    GATEWAY_INDEX: dict[str, float] | None = None  # fringe gateway weight by class (default: signals.APPROACH_VOLUME_INDEX)
    END_INDEX: dict[str, float] | None = None  # relative weight of a street's frontage as an interior trip end, by class (None: uniform)
    SIDE_BALANCE = 0.0  # 0..1: blend of the seeded per-side fringe weights towards a capacity-proportional split
    ROUTE_COST_FACTOR: dict[str, float] | None = None  # None: SUMO findRoute; dict: static route cost factor by street class
    JUNCTION_DELAY_S = 8.0  # time charged for every junction a static route crosses (signals, turns)
    EITHER_SIDE_DESTINATIONS = False  # an interior trip end may be on either side of its street (the cheaper approach is used)
    PATH_GATE: float | None = None  # do not release a trip whose next PATH_LOOKAHEAD blocks include one at this storage occupancy
    PATH_LOOKAHEAD = 8

    # Settings of the dense, sustained rush hour (short rush_hour runs); each overrides the attribute of the same name.
    DENSE: dict[str, Any] = {
        "SIDE_BALANCE": 0.8,
        "GATEWAY_INDEX": {"arterial": 1.0, "arterial_bike": 1.0, "collector": 0.05, "local": 0.01},
        "END_INDEX": {"arterial": 1.0, "arterial_bike": 1.0, "collector": 0.1, "local": 0.02},
        "EITHER_SIDE_DESTINATIONS": True,
        "ROUTE_COST_FACTOR": {"arterial": 1.0, "arterial_bike": 1.0, "collector": 1.8, "local": 3.0},
        "THROTTLE_MODE": "junction",
        "RISK_BLOCKS": 2,
        "SPILL_FRACTION": 0.7,
        "THROTTLE_SLOPE": 0.2,
        "MIN_THROTTLE": 0.35,
        "GATE_FRACTION": 0.7,
        "GATE_BY_CLASS": {"arterial": 0.7, "arterial_bike": 0.7, "collector": 0.35, "local": 0.2},
        "PENDING_PER_GATEWAY": 3,
        "PENDING_BY_CLASS": {"arterial": 3, "arterial_bike": 3, "collector": 1, "local": 1},
        "PATH_GATE": 0.6,
    }

    def __init__(self, engine: "Engine", rng: random.Random, profiles: dict[str, list[str]], overrides: dict[str, Any] | None = None):
        self.engine = engine
        self.net: "NetInfo" = engine.net
        self.rng = rng
        self.profiles = profiles
        self.counter = 0
        self.ped_counter = 0
        self.failed = 0
        # Integer ledger of *background* demand (scenario-tagged vehicles are counted separately in Engine.added):
        #   requests = submitted + dropped + held   (held is always 0 in this generator: a refused attempt draws again)
        self.requests = 0  # arrivals sampled from the profile (integers, before any guardrail)
        self.submitted = 0  # background requests handed to SUMO as vehicles
        self.dropped = 0  # background requests that found no admissible origin/route in 4 attempts (explicit loss)
        self.cruised = 0  # trips that circle for parking before arriving
        self.cancelled = 0  # held requests given up after MAX_HOLD_S (explicit, counted)
        self.held: list[dict[str, Any]] = []  # external hold queue, oldest first
        self.hold_waits: list[float] = []  # seconds each released request waited outside
        self._origin_of: dict[str, str] = {}  # vehicle id -> origin edge, for the per-origin pending count
        self.metered = 0  # trip starts refused (full gateway, or a jammed path); another origin is drawn
        self.deferred = 0.0  # expected trips held back by the throttle (vehicles)
        self.offered = 0.0  # expected trips requested by the scenario profile (vehicles), before any guardrail
        self.replanned = 0  # vehicles that gave up a turn they could not reach and re-planned
        self.throttle = 1.0
        self._throttle_t = -1e9
        self._pending = 0
        self._pending_at: dict[str, int] = {}
        self._pending_t = -1e9
        self._unstick_t = -1e9
        self._route_n = 0
        self._router = None
        self._edge_len: dict[str, float] = {}
        self._route_edges: dict[str, list[str]] = {}
        self._route_cache: dict[tuple[str, str, str, bool], tuple[str, float] | None] = {}
        self._ped_edges = [e for e in self.net.street_edges if any(self.net.lanes[l].allows_ped for l in self.net.edges[e].lanes)]
        self._zones = [z for z in self.net.zones if z.edges]
        self._gate_cap: dict[str, float] = {}
        self._segment: dict[str, list[str]] = {}
        blocks: dict[tuple[str, str, int], list[str]] = {}
        for e in self.net.edges.values():
            if e.kind in ("street", "entry") and e.street:
                blocks.setdefault((e.street, e.dir, e.seg), []).append(e.id)
        self._blocks = list(blocks.values())
        self._junction_blocks: dict[str, list[list[str]]] = {}
        for block in self._blocks:
            node = self.net.edges[block[0]].to
            if node in self.net.tls_ids:
                self._junction_blocks.setdefault(node, []).append(block)
        self._peak_window = self._rush_peak_window()
        self.dense = self._peak_window is not None
        if self.dense:
            for k, v in self.DENSE.items():
                setattr(self, k, v)
        for k, v in (overrides or {}).items():  # experiment overrides win over every default
            if not hasattr(self, k):
                raise AttributeError(f"unknown demand setting '{k}'")
            setattr(self, k, v)
        gw = self.GATEWAY_INDEX or APPROACH_VOLUME_INDEX
        self._gateway_w: dict[str, float] = {e: gw.get(self.net.edges[e].cls, 0.12) for e in self.net.entry_edges + self.net.exit_edges}

    # -- trip ends ---------------------------------------------------------
    def _side_edge(self, side: str, entry: bool) -> str | None:
        lst = (self.net.entries if entry else self.net.exits).get(side, [])
        if not lst:
            return None
        return weighted(self.rng, lst, [self._gateway_w.get(e, 0.12) for e in lst])

    def _rush_peak_window(self) -> tuple[float, float] | None:
        """(start, end) of the commuter plateau when this is a short rush-hour run whose peak should be sustained."""
        sc = self.engine.scenario
        if not self.SUSTAIN_PEAK or sc.spec.id != "rush_hour" or not sc.duration or sc.duration > self.SUSTAIN_MAX_S:
            return None
        for c in sc.components:
            if getattr(c, "name", "") == "rush_hour" and hasattr(c, "plateau"):
                return c.start + c.ramp, c.start + c.ramp + c.plateau
        return None

    def profile(self, t: float, prof: "DemandProfile") -> "DemandProfile":
        """The demand profile in force at ``t``.

        A rush hour scaled into a short run would ramp up *during* the observation and fade out before it ends,
        leaving the warm-up empty. For short runs the commuter peak is therefore held from the very start of the
        warm-up until the end, so the network is loaded before the observation and stays loaded throughout.
        """
        if self._peak_window is None:
            return prof
        lo, hi = self._peak_window
        if not lo <= t <= hi:
            prof = self.engine.scenario.demand(min(max(t, lo), hi))
        if self.SIDE_BALANCE > 0:
            prof = self._balanced(prof)
        return prof

    def _balanced(self, prof: "DemandProfile") -> "DemandProfile":
        """Temper the dominant compass side and load every arterial gateway about equally.

        Each side's weight is blended towards ``mean x (side's total gateway weight / mean of those)``: a side
        with two arterial gateways (west/east here) carries twice the trips of one with a single arterial
        (north/south), so King, Queen and Main are loaded alike instead of the lone north-south arterial
        taking its whole side.
        """
        a = self.SIDE_BALANCE
        cap = {side: sum(self._gateway_w.get(e, 0.0) for e in self.net.entries.get(side, [])) for side in self.net.entries}
        mean_cap = sum(cap.values()) / max(1, len(cap))
        for name in ("origin_w", "dest_w"):
            w = dict(getattr(prof, name))
            sides = [k for k in w if k != "internal"]
            mean = sum(w[k] for k in sides) / len(sides)
            for k in sides:
                target = mean * (cap.get(k, mean_cap) / mean_cap if mean_cap else 1.0)
                w[k] = (1 - a) * w[k] + a * target
            setattr(prof, name, w)
        return prof

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

    def unstick(self, t: float) -> None:
        """Missed-turn recovery.

        In a bumper-to-bumper queue a vehicle may be unable to change into its turn lane. It then stands at the end of
        a lane that has no connection to its next edge and blocks everyone behind it indefinitely (SUMO eventually
        teleports it). A driver in that position gives up the turn and goes on: the route is re-planned from the
        edges the lane *can* reach.
        """
        if self.UNSTICK_WAIT_S is None or t - self._unstick_t < self.UNSTICK_PERIOD_S:
            return
        self._unstick_t = t
        T = self.engine.T
        for vid in T.vehicle.getIDList():
            if T.vehicle.getWaitingTime(vid) < self.UNSTICK_WAIT_S or vid in self.engine.tracked or T.vehicle.getTypeID(vid) == "wreck":
                continue
            try:
                lane = T.vehicle.getLaneID(vid)
                route = list(T.vehicle.getRoute(vid))
                i = T.vehicle.getRouteIndex(vid)
                if lane.startswith(":"):  # on a junction connector: what matters is the lane it feeds
                    links = T.lane.getLinks(lane)
                    if not links:
                        continue
                    feeds = links[0][0]
                    nxt_edge = feeds.rsplit("_", 1)[0]
                    prefix = [route[i]] + ([nxt_edge] if nxt_edge != route[i] else [])  # setRoute starts at the current edge
                    after = route[i + 2] if nxt_edge != route[i] and i + 2 < len(route) else None
                    from_lane = feeds
                else:
                    prefix = [route[i]]
                    after = route[i + 1] if i + 1 < len(route) else None
                    from_lane = lane
                if after is None:
                    continue
                reach = {l[0].rsplit("_", 1)[0] for l in T.lane.getLinks(from_lane)}
                if not reach or after in reach:
                    continue  # the lane does connect to where the vehicle wants to go
                dest = route[-1]
                vtype = T.vehicle.getTypeID(vid)
                best = None
                for e1 in reach:
                    r = T.simulation.findRoute(e1, dest, vType=vtype)
                    if r is not None and r.edges and (best is None or r.travelTime < best.travelTime):
                        best = r
                if best is None:
                    continue
                T.vehicle.setRoute(vid, prefix + list(best.edges))
                self.replanned += 1
            except Exception as exc:  # noqa: BLE001 - never let recovery break the run
                import logging

                logging.getLogger(__name__).debug("unstick %s failed: %r", vid, exc)
                continue

    def block_occupancy(self, edge: str) -> float:
        """Vehicles on the block of ``edge`` (main + bay parts) as a share of its jam storage."""
        stats = self.engine.edge_stats
        n = sum(stats[e][0] for e in self._seg(edge) if e in stats)
        return n / self._capacity(edge)

    def path_jammed(self, edges: list[str]) -> bool:
        gate = self.PATH_GATE
        if gate is None:
            return False
        seen: set[str] = set()
        for e in edges[: self.PATH_LOOKAHEAD]:
            if e not in self.net.edges or self.net.edges[e].kind not in ("street", "entry") or e in seen:
                continue
            seen.update(self._seg(e))
            if self.block_occupancy(e) >= gate:
                return True
        return False

    def crowded(self, edge: str) -> bool:
        """True when the approach queue on ``edge`` (and the blocks upstream of it) is nearly full."""
        stats = self.engine.edge_stats
        halting = sum(stats[e][1] for e in self._seg(edge) if e in stats)
        frac = (self.GATE_BY_CLASS or {}).get(self.net.edges[edge].cls, self.GATE_FRACTION)
        return halting >= frac * self._capacity(edge)

    def update_throttle(self, t: float) -> float:
        """Recompute (at 1 Hz) the generation-rate factor from the number of spilled-back blocks."""
        if t - self._throttle_t >= 1.0:
            self._throttle_t = t
            stats = self.engine.edge_stats

            def spilled(block: list[str]) -> bool:
                halting = sum(stats[e][1] for e in block if e in stats)
                return bool(halting) and halting >= self.SPILL_FRACTION * self._capacity(block[0])

            if self.THROTTLE_MODE == "junction":  # gridlock needs a junction whose approaches are all backed up
                risk = sum(1 for blocks in self._junction_blocks.values() if sum(spilled(b) for b in blocks) >= self.RISK_BLOCKS)
            else:
                risk = sum(1 for block in self._blocks if spilled(block))
            self.throttle = max(self.MIN_THROTTLE, 1.0 - self.THROTTLE_SLOPE * risk)
        return self.throttle

    def backlogged(self, t: float, origin: str | None = None) -> bool:
        """True when SUMO cannot insert more vehicles (globally, or at this gateway's edge)."""
        if t - self._pending_t >= 1.0:
            self._pending_t = t
            ids = self.engine.T.simulation.getPendingVehicles()
            self._pending = len(ids)
            self._pending_at = {}
            if self.PENDING_PER_GATEWAY:
                for vid in ids:
                    try:
                        first = self.engine.T.vehicle.getRoute(vid)[0]
                    except Exception:  # noqa: BLE001
                        continue
                    self._pending_at[first] = self._pending_at.get(first, 0) + 1
        if self.PENDING_PER_GATEWAY:
            limit = (self.PENDING_BY_CLASS or {}).get(self.net.edges[origin].cls, self.PENDING_PER_GATEWAY) if origin is not None else self.PENDING_PER_GATEWAY
            return self._pending > self.MAX_PENDING_TOTAL or (origin is not None and self._pending_at.get(origin, 0) >= limit)
        return self._pending > self.MAX_PENDING

    def _zone_edge(self, core_bias: float) -> str:
        z = weighted(self.rng, self._zones, [core_bias if z.core else 1.0 for z in self._zones])
        if self.END_INDEX is None:
            return self.rng.choice(z.edges)
        return weighted(self.rng, z.edges, [self.net.edges[e].length * self.END_INDEX.get(self.net.edges[e].cls, 1.0) for e in z.edges])

    def _end(self, weights: dict[str, float], core_bias: float, entry: bool) -> tuple[str | None, bool]:
        keys = list(weights)
        k = weighted(self.rng, keys, [weights[x] for x in keys])
        if k == "internal":
            return self._zone_edge(core_bias), True
        return self._side_edge(k, entry), False

    # -- routing -----------------------------------------------------------
    def _static_path(self, o: str, d: str, vtype: str) -> tuple[list[str], float] | None:
        """Cheapest path under a *static* cost (free-flow time x class factor + a per-junction delay).

        ``findRoute`` prices edges with the current congestion, so an origin-destination pair first requested
        once the district is jammed is planned around the arterials and through side streets. A static cost makes
        the route independent of when the pair first appears, and lets collectors and locals be unattractive.
        """
        if self._router is None:
            import sumolib

            from .. import sumo_env  # noqa: F401

            net = sumolib.net.readNet(str(self.net.path))
            self._edge_len = {}
            for e in net.getEdges():
                f = self.ROUTE_COST_FACTOR.get(e.getType(), 1.0)
                self._edge_len[e.getID()] = e.getLength()
                e._speed = e.getSpeed() / f
                e._length = e.getLength() + self.JUNCTION_DELAY_S * e._speed  # cost = length / speed = time*f + delay
            self._router = net
        net = self._router
        try:
            vclass = self.engine.T.vehicletype.getVehicleClass(vtype)
            path, _ = net.getOptimalPath(net.getEdge(o), net.getEdge(d), fastest=True, vClass=vclass)
        except Exception:  # noqa: BLE001
            return None
        if not path:
            return None
        ids = [e.getID() for e in path]
        return ids, sum(self._edge_len[i] for i in ids)

    def route(self, o: str, d: str, vtype: str, static: bool = False) -> tuple[str, float] | None:
        vclass_key = vtype.split("@")[0]
        static = static and self.ROUTE_COST_FACTOR is not None
        key = (o, d, vclass_key, static)
        if key in self._route_cache:
            return self._route_cache[key]
        T = self.engine.T
        edges: list[str] = []
        length = 0.0
        if static:
            found = self._static_path(o, d, vtype)
            if found:
                edges, length = found
        else:
            try:
                r = T.simulation.findRoute(o, d, vType=vtype)
            except Exception:  # noqa: BLE001
                r = None
            if r is not None and r.edges:
                edges, length = list(r.edges), r.length
        out = None
        if edges and length >= self.MIN_TRIP_M:
            self._route_n += 1
            rid = f"r{self._route_n}"
            T.route.add(rid, edges)
            out = (rid, length)
            self._route_edges[rid] = edges
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
        self.unstick(t)
        prof = self.profile(t, prof)
        lam = prof.rate * density * dt / 3600.0
        queued = self.ADMISSION != "legacy"
        k = 1.0 if queued else self.update_throttle(t)
        self.offered += lam
        self.deferred += lam * (1.0 - k)
        n = poisson(self.rng, lam * k)
        self.requests += n
        for _ in range(n):
            if queued:
                self._request(t, prof)
            else:
                self.spawn_trip(t, prof)
        if queued:
            self._release(t)
        if self._ped_edges and prof.ped_rate > 0 and self.engine.person_count < self.MAX_PERSONS:
            for _ in range(poisson(self.rng, prof.ped_rate * dt / 3600.0)):
                self.spawn_pedestrian(prof)

    # -- external hold queue (ADMISSION "open" / "accumulation") ------------------
    def _draw_od(self, prof: "DemandProfile", vtype: str):
        """Draw an origin, a destination and a route, exactly as spawn_trip does. Returns None when no route exists."""
        rng = self.rng
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
            r = self.route(o, d, vtype, static=True)
            if self.EITHER_SIDE_DESTINATIONS and d_int:
                other = self.net.reverse(d)
                if other and other in self.net.edges and self.net.edges[other].kind == "street":
                    r2 = self.route(o, other, vtype, static=True)
                    if r2 is not None and (r is None or r2[1] < r[1] - 1e-6):
                        r, d = r2, other
            if r is None:
                continue
            return o, o_int, d, d_int, r
        return None

    def _request(self, t: float, prof: "DemandProfile") -> None:
        vtype = self.pick_type(prof)
        od = self._draw_od(prof, vtype)
        if od is None:
            self.dropped += 1  # no routable origin-destination pair in 4 draws: an explicit loss, counted
            return
        o, o_int, d, d_int, r = od
        if d_int and self.CRUISE_SHARE > 0 and self.rng.random() < self.CRUISE_SHARE:
            r = self._cruise(o, d, vtype, r)
        self.held.append({"t": t, "o": o, "o_int": o_int, "d_int": d_int, "route": r[0], "vtype": vtype})

    def _cruise(self, o: str, d: str, vtype: str, direct: tuple[str, float]) -> tuple[str, float]:
        """Route o -> w1 [-> w2 ...] -> d through seeded waypoint edges; falls back to the direct route if any leg is missing."""
        lo, hi = self.CRUISE_LEGS
        legs = self.rng.randint(lo, hi)
        stops = [o]
        for _ in range(legs):
            w = self._zone_edge(1.5)
            if w != stops[-1]:
                stops.append(w)
        stops.append(d)
        edges: list[str] = []
        total = 0.0
        for a, b in zip(stops, stops[1:]):
            leg = self.route(a, b, vtype, static=True)
            if leg is None:
                return direct
            e = self._route_edges[leg[0]]
            edges.extend(e if not edges else (e[1:] if e and e[0] == edges[-1] else e))
            total += leg[1]
        if len(edges) < 2:
            return direct
        self._route_n += 1
        rid = f"r{self._route_n}"
        try:
            self.engine.T.route.add(rid, edges)
        except Exception:  # noqa: BLE001 - a waypoint chain SUMO cannot connect: keep the direct trip
            return direct
        self._route_edges[rid] = edges
        self.cruised += 1
        return rid, total

    def _active_motor(self) -> int:
        return len(self.engine.vehicles)

    def _release(self, t: float) -> None:
        if not self.held:
            return
        if self.MAX_HOLD_S is not None:
            keep = [h for h in self.held if t - h["t"] <= self.MAX_HOLD_S]
            self.cancelled += len(self.held) - len(keep)
            self.held = keep
        slots = 10**9
        if self.ADMISSION == "accumulation" and self.TARGET_ACTIVE is not None:
            slots = self.target_active(t) - self._active_motor() - len(self.engine.T.simulation.getPendingVehicles())
        if slots <= 0:
            return
        pending_at: dict[str, int] = {}
        for vid in self.engine.T.simulation.getPendingVehicles():
            o = self._origin_of.get(vid)
            if o is not None:
                pending_at[o] = pending_at.get(o, 0) + 1
        rest: list[dict[str, Any]] = []
        for h in self.held:  # oldest first: FIFO per origin, and the longest-waiting request goes first overall
            if slots > 0 and pending_at.get(h["o"], 0) < self.PENDING_PER_ORIGIN:
                self._insert(t, h)
                pending_at[h["o"]] = pending_at.get(h["o"], 0) + 1
                slots -= 1
            else:
                rest.append(h)
        self.held = rest

    def target_active(self, t: float) -> int:
        """The accumulation the district is held at, following TARGET_SCHEDULE when one is declared."""
        target = self.TARGET_ACTIVE
        for ts, n in (self.TARGET_SCHEDULE or ()):
            if t >= ts:
                target = int(n)
        return target

    def _insert(self, t: float, h: dict[str, Any]) -> None:
        vid = f"v{self.counter}"
        self.counter += 1
        if not self.engine.add_vehicle(vid, h["route"], h["vtype"], depart_pos="random_free" if h["o_int"] else "base",
                                       depart_speed="0" if h["o_int"] else "desired", arrival_pos="random" if h["d_int"] else "max"):
            self.dropped += 1  # SUMO refused the planned route: counted, not hidden
            return
        self.submitted += 1
        self._origin_of[vid] = h["o"]
        self.hold_waits.append(t - h["t"])

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
            if tag is None and (self.crowded(o) or self.backlogged(t, o)):  # scenario-tagged trips are never deferred
                self.metered += 1
                continue
            r = self.route(o, d, vtype, static=True)
            if self.EITHER_SIDE_DESTINATIONS and d_int:
                # parking on the near side avoids circling the block (U-turns are not allowed)
                other = self.net.reverse(d)
                if other and other in self.net.edges and self.net.edges[other].kind == "street":
                    r2 = self.route(o, other, vtype, static=True)
                    if r2 is not None and (r is None or r2[1] < r[1] - 1e-6):
                        r, d = r2, other
            if r is None:
                continue
            if tag is None and self.path_jammed(self._route_edges.get(r[0], [])):
                self.metered += 1
                continue
            vid = f"v{self.counter}"
            self.counter += 1
            if not self.engine.add_vehicle(
                vid, r[0], vtype,
                depart_pos="random_free" if o_int else "base",
                depart_speed="0" if o_int else "desired",
                arrival_pos="random" if d_int else "max",
                tag=tag,
            ):
                continue  # SUMO refused the planned route (lanes closed since): draw again
            if tag is None:
                self.submitted += 1
            return vid
        self.failed += 1
        if tag is None:
            self.dropped += 1
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
