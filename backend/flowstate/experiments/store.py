"""Persistent experiment storage: PostgreSQL (Tiger Cloud / TimescaleDB) with a SQLite fallback.

``open_store()`` never raises: if the configured database cannot be reached it logs the reason and returns ``None`` (or
the local SQLite file when nothing is configured), so a simulation never depends on the database being up.

Environment
-----------
``FLOWSTATE_DATABASE_URL``  PostgreSQL URL (Tiger Cloud: the service URL from the console, ``sslmode=require`` included).
``TIGER_DATABASE_URL``      alias for the above.
``FLOWSTATE_EXPERIMENT_DB`` path of the SQLite fallback (default ``data/experiments/experiments.sqlite``).
``FLOWSTATE_STORE``         ``postgres`` | ``sqlite`` | ``off`` to force a backend.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import tables as T

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE = ROOT / "data" / "experiments" / "experiments.sqlite"
JSON_COLS = {t.name: {c.name for c in t.cols if c.type == "json"} for t in T.TABLES}
ARRAY_COLS = {t.name: {c.name for c in t.cols if c.type == "textarray"} for t in T.TABLES}
TS_COLS = {t.name: {c.name for c in t.cols if c.type == "ts"} for t in T.TABLES}
BOOL_COLS = {t.name: {c.name for c in t.cols if c.type == "bool"} for t in T.TABLES}

KPI_METRICS = [c.name for c in T.KPI_SAMPLES.cols if c.name not in ("experiment_id", "ts", "sim_t")]
SUMMARY_METRICS = [c.name for c in T.SUMMARIES.cols if c.name not in ("experiment_id", "extra")]
# which direction is better for the comparison view
BETTER = {
    "trips_completed": "higher", "throughput_per_h": "higher", "mean_speed_kmh": "higher",
}  # every other summary metric: lower is better


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


class ExperimentStore:
    """Common implementation; subclasses provide the connection, placeholders and a few dialect expressions."""

    backend = "abstract"
    ph = "?"
    timescale = False

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: Any = None

    # -- dialect hooks -----------------------------------------------------
    def _connect(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def _bucket(self, col: str, step: float) -> str:
        """SQL expression flooring ``col`` to a multiple of ``step`` (a validated float, inlined as a literal)."""
        if not step > 0:
            raise ValueError("bucket size must be positive")
        return f"(floor({col} / {step:.6f}) * {step:.6f})"

    def _enc(self, table: str, col: str, v: Any) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def _dec_ts(self, v: Any) -> Any:
        return v

    # -- plumbing ----------------------------------------------------------
    def conn(self):
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def _run(self, sql: str, args: Sequence[Any] = (), *, fetch: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            c = self.conn()
            try:
                cur = c.execute(sql.replace("{p}", self.ph), tuple(args))
                rows = [_plain(dict(zip([getattr(d, "name", d[0]) for d in cur.description], r))) for r in cur.fetchall()] if fetch and cur.description else []
                c.commit()
                return rows
            except Exception:
                c.rollback()
                raise

    def _decode(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        for col in JSON_COLS.get(table, ()):
            v = out.get(col)
            if isinstance(v, str):
                try:
                    out[col] = json.loads(v)
                except ValueError:
                    pass
        for col in ARRAY_COLS.get(table, ()):
            v = out.get(col)
            if isinstance(v, str):
                out[col] = json.loads(v)
        for col in TS_COLS.get(table, ()):
            out[col] = self._dec_ts(out.get(col))
        for col in BOOL_COLS.get(table, ()):
            if out.get(col) is not None:
                out[col] = bool(out[col])
        return out

    # -- schema ------------------------------------------------------------
    def init_schema(self) -> None:  # pragma: no cover - subclass
        raise NotImplementedError

    # -- writes ------------------------------------------------------------
    def create_experiment(self, spec: dict[str, Any]) -> str:
        """Insert a ``running`` experiment and return its id."""
        row = {
            "id": spec.get("id") or str(uuid.uuid4()),
            "status": "running",
            "started_at": spec.get("started_at") or _utcnow(),
            "engine": {},
            "tags": [],
            **{k: v for k, v in spec.items() if k not in ("id", "started_at")},
        }
        self.insert_rows("experiments", [row])
        return row["id"]

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:  # pragma: no cover - subclass
        raise NotImplementedError

    def finish_experiment(self, experiment_id: str, status: str, summary: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._lock:
            if summary is not None:
                self._run(f"DELETE FROM experiment_summary WHERE experiment_id = {{p}}", [experiment_id], fetch=False)
                self.insert_rows("experiment_summary", [{"experiment_id": experiment_id, **summary}])
            self._run(f"UPDATE experiments SET status = {{p}}, finished_at = {{p}}, error = {{p}} WHERE id = {{p}}",
                      [status, self._enc("experiments", "finished_at", _utcnow()), error, experiment_id], fetch=False)

    def delete_experiment(self, experiment_id: str) -> None:
        for t in ("kpi_samples", "edge_samples", "junction_samples", "signal_events", "safety_events", "trip_results", "experiment_summary"):
            self._run(f"DELETE FROM {t} WHERE experiment_id = {{p}}", [experiment_id], fetch=False)
        self._run("DELETE FROM experiments WHERE id = {p}", [experiment_id], fetch=False)

    # -- reads -------------------------------------------------------------
    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        rows = self._run("SELECT * FROM experiments WHERE id = {p}", [experiment_id])
        if not rows:
            return None
        exp = self._decode("experiments", rows[0])
        exp["summary"] = self.summary(experiment_id)
        return exp

    def list_experiments(self, *, scenario: str | None = None, controller: str | None = None, suite: str | None = None,
                         status: str | None = None, seed: int | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("e.scenario", scenario), ("e.controller", controller), ("e.suite", suite), ("e.status", status), ("e.seed", seed)):
            if val is not None:
                where.append(f"{col} = {{p}}")
                args.append(val)
        sql = ("SELECT e.*, s.throughput_per_h, s.mean_travel_s, s.mean_time_loss_s, s.mean_wait_s, s.co2_total_g, s.collisions, s.teleports "
               "FROM experiments e LEFT JOIN experiment_summary s ON s.experiment_id = e.id"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY e.started_at DESC LIMIT {p} OFFSET {p}")
        return [self._decode("experiments", r) for r in self._run(sql, [*args, int(limit), int(offset)])]

    def summary(self, experiment_id: str) -> dict[str, Any] | None:
        rows = self._run("SELECT * FROM experiment_summary WHERE experiment_id = {p}", [experiment_id])
        if not rows:
            return None
        s = self._decode("experiment_summary", rows[0])
        s.pop("experiment_id", None)
        return s

    def kpi_series(self, experiment_id: str, metrics: Sequence[str] | None = None, step_s: float | None = None) -> list[dict[str, Any]]:
        """Time series of KPI samples; ``step_s`` averages them into buckets in the database (efficient for long runs)."""
        metrics = list(metrics or KPI_METRICS)
        bad = [m for m in metrics if m not in KPI_METRICS]
        if bad:
            raise ValueError(f"unknown KPI metrics {bad}; valid: {KPI_METRICS}")
        if step_s and step_s > 1:
            cols = ", ".join(f"AVG({m}) AS {m}" for m in metrics)
            b = self._bucket("sim_t", float(step_s))
            sql = f"SELECT {b} AS sim_t, {cols} FROM kpi_samples WHERE experiment_id = {{p}} GROUP BY 1 ORDER BY 1"
            return self._run(sql, [experiment_id])
        cols = ", ".join(metrics)
        return self._run(f"SELECT sim_t, {cols} FROM kpi_samples WHERE experiment_id = {{p}} ORDER BY sim_t", [experiment_id])

    def edge_heatmap(self, experiment_id: str, metric: str = "halting", bucket_s: float = 30.0, top: int | None = None) -> list[dict[str, Any]]:
        """Congestion heatmap cells (edge x time bucket). ``metric``: halting | vehicles | occupancy | mean_speed_ms | co2_mg_s | fuel_mg_s."""
        if metric not in ("halting", "vehicles", "occupancy", "mean_speed_ms", "co2_mg_s", "fuel_mg_s"):
            raise ValueError(f"unknown heatmap metric {metric!r}")
        b = self._bucket("sim_t", float(bucket_s))
        sql = (f"SELECT edge_id, {b} AS t0, AVG({metric}) AS value, MAX({metric}) AS peak, COUNT(*) AS samples "
               f"FROM edge_samples WHERE experiment_id = {{p}} GROUP BY 1, 2 ORDER BY 2, 1")
        rows = self._run(sql, [experiment_id])
        if top:  # keep the busiest edges only
            tot: dict[str, float] = {}
            for r in rows:
                tot[r["edge_id"]] = tot.get(r["edge_id"], 0.0) + (r["value"] or 0.0)
            keep = set(sorted(tot, key=tot.get, reverse=True)[:top])
            rows = [r for r in rows if r["edge_id"] in keep]
        return rows

    def junction_heatmap(self, experiment_id: str, bucket_s: float = 30.0) -> list[dict[str, Any]]:
        b = self._bucket("sim_t", float(bucket_s))
        sql = (f"SELECT tls_id, {b} AS t0, AVG(queue) AS value, MAX(queue) AS peak FROM junction_samples "
               f"WHERE experiment_id = {{p}} GROUP BY 1, 2 ORDER BY 2, 1")
        return self._run(sql, [experiment_id])

    def signal_events(self, experiment_id: str, tls_id: str | None = None, kind: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        where, args = ["experiment_id = {p}"], [experiment_id]
        if tls_id:
            where.append("tls_id = {p}")
            args.append(tls_id)
        if kind:
            where.append("kind = {p}")
            args.append(kind)
        sql = f"SELECT * FROM signal_events WHERE {' AND '.join(where)} ORDER BY sim_t LIMIT {{p}}"
        return [self._decode("signal_events", r) for r in self._run(sql, [*args, int(limit)])]

    def signal_decision_stats(self, experiment_id: str) -> list[dict[str, Any]]:
        """Per signal: phase changes, mean/max phase length, and priority actions - the explainable view of a controller."""
        sql = ("SELECT tls_id, COUNT(*) FILTER (WHERE kind = 'phase') AS phase_changes, AVG(elapsed_s) FILTER (WHERE kind = 'phase') AS mean_phase_s, "
               "MAX(elapsed_s) FILTER (WHERE kind = 'phase') AS max_phase_s, COUNT(*) FILTER (WHERE kind = 'preempt') AS preemptions, "
               "COUNT(*) FILTER (WHERE kind = 'tsp') AS tsp_extensions, COUNT(*) FILTER (WHERE kind = 'policy') AS policy_decisions "
               "FROM signal_events WHERE experiment_id = {p} GROUP BY tls_id ORDER BY tls_id")
        return self._run(sql, [experiment_id])

    def safety_events(self, experiment_id: str, kind: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        sql = "SELECT * FROM safety_events WHERE experiment_id = {p}" + (" AND kind = {p}" if kind else "") + " ORDER BY sim_t LIMIT {p}"
        args = [experiment_id] + ([kind] if kind else []) + [int(limit)]
        return [self._decode("safety_events", r) for r in self._run(sql, args)]

    def safety_counts(self, experiment_id: str) -> dict[str, int]:
        rows = self._run("SELECT kind, COUNT(*) AS n FROM safety_events WHERE experiment_id = {p} GROUP BY kind", [experiment_id])
        return {r["kind"]: int(r["n"]) for r in rows}

    def trips(self, experiment_id: str, mode: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trip_results WHERE experiment_id = {p}" + (" AND mode = {p}" if mode else "") + " LIMIT {p}"
        args = [experiment_id] + ([mode] if mode else []) + [int(limit)]
        return [self._decode("trip_results", r) for r in self._run(sql, args)]

    # -- comparisons -------------------------------------------------------
    def compare_experiments(self, baseline_id: str, candidate_id: str) -> dict[str, Any]:
        a, b = self.get_experiment(baseline_id), self.get_experiment(candidate_id)
        if a is None or b is None:
            raise KeyError("unknown experiment id")
        sa, sb = a.get("summary") or {}, b.get("summary") or {}
        same = (a["scenario"], a["params_hash"], a["seed"]) == (b["scenario"], b["params_hash"], b["seed"])
        metrics = {}
        for m in SUMMARY_METRICS:
            x, y = sa.get(m), sb.get(m)
            if x is None or y is None:
                continue
            metrics[m] = _delta(x, y, BETTER.get(m, "lower"))
        return {"baseline": _brief(a), "candidate": _brief(b), "paired": same,
                "note": None if same else "experiments differ in scenario, situation parameters or seed: the difference is not attributable to the controller alone",
                "metrics": metrics}

    def compare_controllers(self, scenario: str, params_hash: str | None = None, baseline: str = "fixed",
                            controllers: Sequence[str] | None = None, suite: str | None = None) -> dict[str, Any]:
        """Controllers on identical situations: per-controller mean/std over seeds, and paired (same seed) deltas against ``baseline``."""
        where, args = ["e.status = 'completed'", "e.scenario = {p}"], [scenario]
        if params_hash:
            where.append("e.params_hash = {p}")
            args.append(params_hash)
        if suite:
            where.append("e.suite = {p}")
            args.append(suite)
        cols = ", ".join(f"s.{m} AS {m}" for m in SUMMARY_METRICS)
        sql = (f"SELECT e.id, e.controller, e.seed, e.params_hash, {cols} FROM experiments e JOIN experiment_summary s ON s.experiment_id = e.id "
               f"WHERE {' AND '.join(where)} ORDER BY e.controller, e.seed")
        rows = self._run(sql, args)
        if controllers:
            rows = [r for r in rows if r["controller"] in controllers or r["controller"] == baseline]
        by_ctrl: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_ctrl.setdefault(r["controller"], []).append(r)
        out: dict[str, Any] = {"scenario": scenario, "baseline": baseline, "paramsHashes": sorted({r["params_hash"] for r in rows}), "controllers": {}}
        base_by_seed = {(r["params_hash"], r["seed"]): r for r in by_ctrl.get(baseline, [])}
        for ctrl, rs in by_ctrl.items():
            stats = {m: _mean_std([r[m] for r in rs if r[m] is not None]) for m in SUMMARY_METRICS}
            entry: dict[str, Any] = {"n": len(rs), "seeds": sorted({r["seed"] for r in rs}), "metrics": stats}
            if ctrl != baseline:
                paired = [(r, base_by_seed[(r["params_hash"], r["seed"])]) for r in rs if (r["params_hash"], r["seed"]) in base_by_seed]
                entry["pairedWithBaseline"] = len(paired)
                entry["deltas"] = {}
                for m in SUMMARY_METRICS:
                    diffs = [(c[m] - b[m]) for c, b in paired if c[m] is not None and b[m] is not None]
                    bases = [b[m] for c, b in paired if c[m] is not None and b[m] is not None]
                    if diffs:
                        entry["deltas"][m] = _paired(diffs, bases, BETTER.get(m, "lower"))
            out["controllers"][ctrl] = entry
        return out


# --------------------------------------------------------------------------- value normalisation
def _plain(row: dict[str, Any]) -> dict[str, Any]:
    """JSON-friendly values: Decimal -> float, datetimes -> ISO-8601 UTC (PostgreSQL returns the session time zone)."""
    from decimal import Decimal

    for k, v in row.items():
        if isinstance(v, Decimal):
            row[k] = float(v)
        elif isinstance(v, datetime):
            row[k] = v.astimezone(timezone.utc).isoformat()
    return row


# --------------------------------------------------------------------------- statistics helpers
def _mean_std(xs: list[float]) -> dict[str, float | int | None]:
    n = len(xs)
    if not n:
        return {"mean": None, "std": None, "n": 0}
    mean = sum(xs) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else None
    return {"mean": mean, "std": std, "n": n}


def _delta(x: float, y: float, better: str) -> dict[str, Any]:
    d = y - x
    pct = (d / x * 100.0) if x else None
    improved = (d > 0) if better == "higher" else (d < 0)
    return {"baseline": x, "candidate": y, "delta": d, "pct": pct, "better": better, "improved": bool(improved) if d != 0 else None}


def _paired(diffs: list[float], bases: list[float], better: str) -> dict[str, Any]:
    n = len(diffs)
    mean = sum(diffs) / n
    sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (n - 1)) if n > 1 else None
    # 95 % interval of the mean paired difference (t quantiles for small n; reported as-is, not as a significance test)
    t95 = {1: None, 2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}.get(n, 2.0 if n > 10 else None)
    ci = (mean - t95 * sd / math.sqrt(n), mean + t95 * sd / math.sqrt(n)) if (sd is not None and t95) else None
    base_mean = sum(bases) / n
    return {"n": n, "meanDelta": mean, "std": sd, "ci95": ci, "pctOfBaseline": (mean / base_mean * 100.0) if base_mean else None,
            "better": better, "improvedInEverySeed": all((d > 0) if better == "higher" else (d < 0) for d in diffs)}


def _brief(exp: dict[str, Any]) -> dict[str, Any]:
    return {k: exp.get(k) for k in ("id", "name", "scenario", "controller", "seed", "params_hash", "status", "suite")}


# --------------------------------------------------------------------------- SQLite
class SqliteStore(ExperimentStore):
    backend = "sqlite"
    ph = "?"

    def __init__(self, path: str | Path | None = None):
        super().__init__()
        self.path = Path(path) if path else Path(os.environ.get("FLOWSTATE_EXPERIMENT_DB") or DEFAULT_SQLITE)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _bucket(self, col: str, step: float) -> str:
        if not step > 0:
            raise ValueError("bucket size must be positive")
        return f"(CAST({col} / {step:.6f} AS INTEGER) * {step:.6f})"

    def _enc(self, table: str, col: str, v: Any) -> Any:
        if v is None:
            return None
        if col in JSON_COLS.get(table, ()) or col in ARRAY_COLS.get(table, ()):
            return json.dumps(v, default=str)
        if col in TS_COLS.get(table, ()):
            return _epoch(v) if isinstance(v, datetime) else float(v)
        if col in BOOL_COLS.get(table, ()):
            return int(bool(v))
        return v

    def _dec_ts(self, v: Any) -> Any:
        return datetime.fromtimestamp(v, timezone.utc).isoformat() if isinstance(v, (int, float)) else v

    def init_schema(self) -> None:
        with self._lock:
            c = self.conn()
            for stmt in T.ddl("sqlite"):
                c.execute(stmt)
            c.commit()

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        cols = [c for c in T.BY_NAME[table].names() if any(c in r for r in rows)]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        data = [[self._enc(table, c, r.get(c)) for c in cols] for r in rows]
        with self._lock:
            c = self.conn()
            try:
                c.executemany(sql, data)
                c.commit()
            except Exception:
                c.rollback()
                raise
        return len(rows)

    def _run(self, sql: str, args: Sequence[Any] = (), *, fetch: bool = True) -> list[dict[str, Any]]:
        # SQLite lacks FILTER on old builds; it is supported since 3.30, so nothing to translate here.
        return super()._run(sql, args, fetch=fetch)


# --------------------------------------------------------------------------- PostgreSQL / Tiger Cloud
class PostgresStore(ExperimentStore):
    backend = "postgres"
    ph = "%s"

    def __init__(self, url: str, *, connect_timeout: int = 8, use_timescale: bool = True):
        super().__init__()
        self.url = url
        self.connect_timeout = connect_timeout
        self.use_timescale = use_timescale
        self.timescale = False

    def _connect(self):
        import psycopg  # optional dependency: pip install "psycopg[binary]"

        return psycopg.connect(self.url, connect_timeout=self.connect_timeout, autocommit=False)

    def _enc(self, table: str, col: str, v: Any) -> Any:
        if v is None:
            return None
        if col in JSON_COLS.get(table, ()):
            return json.dumps(v, default=str)
        if col in TS_COLS.get(table, ()) and not isinstance(v, datetime):
            return datetime.fromtimestamp(float(v), timezone.utc)
        return v

    def init_schema(self) -> None:
        with self._lock:
            c = self.conn()
            try:
                if self.use_timescale:
                    try:
                        c.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                        c.commit()
                    except Exception as exc:  # not installed / no privilege: plain PostgreSQL still works
                        c.rollback()
                        log.info("TimescaleDB extension not available (%s); using plain PostgreSQL tables", str(exc).splitlines()[0])
                self.timescale = bool(c.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'").fetchone())
                c.commit()
                for stmt in T.ddl("postgres", hypertables=self.timescale):
                    c.execute(stmt)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def insert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        cols = [c for c in T.BY_NAME[table].names() if any(c in r for r in rows)]
        with self._lock:
            c = self.conn()
            try:
                with c.cursor() as cur:
                    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
                        for r in rows:
                            copy.write_row([self._enc(table, col, r.get(col)) for col in cols])
                c.commit()
            except Exception:
                c.rollback()
                raise
        return len(rows)


# --------------------------------------------------------------------------- factory
def database_url() -> str | None:
    return os.environ.get("FLOWSTATE_DATABASE_URL") or os.environ.get("TIGER_DATABASE_URL") or None


def open_store(url: str | None = None, *, sqlite_path: str | Path | None = None, init: bool = True) -> ExperimentStore | None:
    """The configured store, or ``None`` when storage is disabled or unreachable (the caller then just skips persistence)."""
    mode = (os.environ.get("FLOWSTATE_STORE") or "").lower()
    if mode == "off":
        return None
    url = url or database_url()
    store: ExperimentStore | None = None
    try:
        if mode != "sqlite" and url:
            store = PostgresStore(url)
            if init:
                store.init_schema()
            else:
                store.conn()
            return store
    except Exception as exc:
        log.warning("experiment database unavailable (%s: %s); %s", type(exc).__name__, str(exc).splitlines()[0] if str(exc) else "", "falling back to SQLite" if mode != "postgres" else "storage disabled")
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass
        if mode == "postgres":
            return None
    try:
        store = SqliteStore(sqlite_path)
        if init:
            store.init_schema()
        return store
    except Exception as exc:  # noqa: BLE001
        log.warning("SQLite experiment store unavailable (%s)", exc)
        return None
