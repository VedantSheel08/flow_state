"""Run a scenario in real SUMO and audit it step by step (shared by the scenario tests).

The audit watches what SUMO actually does, not what the scenario intends: collisions, teleports, bicycles on lanes
that do not permit them, vehicles that sit still for a long time, and the roads every vehicle drove on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from flowstate.api.service import NETWORKS_DIR, WORK_DIR
from flowstate.scenarios.base import Recipe, Scenario
from flowstate.scenarios.catalog import CATALOG, create
from flowstate.sim.engine import Engine, RunConfig

OWNED_PREFIXES = ("resp", "amb", "police", "suspect", "inc", "pickup", "bus")
EMERGENCY_PREFIXES = ("resp", "amb", "police")


def is_owned(eng: Engine, vid: str) -> bool:
    """Vehicles the scenario itself created (responders, suspect, wrecks, pick-ups, buses)."""
    return vid in eng.tracked or vid.startswith(OWNED_PREFIXES)


@dataclass
class Audit:
    scenario: str
    seed: int
    duration: float
    collisions: list[tuple[float, list[str]]] = field(default_factory=list)
    teleports: list[tuple[float, list[str]]] = field(default_factory=list)
    bike_violations: dict[str, set[str]] = field(default_factory=dict)
    max_wait: dict[str, float] = field(default_factory=dict)  # scenario-owned vehicles only
    roads: dict[str, set[str]] = field(default_factory=dict)  # vehicle -> roads it was on
    results: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[float, str, str]] = field(default_factory=list)
    tracked: list[dict[str, Any]] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def owned_teleports(self) -> list[str]:
        return [v for _, ids in self.teleports for v in ids if v.startswith(OWNED_PREFIXES)]

    @property
    def background_teleports(self) -> int:
        return sum(1 for _, ids in self.teleports for v in ids if not v.startswith(OWNED_PREFIXES))

    @property
    def wreck_collisions(self) -> list[tuple[float, list[str]]]:
        return [(t, ids) for t, ids in self.collisions if any(v.startswith("inc") for v in ids)]

    @property
    def emergency_collisions(self) -> list[tuple[float, list[str]]]:
        return [(t, ids) for t, ids in self.collisions if any(v.startswith(EMERGENCY_PREFIXES) for v in ids)]


def build_engine(scenario: Scenario, seed: int, label: str) -> Engine:
    cfg = RunConfig(scenario.spec.id, scenario.params, seed, scenario.params.get("signals", "fixed"), label=label)
    return Engine(scenario, cfg, NETWORKS_DIR, WORK_DIR / f"test_{label}")


def custom_scenario(components: Callable[[], list], params: dict[str, Any] | None = None, base: str = "disruption", demand: float = 1500) -> Scenario:
    """A one-off scenario made of the given components, validated against an existing scenario's parameters."""
    spec = CATALOG[base][0]
    return Scenario(spec, lambda sc: Recipe(demand, components()), {"duration": 10, **(params or {})})


def run_audit(sid: str | Scenario, seed: int, params: dict[str, Any] | None = None, *, duration: int | None = None,
              on_step: Callable[[Engine, "Audit"], None] | None = None, label: str | None = None) -> Audit:
    if isinstance(sid, Scenario):
        sc, name = sid, sid.spec.id
    else:
        p = dict(params or {})
        if duration is not None:
            p["duration"] = duration
        sc, name = create(sid, p), sid
    eng = build_engine(sc, seed, label or f"audit_{name}_{seed}")
    audit = Audit(name, seed, sc.duration or 0.0)
    lane_ok: dict[str, bool] = {}
    eng.start()
    try:
        T = eng.T
        while not eng.finished:
            eng.step()
            t = eng.t
            col = T.simulation.getCollidingVehiclesIDList()
            if col:
                audit.collisions.append((round(t, 2), list(col)))
            tele = T.simulation.getStartingTeleportIDList()
            if tele:
                audit.teleports.append((round(t, 2), list(tele)))
            for vid in eng.vehicles:
                road = T.vehicle.getRoadID(vid)
                audit.roads.setdefault(vid, set()).add(road)
                if eng.vtype.get(vid, "").startswith("bike"):
                    lane = T.vehicle.getLaneID(vid)
                    ok = lane_ok.get(lane)
                    if ok is None:
                        ok = lane_ok[lane] = "bicycle" in T.lane.getAllowed(lane)
                    if not ok:
                        audit.bike_violations.setdefault(vid, set()).add(lane)
                if is_owned(eng, vid) and int(t * 4) % 20 == 0:  # every 5 s
                    w = T.vehicle.getWaitingTime(vid)
                    if w > audit.max_wait.get(vid, 0.0):
                        audit.max_wait[vid] = w
            if on_step is not None:
                on_step(eng, audit)
        res = eng.results()
        audit.results = res["scenario"]
        audit.tracked = res["tracked"]
        audit.incidents = res["incidents"]
        audit.events = [(e.t, e.category, e.message) for e in eng.events._events]
        audit.extra = {"kpi": res["metrics"], "collisions": res["collisions"], "teleports": res["teleports"], "signals": res["signals"]}
    finally:
        eng.close()
    return audit


def instantiate(sid: str, seed: int, params: dict[str, Any] | None = None):
    """Draw a scenario instance without starting SUMO."""
    from flowstate.network.info import load_net_info

    sc = create(sid, params or {})
    net = load_net_info(str(NETWORKS_DIR / f"{sc.network_id()}.net.xml"), sc.network_id())
    sc.instantiate(seed, net)
    return sc, net
