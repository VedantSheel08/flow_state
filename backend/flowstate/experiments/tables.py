"""Table definitions for experiment storage: one source for the PostgreSQL / TimescaleDB DDL and the SQLite fallback.

Time model. Every sample has ``sim_t`` (seconds since the start of the observation, warm-up excluded) and ``ts``
(timestamptz = ``experiments.started_at + sim_t``). ``ts`` is a *virtual* clock whose only purpose is to give the
time-series tables a real time column, so that TimescaleDB hypertables, ``time_bucket`` and retention policies work.
It is not wall-clock time of the measurement and not a real calendar time of day.

Units follow SUMO: speeds m/s (``*_kmh`` columns are km/h), emissions mg/s, fuel mg/s (SUMO >= 1.14 reports fuel mass),
distances m, times s.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# logical type -> (postgres, sqlite)
TYPES = {
    "uuid": ("uuid", "TEXT"),
    "text": ("text", "TEXT"),
    "int": ("integer", "INTEGER"),
    "bigint": ("bigint", "INTEGER"),
    "real": ("double precision", "REAL"),
    "ts": ("timestamptz", "REAL"),  # SQLite: unix seconds
    "json": ("jsonb", "TEXT"),
    "textarray": ("text[]", "TEXT"),
    "bool": ("boolean", "INTEGER"),
}


@dataclass(frozen=True)
class Col:
    name: str
    type: str
    notnull: bool = False
    doc: str = ""


@dataclass
class Table:
    name: str
    cols: list[Col]
    pk: tuple[str, ...] = ()
    indexes: list[tuple[str, ...]] = field(default_factory=list)
    hypertable: str | None = None  # time column when the table is time-series
    chunk: str = "1 day"
    fk: dict[str, str] = field(default_factory=dict)  # column -> "table(col)"

    def names(self) -> list[str]:
        return [c.name for c in self.cols]


def _c(name, type_, notnull=False, doc=""):
    return Col(name, type_, notnull, doc)


EXPERIMENTS = Table(
    "experiments",
    [
        _c("id", "uuid", True, "experiment id"),
        _c("name", "text", True),
        _c("scenario", "text", True, "FlowState scenario id"),
        _c("controller", "text", True, "signal controller label, e.g. fixed, actuated, fixed+preempt, rl:<name>"),
        _c("network", "text", True),
        _c("seed", "int", True, "random seed: with scenario+params it fully determines the SUMO run"),
        _c("params", "json", True, "validated scenario parameters (incl. the signal settings the controller maps to)"),
        _c("params_hash", "text", True, "hash of the parameters that define the *situation* (everything except the controller)"),
        _c("warmup_s", "real", True),
        _c("duration_s", "real", True, "observation length after warm-up"),
        _c("started_at", "ts", True),
        _c("finished_at", "ts"),
        _c("status", "text", True, "running | completed | failed"),
        _c("baseline_id", "uuid", False, "experiment this one is compared against by default"),
        _c("suite", "text", False, "benchmark suite label"),
        _c("tags", "textarray"),
        _c("engine", "json", True, "SUMO version, backend, step length, code revision"),
        _c("emission_model", "text", False, "e.g. HBEFA3 (simulated estimates, not measurements)"),
        _c("notes", "text"),
        _c("error", "text"),
    ],
    pk=("id",),
    indexes=[("scenario", "params_hash", "seed", "controller"), ("suite", "status"), ("started_at",), ("controller", "scenario")],
    fk={"baseline_id": "experiments(id)"},
)

KPI_SAMPLES = Table(
    "kpi_samples",
    [
        _c("experiment_id", "uuid", True),
        _c("ts", "ts", True),
        _c("sim_t", "real", True),
        _c("active_vehicles", "int"),
        _c("halted_vehicles", "int", False, "vehicles with speed < 0.1 m/s"),
        _c("mean_speed_kmh", "real"),
        _c("density_veh_km_lane", "real", False, "vehicles per km of general-traffic lane"),
        _c("storage_occupancy", "real", False, "vehicles / jam storage (7.5 m per vehicle-lane)"),
        _c("queue_total", "int", False, "halting vehicles on signal and stop approaches"),
        _c("queue_max", "int"),
        _c("queued_junctions", "int", False, "junctions with a queue of at least 5"),
        _c("pending", "int", False, "vehicles waiting to be inserted"),
        _c("persons", "int"),
        _c("completed_total", "int"),
        _c("throughput_per_h", "real", False, "trailing 5-minute completed trips, per hour"),
        _c("mean_travel_s", "real", False, "trailing-window mean travel time"),
        _c("mean_wait_s", "real"),
        _c("mean_time_loss_s", "real"),
        _c("co2_mg_s", "real"),
        _c("co_mg_s", "real"),
        _c("hc_mg_s", "real"),
        _c("nox_mg_s", "real"),
        _c("pmx_mg_s", "real"),
        _c("fuel_mg_s", "real"),
        _c("idling_vehicles", "int", False, "vehicles that are stationary and not at a planned stop"),
        _c("idle_co2_mg_s", "real"),
        _c("idle_fuel_mg_s", "real"),
        _c("teleports_total", "int"),
        _c("collisions_total", "int", False, "SUMO colliding-vehicle notifications so far"),
        _c("emergency_stops_total", "int", False, "emergency braking events so far"),
    ],
    pk=("experiment_id", "ts"),
    indexes=[("experiment_id", "sim_t")],
    hypertable="ts",
    fk={"experiment_id": "experiments(id)"},
)

EDGE_SAMPLES = Table(
    "edge_samples",
    [
        _c("experiment_id", "uuid", True),
        _c("ts", "ts", True),
        _c("sim_t", "real", True),
        _c("edge_id", "text", True),
        _c("vehicles", "int", True),
        _c("halting", "int"),
        _c("mean_speed_ms", "real"),
        _c("occupancy", "real", False, "vehicles / jam storage of the edge"),
        _c("co2_mg_s", "real"),
        _c("fuel_mg_s", "real"),
    ],
    pk=("experiment_id", "ts", "edge_id"),
    indexes=[("experiment_id", "edge_id", "sim_t")],
    hypertable="ts",
    fk={"experiment_id": "experiments(id)"},
)

JUNCTION_SAMPLES = Table(
    "junction_samples",
    [
        _c("experiment_id", "uuid", True),
        _c("ts", "ts", True),
        _c("sim_t", "real", True),
        _c("tls_id", "text", True),
        _c("queue", "int", True, "halting vehicles on the approaches"),
        _c("vehicles", "int"),
        _c("phase", "int"),
    ],
    pk=("experiment_id", "ts", "tls_id"),
    indexes=[("experiment_id", "tls_id", "sim_t")],
    hypertable="ts",
    fk={"experiment_id": "experiments(id)"},
)

SIGNAL_EVENTS = Table(
    "signal_events",
    [
        _c("experiment_id", "uuid", True),
        _c("ts", "ts", True),
        _c("sim_t", "real", True),
        _c("tls_id", "text", True),
        _c("kind", "text", True, "phase | preempt | tsp | policy"),
        _c("phase_from", "int"),
        _c("phase_to", "int"),
        _c("elapsed_s", "real", False, "how long the previous phase ran"),
        _c("state", "text", False, "SUMO signal state string after the change"),
        _c("detail", "json", False, "controller-specific explanation (vehicle held for, seconds extended, ...)"),
    ],
    indexes=[("experiment_id", "sim_t"), ("experiment_id", "tls_id", "sim_t"), ("experiment_id", "kind")],
    hypertable="ts",
    fk={"experiment_id": "experiments(id)"},
)

SAFETY_EVENTS = Table(
    "safety_events",
    [
        _c("experiment_id", "uuid", True),
        _c("ts", "ts", True),
        _c("sim_t", "real", True),
        _c("kind", "text", True, "collision | teleport | emergency_stop"),
        _c("subtype", "text", False, "SUMO collision type: collision, junction, crossing, side, ..."),
        _c("vehicle", "text"),
        _c("other", "text"),
        _c("lane", "text"),
        _c("speed_ms", "real"),
        _c("other_speed_ms", "real"),
        _c("detail", "json"),
    ],
    indexes=[("experiment_id", "kind", "sim_t")],
    hypertable="ts",
    fk={"experiment_id": "experiments(id)"},
)

TRIP_RESULTS = Table(
    "trip_results",
    [
        _c("experiment_id", "uuid", True),
        _c("vehicle_id", "text", True),
        _c("mode", "text", False, "car | truck | bus | bike | ..."),
        _c("depart_s", "real"),
        _c("arrival_s", "real"),
        _c("finished", "bool", False, "false for trips still under way when the run ended"),
        _c("route_length_m", "real"),
        _c("duration_s", "real"),
        _c("waiting_s", "real", False, "time at speed < 0.1 m/s"),
        _c("time_loss_s", "real"),
        _c("stops", "int", False, "separate stops (SUMO waitingCount)"),
        _c("co2_mg", "real"),
        _c("fuel_mg", "real"),
        _c("nox_mg", "real"),
        _c("pmx_mg", "real"),
    ],
    pk=("experiment_id", "vehicle_id"),
    indexes=[("experiment_id", "mode")],
    fk={"experiment_id": "experiments(id)"},
)

SUMMARIES = Table(
    "experiment_summary",
    [
        _c("experiment_id", "uuid", True),
        _c("trips_completed", "int"),
        _c("throughput_per_h", "real"),
        _c("mean_travel_s", "real"),
        _c("mean_time_loss_s", "real"),
        _c("mean_wait_s", "real"),
        _c("stops_per_trip", "real"),
        _c("mean_speed_kmh", "real"),
        _c("mean_density_veh_km_lane", "real"),
        _c("mean_storage_occupancy", "real"),
        _c("peak_storage_occupancy", "real"),
        _c("mean_queue", "real"),
        _c("peak_queue", "int"),
        _c("pending_max", "int"),
        _c("teleports", "int"),
        _c("collisions", "int"),
        _c("emergency_stops", "int"),
        _c("co2_total_g", "real"),
        _c("co2_g_per_km", "real"),
        _c("co2_g_per_trip", "real"),
        _c("fuel_total_g", "real"),
        _c("idle_s_total", "real"),
        _c("idle_co2_g", "real"),
        _c("idle_fuel_g", "real"),
        _c("idle_share_of_co2", "real"),
        _c("extra", "json", False, "per-mode breakdown, scenario-specific results (response times, ...)"),
    ],
    pk=("experiment_id",),
    fk={"experiment_id": "experiments(id)"},
)

TABLES = [EXPERIMENTS, KPI_SAMPLES, EDGE_SAMPLES, JUNCTION_SAMPLES, SIGNAL_EVENTS, SAFETY_EVENTS, TRIP_RESULTS, SUMMARIES]
BY_NAME = {t.name: t for t in TABLES}


def ddl(dialect: str, *, hypertables: bool = False) -> list[str]:
    """CREATE statements for ``dialect`` ('postgres' | 'sqlite'). With ``hypertables`` also the TimescaleDB calls."""
    idx = 0 if dialect == "postgres" else 1
    out: list[str] = []
    for t in TABLES:
        lines = []
        for c in t.cols:
            lines.append(f"  {c.name} {TYPES[c.type][idx]}{' NOT NULL' if c.notnull else ''}")
        if t.pk:
            lines.append(f"  PRIMARY KEY ({', '.join(t.pk)})")
        for col, ref in t.fk.items():
            lines.append(f"  FOREIGN KEY ({col}) REFERENCES {ref}" + (" ON DELETE CASCADE" if t.name != "experiments" else ""))
        out.append(f"CREATE TABLE IF NOT EXISTS {t.name} (\n" + ",\n".join(lines) + "\n)")
        for cols in t.indexes:
            out.append(f"CREATE INDEX IF NOT EXISTS {t.name}_{'_'.join(cols)}_idx ON {t.name} ({', '.join(cols)})")
    if dialect == "postgres" and hypertables:
        for t in TABLES:
            if t.hypertable:
                out.append(f"SELECT create_hypertable('{t.name}', '{t.hypertable}', chunk_time_interval => INTERVAL '{t.chunk}', if_not_exists => TRUE, migrate_data => TRUE)")
    return out


def schema_sql(dialect: str, *, hypertables: bool = False) -> str:
    head = {
        "postgres": "-- FlowState experiment store: PostgreSQL (TimescaleDB hypertables are created only when the extension exists).\n",
        "sqlite": "-- FlowState experiment store: SQLite fallback with the same columns (ts is unix seconds, json/arrays are TEXT).\n",
    }[dialect]
    return head + ";\n\n".join(ddl(dialect, hypertables=hypertables)) + ";\n"
