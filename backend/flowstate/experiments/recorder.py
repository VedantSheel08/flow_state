"""Run one real SUMO experiment and record what it measured.

Nothing here is simulated twice or invented: a normal FlowState ``Engine`` is stepped, and every number written is read
from SUMO (through the engine's own metrics, TraCI/libsumo queries, and SUMO's ``tripinfo`` / ``collision`` output files).
The engine, scenarios and signal code are *not* modified; the extra SUMO outputs are requested by wrapping the engine's
launch command (``instrumented_sumo``).

Persistence is optional and isolated: if the database is down the experiment still runs and its results are returned
(and can be exported to JSON); a storage error is reported in ``ExperimentResult.persist_error``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import emissions as EM
from .store import ExperimentStore, SqliteStore, ROOT

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
JAM_SPACING_M = 7.5

# A controller label maps to scenario parameters that exist in the scenario's own spec.
CONTROLLERS: dict[str, dict[str, Any]] = {
    "fixed": {"signals": "fixed"},
    "actuated": {"signals": "actuated"},
    "fixed+preempt": {"signals": "fixed", "preemption": True},
    "actuated+preempt": {"signals": "actuated", "preemption": True},
    "fixed+tsp": {"signals": "fixed", "tsp": True},
    "actuated+tsp": {"signals": "actuated", "tsp": True},
}
# parameters a controller sets: excluded from the hash that identifies the *situation* so controllers can be paired
CONTROLLER_KEYS = ("signals", "preemption", "tsp")


@dataclass
class ExperimentSpec:
    scenario: str
    seed: int
    controller: str = "fixed"
    params: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    warmup: float = 90.0
    suite: str | None = None
    tags: list[str] = field(default_factory=list)
    baseline_id: str | None = None
    notes: str | None = None
    step_length: float = 0.25
    kpi_interval: float = 1.0
    spatial_interval: float = 5.0
    # custom controllers (e.g. a learned policy): called with the started Engine, may append to ``engine.signals.policies``
    policy_factory: Callable[[Any], Any] | None = None
    base_mode: str = "fixed"

    def to_json(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "policy_factory"}
        return d


@dataclass
class ExperimentResult:
    id: str
    spec: ExperimentSpec
    params: dict[str, Any]
    summary: dict[str, Any]
    status: str
    network: str
    engine_info: dict[str, Any]
    rows: dict[str, list[dict[str, Any]]]
    memory: ExperimentStore
    persisted: str | None = None  # backend name when stored
    persist_error: str | None = None
    wall_s: float = 0.0

    def export(self) -> dict[str, Any]:
        from .export import export_experiment

        return export_experiment(self.memory, self.id)


# --------------------------------------------------------------------------- helpers
def controller_params(scenario_id: str, controller: str, params: dict[str, Any], base_mode: str = "fixed") -> dict[str, Any]:
    """Scenario parameters for ``controller``; raises ``ValueError`` if the scenario cannot express it."""
    from ..scenarios.catalog import CATALOG

    if scenario_id not in CATALOG:
        raise ValueError(f"unknown scenario '{scenario_id}'")
    keys = {p.key for p in CATALOG[scenario_id][0].params}
    if controller in CONTROLLERS:
        wanted = CONTROLLERS[controller]
    elif controller.startswith("custom:") or controller.startswith("rl:"):
        wanted = {"signals": base_mode}
    else:
        raise ValueError(f"unknown controller '{controller}' (known: {sorted(CONTROLLERS)}, or 'rl:<name>' / 'custom:<name>' with a policy factory)")
    out = dict(params)
    for k, v in wanted.items():
        if k not in keys:
            if v:  # asking for pre-emption on a scenario that has no such switch cannot be honoured silently
                raise ValueError(f"scenario '{scenario_id}' has no '{k}' option, so controller '{controller}' is not available for it")
            continue
        out[k] = v
    return out


def situation_hash(scenario: str, network: str, params: dict[str, Any], warmup: float) -> str:
    sit = {k: v for k, v in sorted(params.items()) if k not in CONTROLLER_KEYS}
    return hashlib.sha1(json.dumps({"s": scenario, "n": network, "p": sit, "w": warmup}, sort_keys=True, default=str).encode()).hexdigest()[:16]


@contextmanager
def instrumented_sumo(work: Path):
    """Ask SUMO for per-trip emissions, collision records (junction checks on) and trip info, via the engine's launch command."""
    from ..sim import engine as module

    targets = [module.traci] + ([module.libsumo] if module.libsumo else [])
    originals = [(t, t.start) for t in targets]

    def wrap(original):
        def start(cmd, *a, **kw):
            cmd = list(cmd)
            if "--collision.check-junctions" in cmd:
                cmd[cmd.index("--collision.check-junctions") + 1] = "true"
            cmd += ["--collision-output", str(work / "collisions.xml"), "--tripinfo-output", str(work / "tripinfo.xml"),
                    "--tripinfo-output.write-unfinished", "true", "--device.emissions.probability", "1.0"]
            return original(cmd, *a, **kw)
        return start

    try:
        for t, o in originals:
            t.start = wrap(o)
        yield
    finally:
        for t, o in originals:
            t.start = o


def _git_rev() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _sumo_version(T: Any) -> str | None:
    try:
        v = T.getVersion()
        return v[1] if isinstance(v, (tuple, list)) else str(v)
    except Exception:  # noqa: BLE001
        try:
            from importlib.metadata import version

            return "eclipse-sumo " + version("eclipse-sumo")
        except Exception:  # noqa: BLE001
            return None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _sf(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- the run
def run_experiment(spec: ExperimentSpec, store: ExperimentStore | None = None, *, networks_dir: Path | None = None, work_dir: Path | None = None,
                   progress: Callable[[float], None] | None = None) -> ExperimentResult:
    from ..api.service import NETWORKS_DIR, WORK_DIR
    from ..scenarios.catalog import create
    from ..sim.engine import Engine, RunConfig
    from ..sim.vtypes import MODE_OF, base_type

    t_wall = time.time()
    params = controller_params(spec.scenario, spec.controller, spec.params, spec.base_mode)
    scenario = create(spec.scenario, params)  # validates (raises ValueError on bad parameters)
    exp_id = str(uuid.uuid4())
    work = Path(work_dir) if work_dir else WORK_DIR / f"exp_{exp_id[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    for f in ("collisions.xml", "tripinfo.xml"):
        (work / f).unlink(missing_ok=True)
    signal_mode = scenario.params.get("signals", spec.base_mode)
    eng = Engine(scenario, RunConfig(spec.scenario, scenario.params, spec.seed, signal_mode, spec.step_length, spec.warmup, label=f"exp-{exp_id[:6]}"),
                 networks_dir or NETWORKS_DIR, work)
    net = eng.net
    duration = float(scenario.duration or 0.0)
    if duration <= 0:
        raise ValueError("experiments need a finite run length (set the scenario 'duration')")
    p_hash = situation_hash(spec.scenario, eng.network_id, scenario.params, spec.warmup)
    started = datetime.now(timezone.utc)

    street_edges = [e for e in net.edges.values() if e.kind in ("street", "entry", "exit")]
    lane_km = sum(e.length * max(1, len(net.car_lanes(e.id))) for e in street_edges) / 1000.0
    storage = sum(e.length * max(1, len(net.car_lanes(e.id))) for e in net.edges.values() if e.kind in ("street", "entry")) / JAM_SPACING_M
    edge_cap = {e.id: max(1.0, e.length * max(1, len(net.car_lanes(e.id))) / JAM_SPACING_M) for e in street_edges}
    approach_n = {t: 0 for t in net.tls_ids}

    rows: dict[str, list[dict[str, Any]]] = {k: [] for k in ("kpi_samples", "edge_samples", "junction_samples", "signal_events", "safety_events", "trip_results")}

    def stamp(t: float) -> datetime:
        return started + timedelta(seconds=t)

    acc = EM.EmissionAccumulator()
    results: dict[str, Any] = {}
    emission_classes: dict[str, str] = {}
    last_phase: dict[str, tuple[int, float]] = {}
    held: dict[str, str | None] = {}
    extended: dict[str, float] = {}
    next_kpi = next_spatial = 0.0
    last_sample_t = 0.0
    seen_tele = 0
    seen_estop: set[str] = set()
    n_tele = n_estop = 0
    peak_q = 0
    pending_max = 0
    occ_samples: list[float] = []
    dens_samples: list[float] = []
    speed_samples: list[float] = []
    queue_samples: list[float] = []

    status, error = "completed", None
    log.info("experiment %s: %s/%s seed=%s", exp_id[:8], spec.scenario, spec.controller, spec.seed)
    with instrumented_sumo(work):
        eng.start()
        try:
            emission_classes = EM.assign_emission_classes(eng.T)
            if spec.policy_factory is not None:
                pol = spec.policy_factory(eng)
                if pol is not None:
                    eng.signals.policies.append(pol)
            T = eng.T
            engine_info = {"sumo": _sumo_version(T), "backend": "libsumo" if getattr(eng, "_libsumo", False) else "traci", "stepLength": spec.step_length,
                           "revision": _git_rev(), "emissionClasses": sorted(set(emission_classes.values()))}
            while not eng.finished:
                eng.step()
                t = eng.t
                if t < 0:
                    continue
                # --- signal decisions
                for tid, ph in eng.tls_phase.items():
                    prev = last_phase.get(tid)
                    if prev is None:
                        last_phase[tid] = (ph, t)
                    elif ph != prev[0]:
                        rows["signal_events"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), tls_id=tid, kind="phase", phase_from=prev[0], phase_to=ph,
                                                          elapsed_s=round(t - prev[1], 2), state=eng.tls_state.get(tid)))
                        last_phase[tid] = (ph, t)
                for tid, info in eng.signals.tls.items():
                    if info.held_by != held.get(tid):
                        if info.held_by:
                            rows["signal_events"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), tls_id=tid, kind="preempt", phase_to=info.phase,
                                                              detail={"vehicle": info.held_by, "label": eng.label(info.held_by), "stateAtRequest": info.state}))
                        held[tid] = info.held_by
                    if info.extended > extended.get(tid, 0.0) + 0.01 and info.extended > 0:
                        rows["signal_events"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), tls_id=tid, kind="tsp", phase_to=info.phase,
                                                          detail={"extendedS": round(info.extended, 2)}))
                    extended[tid] = info.extended
                # --- safety notifications that SUMO raises per step (collisions come from SUMO's own output file at the end)
                try:
                    ids = T.simulation.getStartingTeleportIDList()
                    for vid in ids:
                        n_tele += 1
                        rows["safety_events"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), kind="teleport", vehicle=vid, detail={"reason": "blocked for SUMO's teleport time"}))
                    es = T.simulation.getEmergencyStoppingVehiclesIDList()
                    for vid in es:
                        n_estop += 1
                        try:
                            rows["safety_events"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), kind="emergency_stop", vehicle=vid,
                                                              lane=T.vehicle.getLaneID(vid), speed_ms=round(T.vehicle.getSpeed(vid), 2)))
                        except Exception:  # noqa: BLE001
                            rows["safety_events"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), kind="emergency_stop", vehicle=vid))
                except Exception:  # noqa: BLE001 - not available on every backend; the counts then come from the engine
                    pass
                # --- KPI sample
                if t + 1e-9 >= next_kpi:
                    dt = max(1e-6, t - last_sample_t) if last_sample_t or rows["kpi_samples"] else spec.kpi_interval
                    last_sample_t = t
                    next_kpi = t + spec.kpi_interval
                    acc.begin()
                    halted = 0
                    cars = 0
                    for vid, v in eng.vehicles.items():
                        b = base_type(eng.vtype.get(vid, "sedan"))
                        mode = MODE_OF.get(b, "car")
                        if mode == "incident":
                            continue
                        sp = v[3]
                        e = {k: getattr(T.vehicle, g)(vid) for k, g in EM.EMISSION_GETTERS.items()}
                        road = T.vehicle.getRoadID(vid)
                        idling = sp < 0.1 and not (T.vehicle.getStopState(vid) & 1)  # stationary and not at a planned stop
                        acc.add(road if road in edge_cap else None, sp, e, idling)
                        if sp < 0.1:
                            halted += 1
                        if mode in ("car", "truck", "bus"):
                            cars += 1
                    acc.end(dt)
                    kp = eng.kpi or {}
                    queues = eng.junction_queues()
                    qtot = sum(queues.values())
                    qmax = max(queues.values(), default=0)
                    peak_q = max(peak_q, qmax)
                    pend = int(kp.get("pending") or 0)
                    pending_max = max(pending_max, pend)
                    stats = eng.edge_stats
                    occ = sum(s[0] for e_, s in stats.items() if e_ in net.edges and net.edges[e_].kind in ("street", "entry")) / storage if storage else 0.0
                    dens = cars / lane_km if lane_km else 0.0
                    occ_samples.append(occ)
                    dens_samples.append(dens)
                    if kp.get("meanSpeed") is not None:
                        speed_samples.append(kp["meanSpeed"])
                    queue_samples.append(qtot)
                    rows["kpi_samples"].append(dict(
                        experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), active_vehicles=int(kp.get("active") or len(eng.vehicles)), halted_vehicles=halted,
                        mean_speed_kmh=kp.get("meanSpeed"), density_veh_km_lane=round(dens, 3), storage_occupancy=round(occ, 4), queue_total=qtot, queue_max=qmax,
                        queued_junctions=sum(1 for q in queues.values() if q >= 5), pending=pend, persons=kp.get("persons"), completed_total=kp.get("completed"),
                        throughput_per_h=kp.get("throughput"), mean_travel_s=kp.get("travelTime"), mean_wait_s=kp.get("waiting"), mean_time_loss_s=kp.get("timeLoss"),
                        co2_mg_s=round(acc.rate["co2"], 2), co_mg_s=round(acc.rate["co"], 2), hc_mg_s=round(acc.rate["hc"], 2), nox_mg_s=round(acc.rate["nox"], 2),
                        pmx_mg_s=round(acc.rate["pmx"], 3), fuel_mg_s=round(acc.rate["fuel"], 2), idling_vehicles=acc.idling,
                        idle_co2_mg_s=round(acc.idle_rate["co2"], 2), idle_fuel_mg_s=round(acc.idle_rate["fuel"], 2),
                        teleports_total=eng.teleports, collisions_total=eng.collisions, emergency_stops_total=n_estop))
                    if t + 1e-9 >= next_spatial:
                        next_spatial = t + spec.spatial_interval
                        for eid, (n, h, sp) in stats.items():
                            if eid in edge_cap and n:
                                be = acc.by_edge.get(eid, {})
                                rows["edge_samples"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), edge_id=eid, vehicles=n, halting=h,
                                                                 mean_speed_ms=round(sp, 2), occupancy=round(n / edge_cap[eid], 3),
                                                                 co2_mg_s=round(be.get("co2", 0.0), 2), fuel_mg_s=round(be.get("fuel", 0.0), 2)))
                        for tid, q in queues.items():
                            rows["junction_samples"].append(dict(experiment_id=exp_id, ts=stamp(t), sim_t=round(t, 2), tls_id=tid, queue=q,
                                                                 vehicles=None, phase=eng.tls_phase.get(tid)))
                    if progress and int(t) % 10 == 0:
                        progress(min(1.0, t / duration))
            results = eng.results()
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            log.exception("experiment %s failed", exp_id[:8])
        finally:
            eng.close()
    if status == "failed":
        mem = SqliteStore(":memory:")
        mem.init_schema()
        res = ExperimentResult(exp_id, spec, scenario.params, {}, status, eng.network_id, {"sumo": None}, rows, mem, wall_s=time.time() - t_wall)
        res.persist_error = error
        _persist(res, store, started, p_hash, duration, error)
        return res

    # --- SUMO's own output files (written when the simulation closes)
    warm = spec.warmup
    trips: list[dict[str, Any]] = []
    obs_trips = []
    tf = work / "tripinfo.xml"
    if tf.exists():
        for e in ET.parse(tf).getroot():
            if e.tag != "tripinfo":
                continue
            em = e.find("emissions")
            finished = float(e.get("arrival", -1)) >= 0
            depart, arrival = float(e.get("depart", -1)), float(e.get("arrival", -1))
            b = base_type(e.get("vType", "sedan"))
            rec = dict(experiment_id=exp_id, vehicle_id=e.get("id"), mode=MODE_OF.get(b, "car"), depart_s=round(depart - warm, 2), arrival_s=round(arrival - warm, 2) if finished else None,
                       finished=finished, route_length_m=_sf(e.get("routeLength")), duration_s=_sf(e.get("duration")), waiting_s=_sf(e.get("waitingTime")),
                       time_loss_s=_sf(e.get("timeLoss")), stops=int(float(e.get("waitingCount", 0))),
                       co2_mg=_sf(em.get("CO2_abs")) if em is not None else None, fuel_mg=_sf(em.get("fuel_abs")) if em is not None else None,
                       nox_mg=_sf(em.get("NOx_abs")) if em is not None else None, pmx_mg=_sf(em.get("PMx_abs")) if em is not None else None)
            if rec["mode"] == "incident" or (e.get("id") or "").startswith("inc"):
                continue
            trips.append(rec)
            if finished and arrival >= warm and rec["mode"] in ("car", "truck", "bus"):
                obs_trips.append(rec)
    rows["trip_results"] = trips
    cf = work / "collisions.xml"
    collisions = []
    if cf.exists():
        for c in ET.parse(cf).getroot():
            tt = float(c.get("time", 0))
            if tt < warm:
                continue
            collisions.append(c)
            rows["safety_events"].append(dict(experiment_id=exp_id, ts=stamp(tt - warm), sim_t=round(tt - warm, 2), kind="collision", subtype=c.get("type"), vehicle=c.get("collider"),
                                              other=c.get("victim"), lane=c.get("lane"), speed_ms=_sf(c.get("colliderSpeed")), other_speed_ms=_sf(c.get("victimSpeed")),
                                              detail={"colliderType": c.get("colliderType"), "victimType": c.get("victimType"), "pos": _sf(c.get("pos"))}))

    # --- summary: observation window only, from measurements
    n_obs = len(obs_trips)
    km = acc.distance_m / 1000.0
    summary = dict(
        trips_completed=n_obs, throughput_per_h=n_obs * 3600.0 / duration,
        mean_travel_s=_mean([t["duration_s"] for t in obs_trips if t["duration_s"] is not None]),
        mean_time_loss_s=_mean([t["time_loss_s"] for t in obs_trips if t["time_loss_s"] is not None]),
        mean_wait_s=_mean([t["waiting_s"] for t in obs_trips if t["waiting_s"] is not None]),
        stops_per_trip=_mean([float(t["stops"]) for t in obs_trips]),
        mean_speed_kmh=_mean(speed_samples), mean_density_veh_km_lane=_mean(dens_samples), mean_storage_occupancy=_mean(occ_samples),
        peak_storage_occupancy=max(occ_samples, default=None), mean_queue=_mean(queue_samples), peak_queue=peak_q, pending_max=pending_max,
        teleports=n_tele if n_tele else max(0, int(results.get("teleports", 0))), collisions=len(collisions), emergency_stops=n_estop,
        co2_total_g=acc.total_mg["co2"] / 1000.0, co2_g_per_km=(acc.total_mg["co2"] / 1000.0 / km) if km > 0 else None,
        co2_g_per_trip=(acc.total_mg["co2"] / 1000.0 / n_obs) if n_obs else None, fuel_total_g=acc.total_mg["fuel"] / 1000.0,
        idle_s_total=acc.idle_vehicle_s, idle_co2_g=acc.idle_mg["co2"] / 1000.0, idle_fuel_g=acc.idle_mg["fuel"] / 1000.0,
        idle_share_of_co2=(acc.idle_mg["co2"] / acc.total_mg["co2"]) if acc.total_mg["co2"] > 0 else None,
    )
    by_mode: dict[str, dict[str, Any]] = {}
    for t in obs_trips:
        m = by_mode.setdefault(t["mode"], {"trips": 0, "co2_mg": 0.0, "fuel_mg": 0.0, "waiting_s": 0.0, "duration_s": 0.0})
        m["trips"] += 1
        for k in ("co2_mg", "fuel_mg", "waiting_s", "duration_s"):
            m[k] += t[k] or 0.0
    summary["extra"] = {
        "byMode": by_mode, "vehicleKm": km, "otherTotalsG": {k: acc.total_mg[k] / 1000.0 for k in ("co", "hc", "nox", "pmx")},
        "scenario": results.get("scenario"), "signals": results.get("signals"), "collisionTypes": _count([c.get("type") for c in collisions]),
        "unfinishedTrips": sum(1 for t in trips if not t["finished"]), "teleportsEngine": results.get("teleports"), "demandProfile": "see experiments.params",
    }
    mem = SqliteStore(":memory:")
    mem.init_schema()
    engine_info["emissionModel"] = EM.MODEL
    res = ExperimentResult(exp_id, spec, scenario.params, summary, "completed", eng.network_id, engine_info, rows, mem, wall_s=time.time() - t_wall)
    _persist(res, store, started, p_hash, duration, None)
    return res


def _count(xs: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return out


def _write_all(st: ExperimentStore, res: ExperimentResult, started: datetime, p_hash: str, duration: float, error: str | None) -> None:
    spec = res.spec
    st.create_experiment(dict(
        id=res.id, name=spec.name or f"{spec.scenario}/{spec.controller}/seed{spec.seed}", scenario=spec.scenario, controller=spec.controller, network=res.network,
        seed=spec.seed, params=res.params, params_hash=p_hash, warmup_s=spec.warmup, duration_s=duration, started_at=started, baseline_id=spec.baseline_id,
        suite=spec.suite, tags=spec.tags, engine=res.engine_info, emission_model=EM.MODEL, notes=spec.notes))
    for table in ("kpi_samples", "edge_samples", "junction_samples", "signal_events", "safety_events", "trip_results"):
        for i in range(0, len(res.rows[table]), 5000):
            st.insert_rows(table, res.rows[table][i:i + 5000])
    st.finish_experiment(res.id, res.status, res.summary or None, error)


def _persist_memory(res: ExperimentResult, started: datetime, p_hash: str, duration: float, error: str | None) -> None:
    try:
        _write_all(res.memory, res, started, p_hash, duration, error)
    except Exception as exc:  # noqa: BLE001
        log.exception("in-memory export store failed")
        res.persist_error = f"memory: {exc}"


def _persist_store(res: ExperimentResult, store: ExperimentStore, started: datetime, p_hash: str, duration: float, error: str | None) -> None:
    """Write to the configured database; never raises (a storage problem must not turn a finished experiment into a failure)."""
    try:
        _write_all(store, res, started, p_hash, duration, error)
        res.persisted = store.backend
    except Exception as exc:  # noqa: BLE001
        res.persist_error = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
        log.warning("could not store experiment %s: %s", res.id[:8], res.persist_error)
        try:
            store.delete_experiment(res.id)  # no half-written experiment
        except Exception:  # noqa: BLE001
            pass


def _persist(res: ExperimentResult, store: ExperimentStore | None, started: datetime, p_hash: str, duration: float, error: str | None) -> None:
    """Write to the in-memory export store (always) and the configured database (best effort)."""
    _persist_memory(res, started, p_hash, duration, error)
    if store is not None:
        _persist_store(res, store, started, p_hash, duration, error)
