"""Scenario framework.

A scenario is a *recipe*: a demand model plus a list of stochastic
:class:`Component` generators, parameterised by validated user settings.
Creating a run draws a concrete *instance* from the recipe using the run seed —
the accident location and severity, the peak intensity, the dispatch times… —
so each seed gives a different but exactly reproducible situation. Nothing
about the outcome is scripted: components only change SUMO inputs (demand,
lane permissions, speed limits, vehicles, driver parameters) and SUMO decides
what happens.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel

from ..sim.vtypes import Conditions

if TYPE_CHECKING:  # pragma: no cover
    from ..network.info import NetInfo
    from ..sim.engine import Engine, Tracked

ParamType = Literal["int", "float", "select", "bool", "edge"]


class ParamSpec(BaseModel):
    key: str
    label: str
    type: ParamType
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[dict[str, Any]] | None = None
    unit: str | None = None
    description: str | None = None
    live: bool = False
    group: str = "Scenario"


class Marker(BaseModel):
    t: float
    label: str
    kind: str = "event"  # event | incident | emergency | demand | transit


class BriefingItem(BaseModel):
    label: str
    value: str
    detail: str | None = None


class ScenarioSpec(BaseModel):
    id: str
    title: str
    tagline: str
    description: str
    category: str
    params: list[ParamSpec]
    networks: list[str] = ["district"]
    measures: list[str] = []
    assumptions: list[str] = []


# ---------------------------------------------------------------------------
# Demand
# ---------------------------------------------------------------------------
@dataclass
class DemandProfile:
    """Instantaneous demand. ``rate`` is veh/h before the traffic-level multiplier."""

    rate: float
    origin_w: dict[str, float] = field(default_factory=lambda: {"W": 1, "E": 1, "N": 1, "S": 1, "internal": 1.6})
    dest_w: dict[str, float] = field(default_factory=lambda: {"W": 1, "E": 1, "N": 1, "S": 1, "internal": 1.6})
    core_bias: float = 1.5
    mix: dict[str, float] = field(default_factory=dict)
    truck_share: float = 0.05
    bike_share: float = 0.05
    ped_rate: float = 900.0
    attractor_edges: list[str] = field(default_factory=list)
    attractor_share: float = 0.0
    generator_edges: list[str] = field(default_factory=list)
    generator_share: float = 0.0
    ped_attractor_edges: list[str] = field(default_factory=list)
    ped_generator_edges: list[str] = field(default_factory=list)
    ped_venue_share: float = 0.0


def protected_edges(net: "NetInfo") -> set[str]:
    """Streets that serve the hospital and police station: never closed, so responders can always leave."""
    out: set[str] = set()
    for poi in net.pois.values():
        if poi.kind in ("hospital", "police"):
            for e in poi.access:
                if e in net.edges:
                    out.update(net.street_segment(e))
    return out


def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def bell(t: float, start: float, peak_start: float, peak_end: float, end: float) -> float:
    if t <= start or t >= end:
        return 0.0
    if t < peak_start:
        return smoothstep((t - start) / max(1.0, peak_start - start))
    if t <= peak_end:
        return 1.0
    return 1.0 - smoothstep((t - peak_end) / max(1.0, end - peak_end))


def fmt_clock(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60:02d}:{t % 60:02d}"


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
class Component:
    """A stochastic process that shapes demand or changes simulation state."""

    name = "component"
    #: Lower values are drawn first. Components that need a fixed place (emergency calls, demand spikes) go
    #: before the ones that close streets, so a closure is never drawn on top of them.
    instantiate_order = 1

    def instantiate(self, rng: random.Random, sc: "Scenario", net: "NetInfo") -> None:
        """Draw the concrete instance values (once per run, before SUMO starts)."""

    def briefing(self) -> list[BriefingItem]:
        return []

    def markers(self) -> list[Marker]:
        return []

    def conditions(self, cond: Conditions) -> None:
        pass

    def modify_demand(self, t: float, profile: DemandProfile) -> None:
        pass

    def on_start(self, eng: "Engine") -> None:
        pass

    def on_step(self, eng: "Engine", t: float) -> None:
        pass

    def on_arrival(self, eng: "Engine", tracked: "Tracked") -> None:
        pass

    def status(self, eng: "Engine") -> dict[str, Any]:
        return {}

    def results(self, eng: "Engine") -> dict[str, Any]:
        return {}

    def reserved_edges(self) -> set[str]:
        return set()


@dataclass
class Recipe:
    base_rate: float
    components: list[Component]


class Scenario:
    """Runtime scenario: definition + validated params + seed → concrete instance."""

    def __init__(self, spec: ScenarioSpec, build: Callable[["Scenario"], Recipe], params: dict[str, Any] | None = None):
        self.spec = spec
        self._build = build
        self.params: dict[str, Any] = validate_params(spec, params or {})
        self.seed: int = 0
        self.recipe: Recipe | None = None
        self.net: "NetInfo | None" = None
        self.reserved: set[str] = set()
        self.protected: set[str] = set()
        self.preemption = bool(self.params.get("preemption", False))
        self.tsp = bool(self.params.get("tsp", False))

    # -- lifecycle -----------------------------------------------------------
    def instantiate(self, seed: int, net: "NetInfo") -> None:
        self.seed = seed
        self.net = net
        self.recipe = self._build(self)
        self.reserved = set()
        self.protected = protected_edges(net)
        master = random.Random(seed * 2654435761 % (2**32))
        # every component keeps its own RNG stream (assigned in declaration order), so the seed's outcome does not
        # depend on the order in which the components are drawn
        comps = self.recipe.components
        streams = [random.Random(master.random()) for _ in comps]
        for i in sorted(range(len(comps)), key=lambda i: (comps[i].instantiate_order, i)):
            comps[i].instantiate(streams[i], self, net)
            for e in comps[i].reserved_edges():
                self.reserved.update(net.street_segment(e) if e in net.edges else [e])

    def network_id(self) -> str:
        requested = self.params.get("network")
        return requested if requested in self.spec.networks else self.spec.networks[0]

    @property
    def duration(self) -> float | None:
        d = float(self.params.get("duration", 0) or 0)
        return d * 60.0 if d > 0 else None

    @property
    def density(self) -> float:
        return float(self.params.get("traffic", 1.0))

    @property
    def components(self) -> list[Component]:
        return self.recipe.components if self.recipe else []

    def conditions(self) -> Conditions:
        cond = Conditions()
        for c in self.components:
            c.conditions(cond)
        return cond

    def demand(self, t: float) -> DemandProfile:
        assert self.recipe is not None
        prof = DemandProfile(
            rate=float(self.recipe.base_rate),
            bike_share=float(self.params.get("bike_share", 5)) / 100.0,
            ped_rate=900.0 if self.params.get("pedestrians", True) else 0.0,
        )
        for c in self.recipe.components:
            c.modify_demand(t, prof)
        return prof

    def on_start(self, eng: "Engine") -> None:
        for c in self.components:
            c.on_start(eng)

    def on_step(self, eng: "Engine", t: float) -> None:
        for c in self.components:
            c.on_step(eng, t)

    def on_arrival(self, eng: "Engine", tracked: "Tracked") -> None:
        for c in self.components:
            c.on_arrival(eng, tracked)

    def status(self, eng: "Engine") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for c in self.components:
            out.update(c.status(eng))
        return out

    def results(self, eng: "Engine") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for c in self.components:
            out.update(c.results(eng))
        return out

    @property
    def markers(self) -> list[Marker]:
        return sorted((m for c in self.components for m in c.markers()), key=lambda m: m.t)

    def briefing(self) -> list[BriefingItem]:
        return [b for c in self.components for b in c.briefing()]

    def update_live(self, params: dict[str, Any]) -> dict[str, Any]:
        specs = {p.key: p for p in self.spec.params}
        applied: dict[str, Any] = {}
        for key, val in params.items():
            spec = specs.get(key)
            if spec is None or not spec.live:
                raise ValueError(f"'{key}' cannot be changed while running")
            self.params[key] = coerce(spec, val)
            applied[key] = self.params[key]
        return applied

    def describe(self) -> dict[str, Any]:
        return {
            "scenario": self.spec.id,
            "title": self.spec.title,
            "seed": self.seed,
            "params": self.params,
            "network": self.network_id(),
            "duration": self.duration,
            "briefing": [b.model_dump() for b in self.briefing()],
            "markers": [m.model_dump() for m in self.markers],
        }


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------
def validate_params(spec: ScenarioSpec, params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    errors: list[str] = []
    specs = {p.key: p for p in spec.params}
    for key, ps in specs.items():
        val = params.get(key, ps.default)
        try:
            val = coerce(ps, val)
        except ValueError as e:
            errors.append(f"{ps.label}: {e}")
            val = ps.default
        out[key] = val
    unknown = set(params) - set(specs)
    if unknown:
        errors.append(f"unknown parameters: {sorted(unknown)}")
    if errors:
        raise ValueError("; ".join(errors))
    return out


def coerce(spec: ParamSpec, val: Any) -> Any:
    if spec.type == "int":
        try:
            v: float = int(float(val))
        except (TypeError, ValueError):
            raise ValueError(f"expected an integer, got {val!r}") from None
    elif spec.type == "float":
        try:
            v = float(val)
        except (TypeError, ValueError):
            raise ValueError(f"expected a number, got {val!r}") from None
    elif spec.type == "bool":
        return bool(val) if not isinstance(val, str) else val.lower() in ("1", "true", "yes", "on")
    elif spec.type == "select":
        opts = [o["value"] for o in (spec.options or [])]
        if val not in opts:
            raise ValueError(f"must be one of {opts}")
        return val
    else:
        if not isinstance(val, str):
            raise ValueError("expected an edge id")
        return val
    if spec.min is not None and v < spec.min:
        raise ValueError(f"must be ≥ {spec.min:g}")
    if spec.max is not None and v > spec.max:
        raise ValueError(f"must be ≤ {spec.max:g}")
    return v
