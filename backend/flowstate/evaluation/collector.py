"""Congestion measurements read from a live SUMO engine (docs/CONGESTION_ACCEPTANCE.md).

Every number comes from SUMO: lane vehicle lists, positions, speeds, link states, the native departure/arrival/
teleport counters. Nothing is a configured demand multiplier. The collector is driven once per engine step and
samples once per simulated second (the acceptance contract's 1 Hz); it keeps the whole warm-up so the "was the
target already present at t=0" question can be answered.

Definitions (see also schema.py):

* density            motor vehicles on the scope's motor lanes / scope lane-km (empty lanes included)
* body occupancy     sum of vehicle body lengths clipped to the lane / lane metres
* storage use        sum of (length + native standstill gap) clipped to the lane / lane metres
* queue              a chain of vehicles stopped (< 1 m/s) whose head is within 15 m of the stop line and whose
                     successive gaps are <= 10 m, persisting >= 10 s (rolling minimum)
* blocked movement   the head vehicle of an approach is stopped, its next route edge is B, and every lane of B
                     reachable from the head lane has no room for it (the receiving lane "cannot accept"); logged
                     separately for the seconds in which the movement's signal is green
* blocked cycle      a strongly connected set of blocked movements spanning >= 4 junctions and >= 2 streets
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..sim.vtypes import MODE_OF, base_type
from .zone import Block, BlockKey, Zone

if TYPE_CHECKING:  # pragma: no cover
    from ..sim.engine import Engine

HEAD_WINDOW_M = 15.0  # the head of a queue is within this distance of the stop line
QUEUE_LINK_M = 10.0  # successive queued vehicles are at most this far apart (bumper to bumper)
QUEUE_SPEED = 1.0  # m/s below which a vehicle counts as queued
HALT_SPEED = 0.1  # SUMO's halting threshold
QUEUE_PERSIST_S = 10
STAND_S = 5.0  # both vehicles must have stood this long before a standstill gap is sampled
GAP_PERIOD_S = 5
ROUTE_PERIOD_S = 10
TRAVEL_MIN_S = 20.0  # a vehicle must have been in the scope this long for its travel speed to count


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))]


@dataclass
class _V:
    lane: str
    pos: float
    length: float
    gap: float
    speed: float
    vid: str
    motor: bool


@dataclass
class _BlockStat:
    extent: list[int] = field(default_factory=list)  # persisted queue extent per second, metres
    vehicles: list[int] = field(default_factory=list)
    recent: deque = field(default_factory=lambda: deque(maxlen=QUEUE_PERSIST_S))


class Collector:
    def __init__(self, eng: "Engine", zone: Zone, graph_zone: Zone, declared: set[BlockKey] | None = None, declared_zone: Zone | None = None):
        self.declared_zone = declared_zone
        self.eng = eng
        self.net = eng.net
        self.zone = zone
        self.graph = graph_zone
        self.declared = declared or set()
        self.zone_lanes = set(zone.lanes)
        extra = set(declared_zone.lanes) if declared_zone else set()
        extra |= {l for e in eng.net.edges.values() for l in eng.net.car_lanes(e.id)}  # the breakdown needs every motor lane
        self.lane_len = {l: self.net.lanes[l].length for l in set(zone.lanes) | set(graph_zone.lanes) | extra}
        self.lanes = sorted(set(zone.lanes) | set(graph_zone.lanes) | extra)
        # where the vehicles are: motor vehicles and lane-km by (edge kind, street class) over every motor lane
        self.group_of: dict[str, str] = {}
        for e in self.net.edges.values():
            for l in self.net.car_lanes(e.id):
                self.group_of[l] = f"{e.kind}:{e.cls or '-'}"
        self.group_km: dict[str, float] = defaultdict(float)
        for l, g in self.group_of.items():
            self.group_km[g] += self.net.lanes[l].length / 1000.0
        self.group_rows: list[dict[str, list[int]]] = []
        self.gb_arcs: list[list[tuple[BlockKey, BlockKey]]] = []  # per second: movements blocked by a full receiving lane while their signal is green
        self.snap0: tuple[dict[str, int], dict[str, int]] | None = None  # stop/start counters at t = 0
        self.zone_edge_set = set(zone.edges)
        self.rows: list[dict[str, Any]] = []
        self.block_stats: dict[BlockKey, _BlockStat] = {k: _BlockStat() for k in graph_zone.blocks}
        self._type_cache: dict[str, tuple[float, float]] = {}
        self._last_second = -10**9
        # stop-and-go bookkeeping (vehicle ids in the scope)
        self._state: dict[str, str] = {}
        self._stopped_since: dict[str, float] = {}
        self.stops: dict[str, int] = defaultdict(int)
        self.starts: dict[str, int] = defaultdict(int)
        self.gap_samples: list[float] = []
        self.gap_outliers: list[dict[str, Any]] = []
        # blocked movements
        self.green_blocked_seconds: dict[str, int] = defaultdict(int)
        self.blocked_seconds: dict[str, int] = defaultdict(int)
        self.corridor_blocked_seconds: dict[tuple[str, str], int] = defaultdict(int)
        self.cycle_seconds: list[dict[str, Any]] = []
        self._vehicle_block: dict[str, BlockKey | None] = {}
        self.declared_outflow: list[int] = []  # vehicles leaving the declared zone, per second
        self.declared_inflow: list[int] = []
        # per-step native counters
        self.steps = 0
        self.native = defaultdict(int)  # departed, arrived, teleport_started, teleport_ended, loaded (cumulative in the window)
        self.invalid_routes = 0
        self.route_samples = 0
        self.stranded_samples = 0
        self.max_nonfinite = 0
        # per-vehicle travel through the primary scope: first/last sighting and odometer, for travel speed
        self._track: dict[str, dict[str, list[float]]] = {"primary": {}, "declared": {}}  # scope -> vid -> [t_first, odo_first, t_last, odo_last]
        self.zone_trips: dict[str, list[tuple[float, float, float]]] = {"primary": [], "declared": []}  # (t_exit, dwell_s, distance_m)
        self.wait_forensics: list[dict[str, Any]] = []  # vehicles that have stood >= 180 s at the head of an approach (first 40)
        self._wait_seen: set[str] = set()
        self.tele_forensics: list[dict[str, Any]] = []  # situation of each vehicle SUMO starts to teleport (first 60)
        self.forensics: list[dict[str, Any]] = []  # situation at each new collision episode (first 40)
        self._episodes_seen = 0

    # -- helpers ---------------------------------------------------------
    def _type(self, vid: str) -> tuple[float, float]:
        tid = self.eng.vtype.get(vid)
        if tid is None:
            tid = self.eng.T.vehicle.getTypeID(vid)
            self.eng.vtype[vid] = tid
        v = self._type_cache.get(tid)
        if v is None:
            T = self.eng.T
            v = self._type_cache[tid] = (T.vehicletype.getLength(tid), T.vehicletype.getMinGap(tid))
        return v

    def _is_motor(self, vid: str) -> bool:
        tid = self.eng.vtype.get(vid) or ""
        return MODE_OF.get(base_type(tid), "car") != "bike"

    # -- per step --------------------------------------------------------
    def after_step(self) -> None:
        eng = self.eng
        T = eng.T
        self.steps += 1
        self.native["departed"] += T.simulation.getDepartedNumber()
        self.native["arrived"] += T.simulation.getArrivedNumber()
        self.native["teleport_started"] += T.simulation.getStartingTeleportNumber()
        self.native["teleport_ended"] += T.simulation.getEndingTeleportNumber()
        if T.simulation.getStartingTeleportNumber() and len(self.tele_forensics) < 60:
            for vid in T.simulation.getStartingTeleportIDList():
                self.tele_forensics.append(self._teleport_state(vid))
        if eng.safety.episodes > self._episodes_seen:
            self._episodes_seen = eng.safety.episodes
            self._snapshot_collision()
        sec = int(eng.t // 1.0)
        if sec != self._last_second and eng.t - sec < eng.dt + 1e-6:
            self._last_second = sec
            self.sample(float(sec))

    def _vehicle_state(self, vid: str) -> dict[str, Any]:
        T = self.eng.T
        out: dict[str, Any] = {"id": vid, "type": self.eng.vtype.get(vid)}
        try:
            route = list(T.vehicle.getRoute(vid))
            idx = T.vehicle.getRouteIndex(vid)
            out.update({
                "lane": T.vehicle.getLaneID(vid), "pos": round(T.vehicle.getLanePosition(vid), 2), "lat": round(T.vehicle.getLateralLanePosition(vid), 2),
                "speed": round(T.vehicle.getSpeed(vid), 2), "waiting": round(T.vehicle.getWaitingTime(vid), 1), "angle": round(T.vehicle.getAngle(vid), 1),
                "route": route[max(0, idx - 1): idx + 3], "length": T.vehicle.getLength(vid), "width": T.vehicle.getWidth(vid),
            })
            nxt = T.vehicle.getNextTLS(vid)
            if nxt:
                out["nextTls"] = {"id": nxt[0][0], "dist": round(nxt[0][2], 1), "state": nxt[0][3]}
            ld = T.vehicle.getLeader(vid, 30.0)
            if ld and ld[0]:
                out["leader"] = {"id": ld[0], "gap": round(ld[1], 2)}
        except Exception as exc:  # noqa: BLE001
            out["error"] = repr(exc)
        return out

    def _teleport_state(self, vid: str) -> dict[str, Any]:
        T = self.eng.T
        out = self._vehicle_state(vid)
        out["t"] = round(self.eng.t, 1)
        try:
            links = T.vehicle.getNextLinks(vid)
            # libsumo link tuple: (approachedLane, hasPrio, isOpen, hasFoe, viaLane, state, direction, length)
            out["nextLinks"] = [{"lane": l[0], "hasPrio": l[1], "isOpen": l[2], "hasFoe": l[3], "via": l[4], "state": l[5], "dir": l[6]} for l in links[:2]]
            lane = T.vehicle.getLaneID(vid)
            out["laneVehicles"] = T.lane.getLastStepVehicleNumber(lane)
            route = list(T.vehicle.getRoute(vid))
            idx = T.vehicle.getRouteIndex(vid)
            if idx + 1 < len(route):
                nxt = route[idx + 1]
                recv = [l[0] for l in T.lane.getLinks(lane) if l[0].rsplit("_", 1)[0] == nxt] if not lane.startswith(":") else []
                out["receiving"] = [{"lane": r, "vehicles": T.lane.getLastStepVehicleNumber(r), "meanSpeed": round(T.lane.getLastStepMeanSpeed(r), 2)} for r in recv]
        except Exception as exc:  # noqa: BLE001
            out["linkError"] = repr(exc)
        return out

    def _wait_state(self, vid: str, lane: str, lane_veh: dict[str, list[_V]]) -> dict[str, Any]:
        """What a vehicle that has stood for >= 180 s at the head of an approach is waiting for."""
        T = self.eng.T
        out = self._teleport_state(vid)
        out["headLane"] = lane
        out["signal"] = None
        try:
            node = self.net.edge_to_tls.get(self.net.lanes[lane].edge)
            if node and node in self.eng.tls_state:
                out["signal"] = {"junction": node, "state": self.eng.tls_state[node], "phase": self.eng.tls_phase.get(node)}
            foes = []
            for l in T.vehicle.getNextLinks(vid)[:2]:
                via = l[4]
                if via:
                    on_via = T.lane.getLastStepVehicleIDs(via)
                    foes.append({"via": via, "vehiclesOnInternalLane": [(v, round(T.vehicle.getSpeed(v), 2), T.vehicle.getTypeID(v)) for v in on_via[:4]]})
            out["internalLanes"] = foes
            out["pedestriansOnJunction"] = len([p for p in self.eng.persons])
        except Exception as exc:  # noqa: BLE001
            out["waitError"] = repr(exc)
        return out

    def _snapshot_collision(self) -> None:
        if len(self.forensics) >= 40 or not self.eng.safety.examples:
            return
        ex = self.eng.safety.examples[-1]
        snap = {"t": round(self.eng.t, 2), "type": ex["type"], "lane": ex["lane"], "collider": self._vehicle_state(ex["collider"]), "victim": self._vehicle_state(ex["victim"])}
        lane = ex["lane"]
        node = lane.split("_")[0][1:] if lane.startswith(":") else None
        if node and node in self.eng.tls_state:
            snap["tlsState"] = self.eng.tls_state[node]
            snap["tlsPhase"] = self.eng.tls_phase.get(node)
        self.forensics.append(snap)

    # -- per second ------------------------------------------------------
    def sample(self, t: float) -> None:
        eng, T = self.eng, self.eng.T
        lane_veh: dict[str, list[_V]] = {}
        for lane in self.lanes:
            ids = T.lane.getLastStepVehicleIDs(lane)
            if not ids:
                lane_veh[lane] = []
                continue
            out = []
            for vid in ids:
                length, gap = self._type(vid)
                sv = eng.vehicles.get(vid)
                speed = sv[3] if sv else T.vehicle.getSpeed(vid)
                out.append(_V(lane, T.vehicle.getLanePosition(vid), length, gap, speed, vid, self._is_motor(vid)))
            out.sort(key=lambda v: -v.pos)
            lane_veh[lane] = out

        row, zone_ids = self._scope(self.zone, lane_veh)
        row.update({"t": t, "active": len(eng.vehicles), "pending": len(T.simulation.getPendingVehicles()),
                    "teleports": eng.teleports, "collisions": eng.safety.records, "arrivedCum": self.native["arrived"], "departedCum": self.native["departed"]})
        dz_ids: list[_V] = []
        if self.declared_zone is not None:
            row["dz"], dz_ids = self._scope(self.declared_zone, lane_veh)
        self.rows.append(row)

        groups: dict[str, list[int]] = {}
        for lane, vs in lane_veh.items():
            g = self.group_of.get(lane)
            if g is None or not vs:
                continue
            slot = groups.setdefault(g, [0, 0])
            for v in vs:
                if v.motor:
                    slot[0] += 1
                    if v.speed < HALT_SPEED:
                        slot[1] += 1
        self.group_rows.append(groups)

        self._track_travel("primary", t, zone_ids)
        if self.declared_zone is not None:
            self._track_travel("declared", t, dz_ids)

        # ---- stop-and-go and standstill gaps ------------------------------
        for v in zone_ids:
            st = self._state.get(v.vid)
            if v.speed < HALT_SPEED:
                if st != "S":
                    if st == "M":
                        self.stops[v.vid] += 1
                    self._state[v.vid] = "S"
                    self._stopped_since[v.vid] = t
            elif v.speed > 2.0:
                if st == "S":
                    self.starts[v.vid] += 1
                    self._stopped_since.pop(v.vid, None)
                self._state[v.vid] = "M"
        if int(t) % GAP_PERIOD_S == 0:
            self._sample_gaps(t, zone_ids)

        # ---- queues per block, blocked movements, cycles ------------------
        arcs = self._blocked_movements(t, lane_veh)
        self.gb_arcs.append([(a_, b_) for a_, b_, _n, g in arcs if g])
        if self.snap0 is None and t >= 0:
            self.snap0 = (dict(self.stops), dict(self.starts))
        self._queues(t, lane_veh)
        self._cycles(t, arcs)
        self._declared_flow(t, lane_veh)
        if int(t) % ROUTE_PERIOD_S == 0:
            self._route_validity()
        self._forget(set(eng.vehicles))

    def _scope(self, zone: Zone, lane_veh: dict[str, list[_V]]) -> tuple[dict[str, Any], list[_V]]:
        """Density, occupancy, storage use and speed distribution of the motor vehicles on a scope's lanes."""
        total_m = sum(self.lane_len[l] for l in zone.lanes)
        n_motor = n_bike = 0
        body = foot = 0.0
        speeds: list[float] = []
        vs: list[_V] = []
        for lane in zone.lanes:
            L = self.lane_len[lane]
            for v in lane_veh.get(lane, ()):
                if not v.motor:
                    n_bike += 1
                    continue
                n_motor += 1
                body += max(0.0, min(v.pos, L) - max(v.pos - v.length, 0.0))
                foot += max(0.0, min(v.pos, L) - max(v.pos - v.length - v.gap, 0.0))
                speeds.append(v.speed)
                vs.append(v)
        kmh = [x * 3.6 for x in speeds]
        return {
            "motor": n_motor, "bikes": n_bike, "density": n_motor / (total_m / 1000.0) if total_m else 0.0,
            "body": body / total_m if total_m else 0.0, "storage": foot / total_m if total_m else 0.0,
            "p10": pct(kmh, 0.1), "p50": pct(kmh, 0.5), "p90": pct(kmh, 0.9),
            "halted": (sum(1 for x in speeds if x < HALT_SPEED) / len(speeds)) if speeds else 0.0,
        }, vs

    def _track_travel(self, scope: str, t: float, zone_ids: list[_V]) -> None:
        T = self.eng.T
        track = self._track[scope]
        for v in zone_ids:
            tr = track.get(v.vid)
            odo = T.vehicle.getDistance(v.vid)
            if tr is None:
                track[v.vid] = [t, odo, t, odo]
            else:
                tr[2], tr[3] = t, odo
        for vid in [k for k, tr in track.items() if t - tr[2] >= 5.0]:  # left the scope (not just crossing a junction)
            t0, d0, t1, d1 = track.pop(vid)
            if t1 - t0 >= TRAVEL_MIN_S:
                self.zone_trips[scope].append((t1, t1 - t0, d1 - d0))

    def travel_speeds_kmh(self, scope: str, lo: float, hi: float) -> list[float]:
        """Average speed of each vehicle through the scope: finished trips that ended in [lo, hi) plus vehicles still inside at ``hi``."""
        out = [d / dwell * 3.6 for t1, dwell, d in self.zone_trips[scope] if lo <= t1 < hi]
        out += [(tr[3] - tr[1]) / (tr[2] - tr[0]) * 3.6 for tr in self._track[scope].values() if tr[2] - tr[0] >= TRAVEL_MIN_S]
        return out

    # -- standstill following gaps ----------------------------------------
    def _sample_gaps(self, t: float, vs: list[_V]) -> None:
        T = self.eng.T
        for v in vs:
            since = self._stopped_since.get(v.vid)
            if since is None or t - since < STAND_S or v.speed >= HALT_SPEED:
                continue
            try:
                leader = T.vehicle.getLeader(v.vid, 30.0)
            except Exception:  # noqa: BLE001
                continue
            if not leader or not leader[0]:
                continue
            lid, gap = leader
            lsince = self._stopped_since.get(lid)
            if lsince is None or t - lsince < STAND_S or not self._is_motor(lid) or not self._is_motor(v.vid):
                continue
            try:
                same_lane = T.vehicle.getLaneID(v.vid) == T.vehicle.getLaneID(lid)
                lateral = abs(T.vehicle.getLateralLanePosition(v.vid) - T.vehicle.getLateralLanePosition(lid))
            except Exception:  # noqa: BLE001
                continue
            if not same_lane or lateral > 0.9:
                continue  # side-by-side or lane-change cases are not leader/follower pairs
            bumper = gap + self._type(v.vid)[1]  # SUMO's leader gap excludes the follower's minGap: add it back
            self.gap_samples.append(bumper)
            if bumper < 1.0 or bumper > 6.0:
                if len(self.gap_outliers) < 40:
                    self.gap_outliers.append({"t": t, "follower": v.vid, "leader": lid, "bumperGap": round(bumper, 2), "lane": v.lane})

    # -- queues -------------------------------------------------------------
    @staticmethod
    def _chain(vs: list[_V], lane_len: float) -> list[_V]:
        chain: list[_V] = []
        prev_rear = None
        for v in vs:
            if v.speed >= QUEUE_SPEED:
                break
            if prev_rear is None:
                if lane_len - v.pos > HEAD_WINDOW_M:
                    break
            elif prev_rear - v.pos > QUEUE_LINK_M:
                break
            chain.append(v)
            prev_rear = v.pos - v.length
        return chain

    def _queues(self, t: float, lane_veh: dict[str, list[_V]]) -> None:
        for key, block in self.graph.blocks.items():
            down = block.edges[-1]
            best = 0.0
            nveh = 0
            for lane in block.lanes[down]:
                chain = self._chain(lane_veh.get(lane, []), self.lane_len[lane])
                nveh += len(chain)
                if chain:
                    last = chain[-1]
                    best = max(best, self.lane_len[lane] - (last.pos - last.length))
            if len(block.edges) == 2:
                bay_len = self.net.edges[down].length
                if best >= bay_len - QUEUE_LINK_M:
                    main = block.edges[0]
                    cont = 0.0
                    for lane in block.lanes[main]:
                        chain = self._chain(lane_veh.get(lane, []), self.lane_len[lane])
                        nveh += len(chain)
                        if chain:
                            last = chain[-1]
                            cont = max(cont, self.lane_len[lane] - (last.pos - last.length))
                    best = bay_len + cont
            st = self.block_stats[key]
            st.recent.append(best)
            persisted = min(st.recent) if len(st.recent) == QUEUE_PERSIST_S else 0.0
            st.extent.append(int(persisted))
            st.vehicles.append(nveh)

    # -- blocked movements -------------------------------------------------
    def _blocked_movements(self, t: float, lane_veh: dict[str, list[_V]]) -> list[tuple[BlockKey, BlockKey, str, bool]]:
        eng, T, net = self.eng, self.eng.T, self.net
        arcs: list[tuple[BlockKey, BlockKey, str, bool]] = []
        seen_nodes: set[str] = set()
        for key, block in self.graph.blocks.items():
            down = block.edges[-1]
            node = block.to_node
            for lane in block.lanes[down]:
                vs = lane_veh.get(lane)
                if not vs:
                    continue
                head = vs[0]
                if head.speed >= 0.5 or self.lane_len[lane] - head.pos > HEAD_WINDOW_M:
                    continue
                if len(self.wait_forensics) < 40 and head.vid not in self._wait_seen and T.vehicle.getWaitingTime(head.vid) >= 180.0:
                    self._wait_seen.add(head.vid)
                    self.wait_forensics.append(self._wait_state(head.vid, lane, lane_veh))
                try:
                    route = T.vehicle.getRoute(head.vid)
                    idx = T.vehicle.getRouteIndex(head.vid)
                except Exception:  # noqa: BLE001
                    continue
                if idx + 1 >= len(route):
                    continue
                nxt = route[idx + 1]
                links = [l for l in T.lane.getLinks(lane) if l[0].rsplit("_", 1)[0] == nxt]
                if not links:
                    continue
                room = False
                recv_lane = links[0][0]
                for l in links:
                    rl = l[0]
                    rv = lane_veh.get(rl)
                    if rv is None:  # a receiving lane outside the graph scope (exit stub): treat as free
                        room = True
                        break
                    free = min((v.pos - v.length for v in rv), default=self.lane_len.get(rl, 1e9))
                    if free >= head.length + 1.0:
                        room = True
                        break
                if room:
                    continue
                to_key = self.graph.edge_block.get(nxt)
                if to_key is None:
                    continue
                green = False
                tls = net.edge_to_tls.get(down) or net.lane_to_tls.get(lane)
                if tls and tls in eng.tls_state:
                    state = eng.tls_state[tls]
                    for link in net.tls_links.get(tls, ()):
                        if link.from_lane == lane and link.to_lane.rsplit("_", 1)[0] == nxt and link.index < len(state) and state[link.index] in "Gg":
                            green = True
                            break
                arcs.append((key, to_key, node, green))
                if node not in seen_nodes:
                    seen_nodes.add(node)
                    self.blocked_seconds[node] += 1
                if green:
                    self.green_blocked_seconds[node] += 1
                    self.corridor_blocked_seconds[(key[0], key[1])] += 1
        return arcs

    def _cycles(self, t: float, arcs: list[tuple[BlockKey, BlockKey, str, bool]]) -> None:
        graph: dict[BlockKey, set[BlockKey]] = defaultdict(set)
        for a, b, _n, _g in arcs:
            graph[a].add(b)
        best: dict[str, Any] | None = None
        for comp in _sccs(graph):
            nodes = {self.graph.blocks[k].to_node for k in comp if k in self.graph.blocks}
            streets = {k[0] for k in comp}
            if len(comp) >= 2 and len(nodes) >= 4 and len(streets) >= 2:
                cand = {"t": t, "blocks": sorted(f"{k[0]}:{k[1]}{k[2]}" for k in comp), "junctions": sorted(nodes), "streets": sorted(streets)}
                if best is None or len(comp) > len(best["blocks"]):
                    best = cand
        any_scc = any(len(c) >= 2 for c in _sccs(graph))
        self.cycle_seconds.append({"t": t, "qualifying": best, "anyCycle": any_scc, "blockedMovements": len(arcs)})

    # -- declared zone flow --------------------------------------------------
    def _declared_flow(self, t: float, lane_veh: dict[str, list[_V]]) -> None:
        """Vehicles that left the declared zone's blocks this second (cut outflow), and vehicles that entered."""
        if not self.declared:
            return
        out = inn = 0
        current: dict[str, BlockKey | None] = {}
        for lane, vs in lane_veh.items():
            key = self.graph.edge_block.get(self.net.lanes[lane].edge)
            for v in vs:
                current[v.vid] = key
        for vid, key in current.items():
            prev = self._vehicle_block.get(vid, "new")
            if prev == "new":
                continue
            if prev in self.declared and key not in self.declared:
                out += 1
            elif key in self.declared and prev not in self.declared:
                inn += 1
        # vehicles that vanished (arrived/teleported out) while last seen in the zone left it too
        for vid, prev in list(self._vehicle_block.items()):
            if vid not in current and prev in self.declared and vid not in self.eng.vehicles:
                out += 1
        self._vehicle_block.update(current)
        for vid in [v for v in self._vehicle_block if v not in current and v not in self.eng.vehicles]:
            self._vehicle_block.pop(vid, None)
        self.declared_outflow.append(out)
        self.declared_inflow.append(inn)

    # -- routes ----------------------------------------------------------------
    def _route_validity(self) -> None:
        T, net = self.eng.T, self.net
        for vid in list(self.eng.vehicles):
            try:
                route = T.vehicle.getRoute(vid)
                idx = T.vehicle.getRouteIndex(vid)
            except Exception:  # noqa: BLE001
                continue
            self.route_samples += 1
            for a, b in zip(route[idx:], route[idx + 1:]):
                ea, eb = net.edges.get(a), net.edges.get(b)
                if ea is None or eb is None or ea.to != eb.frm:
                    self.invalid_routes += 1
                    break

    def _forget(self, alive: set[str]) -> None:
        if len(self._state) > 4 * max(1, len(alive)):
            for d in (self._state, self._stopped_since, self.stops, self.starts):
                for vid in [v for v in d if v not in alive]:
                    if d is self.stops or d is self.starts:
                        continue
                    d.pop(vid, None)

    # -- results -----------------------------------------------------------------
    def window(self, lo: float, hi: float) -> list[dict[str, Any]]:
        return [r for r in self.rows if lo <= r["t"] < hi]

    def summary_series(self) -> dict[str, list[Any]]:
        keys = ("t", "motor", "bikes", "density", "body", "storage", "p10", "p50", "p90", "halted", "active", "pending", "teleports", "collisions", "arrivedCum", "departedCum")
        return {k: [round(r[k], 4) if isinstance(r[k], float) else r[k] for r in self.rows] for k in keys}


def _sccs(graph: dict[Any, set[Any]]) -> list[list[Any]]:
    """Tarjan's strongly connected components (iterative)."""
    index: dict[Any, int] = {}
    low: dict[Any, int] = {}
    onstack: set[Any] = set()
    stack: list[Any] = []
    out: list[list[Any]] = []
    counter = 0
    nodes = set(graph) | {b for bs in graph.values() for b in bs}
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(graph.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        onstack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    onstack.add(nxt)
                    work.append((nxt, iter(graph.get(nxt, ()))))
                    advanced = True
                    break
                if nxt in onstack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    onstack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                out.append(comp)
    return out


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None
