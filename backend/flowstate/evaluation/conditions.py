"""Named, reproducible traffic conditions.

A condition is a complete experiment input: scenario, parameters, warm-up, SUMO and demand-generator overrides and
(for gridlock) the declared zone. Seeds are given separately, so a condition + seed pair always replays the same
SUMO run (same machine, same package versions; see the manifest each report carries).

``baseline_*`` conditions reproduce the inherited presets and exist to be measured against the acceptance contract.
The shipped conditions (``rush``, ``severe``, ``gridlock``) are tuned against it; their definitions and the reasons
for every setting are in docs/TRAFFIC_REALISM.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Condition:
    id: str
    requested: str  # the tier this condition intends to produce (an input, never a result)
    description: str
    scenario: str = "rush_hour"
    params: dict[str, Any] = field(default_factory=dict)
    warmup: float = 600.0
    observation: float = 300.0
    sumo_options: dict[str, str | None] = field(default_factory=dict)
    demand_overrides: dict[str, Any] = field(default_factory=dict)
    declared_junctions: list[str] = field(default_factory=list)  # gridlock: the zone declared before the run
    reference: dict[str, Any] | None = None  # overrides that remove the gridlock cause, for the matched non-locked run
    outlets: dict[str, Any] = field(default_factory=dict)  # downstream capacity of the exit roads (engine.apply_outlets)
    box_guard: dict[str, Any] | None = None  # keep-the-box-clear rule (sim/guard.py)
    vtype_overrides: dict[str, dict[str, str]] = field(default_factory=dict)  # vehicle-model attributes replaced after the seeded draw
    signal_mode: str = "fixed"
    signal_program: str | None = None  # None: the engine default (safe for the dense rush hour)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# The four interior junctions at the centre of the district (Main/Church x King/Queen).
CENTRE = ["n1_1", "n2_1", "n1_2", "n2_2"]

CONDITIONS: dict[str, Condition] = {}


def register(c: Condition) -> Condition:
    CONDITIONS[c.id] = c
    return c


register(Condition("baseline_rush", "rush", "Inherited rush_hour preset: 2800 trips/h, peak x2.0 (task/backend-performance)",
                   params={"demand": 2800, "traffic": 1.0, "peak": 2.0}))
register(Condition("baseline_severe", "severe", "Inherited severe preset: 3200 trips/h x1.6, peak x2.5",
                   params={"demand": 3200, "traffic": 1.6, "peak": 2.5}))
register(Condition("baseline_gridlock", "gridlock", "Inherited gridlock_stress preset: 3200 trips/h x1.6, peak x3.0 (max demand; no cause)",
                   params={"demand": 3200, "traffic": 1.6, "peak": 3.0}, declared_junctions=CENTRE))


def get(cid: str) -> Condition:
    if cid not in CONDITIONS:
        raise KeyError(f"unknown condition '{cid}' (known: {', '.join(sorted(CONDITIONS))})")
    return CONDITIONS[cid]


# ------------------------------------------------------------------ experiments
# Controlled variants of a base condition. Each isolates one change so its effect on density, collisions and
# teleports can be read from the same seeds (docs/TRAFFIC_REALISM.md keeps the results).
def variant(base: Condition, new_id: str, description: str, *, params: dict | None = None, sumo: dict | None = None,
            demand: dict | None = None, outlets: dict | None = None, box_guard: dict | None = None, requested: str | None = None, **fields: Any) -> Condition:
    d = base.to_dict()
    d.update(id=new_id, description=description, requested=requested or base.requested, **fields)
    d["params"] = {**base.params, **(params or {})}
    d["sumo_options"] = {**base.sumo_options, **(sumo or {})}
    d["demand_overrides"] = {**base.demand_overrides, **(demand or {})}
    d["outlets"] = {**base.outlets, **(outlets or {})}
    d["vtype_overrides"] = {**base.vtype_overrides, **(fields.pop("vtype_overrides", None) or {})}
    if box_guard is not None:
        d["box_guard"] = box_guard or None
    return register(Condition(**d))


# Admission policy removed: every request is released to SUMO as soon as the generator draws it. Insertion is then
# limited only by SUMO's own physical insertion check (free space on the entry lane).
OPEN_ADMISSION = {"GATE_FRACTION": 99.0, "GATE_BY_CLASS": None, "PENDING_PER_GATEWAY": 0, "MAX_PENDING": 10**6, "MAX_PENDING_TOTAL": 10**6,
                  "MIN_THROTTLE": 1.0, "THROTTLE_SLOPE": 0.0, "PATH_GATE": None}
_sev = CONDITIONS["baseline_severe"]
variant(_sev, "exp_open", "baseline severe with all admission throttles removed; SUMO never discards pending insertions",
        demand=OPEN_ADMISSION, sumo={"--max-depart-delay": "-1"})
variant(CONDITIONS["exp_open"], "exp_open_lane", "exp_open with SUMO's one-vehicle-per-lane model (no sublane resolution)", sumo={"--lateral-resolution": None})
variant(CONDITIONS["exp_open"], "exp_open_nobike", "exp_open without cyclists (bike_share 0)", params={"bike_share": 0})

variant(CONDITIONS["exp_open"], "exp_out_lane", "exp_open + exit roads reduced to one lane (a lane drop beyond the district)", outlets={"lanes": 1})
variant(CONDITIONS["exp_open"], "exp_out_20", "exp_open + one exit lane and a 20 km/h limit on every exit road", outlets={"lanes": 1, "speed_kmh": 20})
variant(CONDITIONS["exp_open"], "exp_out_10", "exp_open + one exit lane and a 10 km/h limit on every exit road", outlets={"lanes": 1, "speed_kmh": 10})
variant(CONDITIONS["exp_open"], "exp_pm", "exp_open with the outbound (PM) peak: trips start in the district and leave through its exits", params={"period": "pm"})
variant(CONDITIONS["exp_pm"], "exp_pm_lane", "exp_pm + one exit lane", outlets={"lanes": 1})
variant(CONDITIONS["exp_pm"], "exp_pm_10", "exp_pm + one exit lane and 10 km/h on every exit road", outlets={"lanes": 1, "speed_kmh": 10})


# Demand sweeps (open admission, 2.0x peak plateau): the network's density / speed / flow relationship for each peak
# direction. They exist to choose condition parameters from measurements instead of guessing them.
for _period, _levels in (("am", (600, 1000, 1400, 1800)), ("pm", (400, 600, 800, 1000))):
    for _d in _levels:
        variant(CONDITIONS["exp_open"], f"sweep_{_period}_{_d}", f"open admission, {_period.upper()} peak, base demand {_d}/h x 2.0 plateau",
                params={"demand": _d, "traffic": 1.0, "peak": 2.0, "period": _period})


# Controlled accumulation (explicit external hold queue; docs/TRAFFIC_REALISM.md). Demand is far above capacity so the
# queue is never empty; the district holds TARGET_ACTIVE motor vehicles and the rest wait outside.
def _acc(cid: str, period: str, target: int, desc: str, guard: bool = False, **kw) -> Condition:
    return variant(CONDITIONS["exp_open"], cid, desc,
                   params={"demand": 3200, "traffic": 1.6, "peak": 2.5, "period": period},
                   demand={**OPEN_ADMISSION, "ADMISSION": "accumulation", "TARGET_ACTIVE": target, "MAX_HOLD_S": 900.0, "PENDING_PER_ORIGIN": 2},
                   sumo={"--max-depart-delay": "-1"}, box_guard=({"margin": 2.0, "window": 60.0} if guard else {}), **kw)


for _n in (600, 800, 1000):
    _acc(f"acc_pm_{_n}", "pm", _n, f"PM peak, district held at {_n} motor vehicles, no box guard")
    _acc(f"acc_pm_{_n}_g", "pm", _n, f"PM peak, district held at {_n} motor vehicles, keep-the-box-clear guard on", guard=True)


# Cruising for parking (AM peak): extra interior load from moving vehicles, entry flow unchanged.
def _cruise(cid: str, demand: int, share: float, legs: tuple[int, int], desc: str) -> Condition:
    return variant(CONDITIONS["exp_open"], cid, desc, params={"demand": demand, "traffic": 1.0, "peak": 2.0, "period": "am"},
                   demand={"ADMISSION": "open", "CRUISE_SHARE": share, "CRUISE_LEGS": legs, "MAX_HOLD_S": 900.0, "PENDING_PER_ORIGIN": 2})


_cruise("cruise_am_1", 1800, 0.5, (1, 3), "AM peak 3600/h, half of the district-bound trips cruise past 1-3 blocks first")
_cruise("cruise_am_2", 2200, 0.6, (2, 4), "AM peak 4400/h, 60 % cruise past 2-4 blocks")
_cruise("cruise_am_3", 2400, 0.8, (2, 5), "AM peak 4800/h, 80 % cruise past 2-5 blocks")


# Safety experiments on the downstream-bottleneck scenario (docs/TRAFFIC_REALISM.md, "Safety diagnosis").
variant(CONDITIONS["exp_pm_10"], "exp_pm_10_g", "exp_pm_10 + keep-the-box-clear rule", box_guard={"margin": 2.0, "window": 60.0})

# Recovery: no SUMO deadlock rescue (disclosed), the exit restriction lifts 300 s into the observation.
variant(CONDITIONS["exp_pm_10"], "rec_pm_10", "exp_pm_10, no teleport rescue, exits restored at t=300 s", sumo={"--time-to-teleport": "-1"},
        outlets={"relieve_at": 300.0}, observation=900.0)
variant(CONDITIONS["rec_pm_10"], "rec_pm_10_g", "rec_pm_10 + keep-the-box-clear rule", box_guard={"margin": 2.0, "window": 60.0})

# Collision experiments on a shorter severe run (collisions appear within the first 5 simulated minutes). Each variant
# changes one driving-model attribute set; counts are compared over the same seeds.
_sx = variant(CONDITIONS["baseline_severe"], "sx_ctrl", "severe preset, 300 s warm-up + 300 s: control for the collision experiments", warmup=300.0)
variant(_sx, "sx_gap", "control + minor-link time gap 2.5 s (jmTimegapMinor) for every vehicle type", vtype_overrides={"all": {"jmTimegapMinor": "2.5"}})
variant(_sx, "sx_lat", "control + wider lateral gap (minGapLat 0.8) and slower lateral speed (maxSpeedLat 0.6) for cars", vtype_overrides={"passenger": {"minGapLat": "0.8", "maxSpeedLat": "0.6"}, "delivery": {"minGapLat": "0.8", "maxSpeedLat": "0.6"}})
variant(_sx, "sx_guard", "control + keep-the-box-clear rule", box_guard={"margin": 2.0, "window": 60.0})

# Gridlock accumulation sweep: the district is held at TARGET_ACTIVE motor vehicles (the surplus waits in the counted external
# queue), rescue off, 900 s observation. "_o" adds the downstream bottleneck (one exit lane, 10 km/h) that lifts at t=300 s.
for _n in (450, 600, 750):
    variant(CONDITIONS["exp_open"], f"gl_{_n}", f"PM peak held at {_n} motor vehicles, rescue off, no downstream restriction",
            params={"demand": 3200, "traffic": 1.6, "peak": 2.5, "period": "pm"}, observation=900.0,
            demand={"ADMISSION": "accumulation", "TARGET_ACTIVE": _n, "MAX_HOLD_S": 1800.0, "PENDING_PER_ORIGIN": 2},
            sumo={"--max-depart-delay": "-1", "--time-to-teleport": "-1"})
    variant(CONDITIONS[f"gl_{_n}"], f"gl_{_n}_o", f"gl_{_n} + exit bottleneck (1 lane, 10 km/h) until t=300 s", outlets={"lanes": 1, "speed_kmh": 10, "relieve_at": 300.0})

# Pedestrian hypothesis test (docs/TRAFFIC_REALISM.md): identical runs with the pedestrian generator off.
variant(CONDITIONS["gl_450"], "gl_450_np", "gl_450 without pedestrians", params={"pedestrians": False})
variant(CONDITIONS["gl_750_o"], "gl_750_o_np", "gl_750_o without pedestrians", params={"pedestrians": False})
variant(CONDITIONS["rec_pm_10"], "rec_pm_10_np", "rec_pm_10 without pedestrians", params={"pedestrians": False})

# Pedestrian dose-response on gl_450 (seed-matched): global cap on simultaneous pedestrians.
for _p in (100, 200, 300):
    variant(CONDITIONS["gl_450"], f"gl_450_p{_p}", f"gl_450 with at most {_p} pedestrians in the district", demand={"MAX_PERSONS": _p})

# Signal-program hypothesis: the same gridlock runs with the protected-left "safe" program forced (the 900 s runs default to "0").
for _b in ("gl_450", "gl_750_o", "rec_pm_10"):
    variant(CONDITIONS[_b], f"{_b}_safe", f"{_b} with the protected-left safe signal program", signal_program="safe")

# Accumulation ladder with the safe program behind the exit bottleneck (lifts at t=300 s): where does the district stay
# jammed but still discharging, and does the box guard raise that ceiling?
for _n in (900, 1100, 1300):
    variant(CONDITIONS["gl_750_o_safe"], f"ga_{_n}", f"PM peak held at {_n} motor vehicles, safe program, exit bottleneck until t=300 s, rescue off",
            demand={"TARGET_ACTIVE": _n})
    variant(CONDITIONS[f"ga_{_n}"], f"ga_{_n}_g", f"ga_{_n} + keep-the-box-clear rule", box_guard={"margin": 2.0, "window": 60.0})

# Recovery ladder: the district is held at the jam level, then at t=300 s the exit restriction lifts AND the metered
# accumulation drops to 450 (demand relief); everything else is unchanged.
for _n in (1100, 1300):
    variant(CONDITIONS[f"ga_{_n}_g"], f"gr_{_n}_g", f"ga_{_n}_g, then exits restored and accumulation metered down to 450 at t=300 s",
            demand={"TARGET_SCHEDULE": [[300.0, 450]]})

for _n in (1600, 2000):
    variant(CONDITIONS["ga_1300_g"], f"ga_{_n}_g", f"PM peak held at {_n} motor vehicles, safe program, guard, exit bottleneck until t=300 s, rescue off", demand={"TARGET_ACTIVE": _n})


# ------------------------------------------------------------------ shipped conditions
# The three jam presets exactly as the API serves them (performance/congestion.ENGINE_OPTIONS is the single source).
# The warm-up is the time the district needs to reach the preset's accumulation; the first 300 s after it is the
# classified window. "gridlock" keeps running for 600 s more: the exits are restored and the accumulation is metered down
# at t=300 s, which is the recovery sequence (it never enters the classified window).
from ..performance.congestion import engine_options, engine_params  # noqa: E402


def _shipped(cid: str, tier: str, preset: str, warmup: float, observation: float, desc: str, *, relief: bool = False) -> Condition:
    o = engine_options(preset)
    demand, outlets = o["demand_overrides"], o["outlets"]
    if relief:
        outlets = {**outlets, "relieve_at": 300.0}
        demand = {**demand, "TARGET_SCHEDULE": [[300.0, int(demand["TARGET_ACTIVE"] * 0.4)]]}
    return register(Condition(cid, tier, desc, params=engine_params(preset), warmup=warmup, observation=observation,
                              sumo_options=o["sumo_options"], demand_overrides=demand, outlets=outlets, box_guard=o["box_guard"], signal_program=o["signal_program"]))


_shipped("rush", "rush", "rush_hour", 600.0, 300.0, "Rush hour: inbound AM peak, accumulation target 600 (saturates near 450), safe signal program, box guard")
_shipped("severe", "severe", "severe", 600.0, 300.0, "Severe: inbound AM peak plus 80 % of district-bound trips circling for parking, accumulation target 800")
_shipped("gridlock", "gridlock", "gridlock_stress", 1000.0, 900.0, "Gridlock: outbound PM peak held at 1400 vehicles (the district's measured storage ceiling) behind a downstream restriction; "
         "no deadlock rescue; at t=300 s the exits are restored and the accumulation is metered down to 40 %", relief=True)


# Remaining-collision experiments on the shipped rush condition (docs/TRAFFIC_REALISM.md, "Safety diagnosis").
variant(CONDITIONS["rush"], "rush_gap", "rush + minor-link time gap 3 s for cars and delivery vehicles (right-hook yielding margin)",
        vtype_overrides={"passenger": {"jmTimegapMinor": "3"}, "delivery": {"jmTimegapMinor": "3"}})
variant(CONDITIONS["rush"], "rush_ped", "rush + pedestrians never squeeze through a vehicle standing on a crossing", sumo={"--pedestrian.striping.jamtime.crossing": "-1"})
variant(CONDITIONS["rush_gap"], "rush_both", "rush_gap + rush_ped", sumo={"--pedestrian.striping.jamtime.crossing": "-1"})


# Accumulation sweeps for the shipped rush / severe conditions (rescue on, so every teleport is a counted real 240 s stall).
for _n in (450, 525, 600):
    variant(CONDITIONS["rush"], f"rush_{_n}", f"rush held at {_n} vehicles", demand={"TARGET_ACTIVE": _n})
for _n in (700, 800, 900):
    variant(CONDITIONS["severe"], f"severe_{_n}", f"severe held at {_n} vehicles behind the permanent restriction", demand={"TARGET_ACTIVE": _n})

# The inbound (AM) profile at the same accumulation ladder: does a different demand direction load the grid more evenly?
for _n in (450, 600, 750):
    variant(CONDITIONS["rush"], f"rush_am_{_n}", f"AM (inbound) peak held at {_n} vehicles", params={"period": "am"}, demand={"TARGET_ACTIVE": _n})

# Severe candidates on the inbound profile: vehicles that circle for parking (CRUISE_SHARE) load the interior without raising entry flow.
for _n, _share, _legs in ((600, 0.6, (2, 4)), (800, 0.8, (2, 5)), (1000, 0.9, (3, 6))):
    variant(CONDITIONS["rush_am_450"], f"sevam_{_n}", f"AM peak, held at {_n}, {int(_share * 100)} % of district-bound trips cruise {_legs[0]}-{_legs[1]} blocks",
            params={"demand": 2400}, demand={"TARGET_ACTIVE": _n, "CRUISE_SHARE": _share, "CRUISE_LEGS": _legs})


# Recovery timing: the same gridlock condition, relieved earlier (seconds after the classified window starts).
for _r in (0, 120):
    _g = CONDITIONS["gridlock"]
    variant(_g, f"gridlock_r{_r}", f"gridlock with the relief (exits restored, accumulation metered to 40 %) at t={_r} s", outlets={"relieve_at": float(_r)},
            demand={"TARGET_SCHEDULE": [[float(_r), 560]]})
