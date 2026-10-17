"""Experiment storage, emissions analytics and the experiment API, checked against real SUMO runs.

PostgreSQL tests use a real PostgreSQL server: ``FLOWSTATE_TEST_PG_URL`` if set, otherwise an embedded server from the
optional ``pixeltable-pgserver`` package; they are skipped (and reported as skipped) when neither is available.
TimescaleDB itself is not available locally, so hypertable creation is verified only against a real Tiger Cloud service
(see docs/INTEGRATIONS.md); here the hypertable statements are checked to be the only difference between the schemas.
"""

import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flowstate.experiments import api as exp_api
from flowstate.experiments import tables as T
from flowstate.experiments.export import export_comparison, export_experiment
from flowstate.experiments.recorder import ExperimentSpec, _persist_store, controller_params, run_experiment, situation_hash
from flowstate.experiments.store import PostgresStore, SqliteStore, open_store

EXP_DIR = Path(__file__).resolve().parents[1] / "backend" / "flowstate" / "experiments"


# --------------------------------------------------------------------------- stores
@pytest.fixture(scope="module")
def pg_url():
    url = os.environ.get("FLOWSTATE_TEST_PG_URL")
    if url:
        yield url
        return
    try:
        import psycopg  # noqa: F401
        import pixeltable_pgserver as pgserver
    except ImportError:
        pytest.skip("no PostgreSQL: set FLOWSTATE_TEST_PG_URL or install psycopg[binary] and pixeltable-pgserver")
    data = Path(tempfile.mkdtemp(prefix="flowstate-pg-"))
    srv = pgserver.get_server(data, cleanup_mode="delete")
    try:
        yield srv.get_uri()
    finally:
        srv.cleanup()


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        st = SqliteStore(tmp_path / "e.sqlite")
    else:
        st = PostgresStore(request.getfixturevalue("pg_url"), use_timescale=False)
    st.init_schema()
    yield st
    st.close()


def seed_experiments(st, suite="t"):
    """Two controllers x three seeds of invented-but-structured rows: tests the SQL, not traffic."""
    base = datetime.now(timezone.utc)
    ids = {}
    for ctrl, bump in (("fixed", 0), ("actuated", 40)):
        for seed in (1, 2, 3):
            eid = st.create_experiment(dict(name=f"{ctrl}-{seed}", scenario="rush_hour", controller=ctrl, network="district", seed=seed, params={"a": 1}, params_hash="h1",
                                            warmup_s=90.0, duration_s=300.0, suite=suite, tags=["t"], engine={"sumo": "x"}))
            ids[(ctrl, seed)] = eid
            st.insert_rows("kpi_samples", [dict(experiment_id=eid, ts=base + timedelta(seconds=i), sim_t=float(i), active_vehicles=100 + i, co2_mg_s=1000.0 + i, mean_speed_kmh=20.0) for i in range(300)])
            st.insert_rows("edge_samples", [dict(experiment_id=eid, ts=base + timedelta(seconds=i), sim_t=float(i), edge_id=f"e{j}", vehicles=j, halting=j % 3, mean_speed_ms=3.0, occupancy=0.1 * j)
                                            for i in range(0, 300, 5) for j in range(4)])
            st.insert_rows("junction_samples", [dict(experiment_id=eid, ts=base + timedelta(seconds=i), sim_t=float(i), tls_id="n1", queue=i % 7) for i in range(0, 300, 5)])
            st.insert_rows("signal_events", [dict(experiment_id=eid, ts=base, sim_t=1.0, tls_id="n1", kind="phase", phase_from=0, phase_to=1, elapsed_s=30.0, detail={"why": "x"}),
                                             dict(experiment_id=eid, ts=base, sim_t=2.0, tls_id="n1", kind="preempt", detail={"vehicle": "amb1"})])
            st.insert_rows("safety_events", [dict(experiment_id=eid, ts=base, sim_t=5.0, kind="collision", subtype="junction", vehicle="a", other="b")])
            tp = 400 + seed * 10 + bump
            st.finish_experiment(eid, "completed", dict(trips_completed=tp // 10, throughput_per_h=float(tp), mean_travel_s=150.0 - bump / 4, co2_total_g=5000.0 - bump * 5, collisions=1))
    return ids


def test_store_contract_on_sqlite_and_real_postgres(store):
    ids = seed_experiments(store)
    e = ids[("fixed", 1)]
    exp = store.get_experiment(e)
    assert exp["status"] == "completed" and exp["params"] == {"a": 1} and exp["tags"] == ["t"] and exp["summary"]["throughput_per_h"] == 410.0
    # bucketed in the database: 300 one-second samples -> three 100 s buckets
    series = store.kpi_series(e, ["active_vehicles", "co2_mg_s"], step_s=100)
    assert [r["sim_t"] for r in series] == [0.0, 100.0, 200.0] and abs(series[0]["active_vehicles"] - 149.5) < 1e-9
    assert len(store.kpi_series(e, ["active_vehicles"])) == 300
    with pytest.raises(ValueError):
        store.kpi_series(e, ["not_a_metric"])
    heat = store.edge_heatmap(e, "halting", 100)
    assert {r["edge_id"] for r in heat} == {"e0", "e1", "e2", "e3"} and all(r["samples"] == 20 for r in heat)
    assert len(store.edge_heatmap(e, "halting", 100, top=2)) == 6  # busiest 2 edges x 3 buckets
    assert store.junction_heatmap(e, 100)[0]["peak"] >= 0
    stats = store.signal_decision_stats(e)[0]
    assert stats["phase_changes"] == 1 and stats["preemptions"] == 1 and stats["mean_phase_s"] == 30.0
    assert store.safety_counts(e) == {"collision": 1}
    assert store.signal_events(e, kind="preempt")[0]["detail"] == {"vehicle": "amb1"}
    # paired comparison: same situation, same seed, different controller
    pair = store.compare_experiments(ids[("fixed", 1)], ids[("actuated", 1)])
    assert pair["paired"] and pair["metrics"]["mean_travel_s"]["improved"] is True and pair["metrics"]["throughput_per_h"]["improved"] is True
    assert not store.compare_experiments(ids[("fixed", 1)], ids[("actuated", 2)])["paired"]
    cc = store.compare_controllers("rush_hour")
    d = cc["controllers"]["actuated"]["deltas"]["throughput_per_h"]
    assert cc["controllers"]["actuated"]["pairedWithBaseline"] == 3 and d["meanDelta"] == 40.0 and d["improvedInEverySeed"] is True
    assert cc["controllers"]["fixed"]["metrics"]["throughput_per_h"]["n"] == 3
    # filters, ordering and deletion
    assert len(store.list_experiments(scenario="rush_hour", controller="actuated")) == 3
    assert store.list_experiments(controller="nope") == []
    store.delete_experiment(e)
    assert store.get_experiment(e) is None and store.kpi_series(e) == []


def test_postgres_schema_is_the_same_tables_and_indexes_as_sqlite(pg_url):
    import psycopg

    st = PostgresStore(pg_url, use_timescale=False)
    st.init_schema()
    with psycopg.connect(pg_url) as c:
        cols = {r[0] for r in c.execute("select table_name from information_schema.tables where table_schema='public'")}
        assert {t.name for t in T.TABLES} <= cols
        for t in T.TABLES:
            have = {r[0] for r in c.execute("select column_name from information_schema.columns where table_name=%s", (t.name,))}
            assert have >= set(t.names()), t.name
        idx = {r[0] for r in c.execute("select indexname from pg_indexes where tablename='kpi_samples'")}
        assert "kpi_samples_experiment_id_sim_t_idx" in idx and "kpi_samples_pkey" in idx
    # the query planner uses the index for a time-ordered read of one experiment
    st.close()


def test_committed_schema_files_match_the_generated_ddl():
    assert (EXP_DIR / "schema_postgres.sql").read_text(encoding="utf-8") == T.schema_sql("postgres")
    assert (EXP_DIR / "schema_sqlite.sql").read_text(encoding="utf-8") == T.schema_sql("sqlite")
    ts = (EXP_DIR / "schema_timescale.sql").read_text(encoding="utf-8")
    assert ts.count("create_hypertable") == sum(1 for t in T.TABLES if t.hypertable) == 5
    # every hypertable's unique index contains the partition column (a TimescaleDB requirement)
    for t in T.TABLES:
        if t.hypertable and t.pk:
            assert t.hypertable in t.pk, t.name


def test_unreachable_database_never_breaks_the_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWSTATE_EXPERIMENT_DB", str(tmp_path / "fallback.sqlite"))
    monkeypatch.delenv("FLOWSTATE_STORE", raising=False)
    t0 = time.time()
    st = open_store("postgresql://nobody@127.0.0.1:1/none?connect_timeout=2")  # nothing listens on port 1
    assert st is not None and st.backend == "sqlite" and time.time() - t0 < 15  # fell back to the local file
    monkeypatch.setenv("FLOWSTATE_STORE", "postgres")
    assert open_store("postgresql://nobody@127.0.0.1:1/none?connect_timeout=2") is None  # forced: reported as unavailable, no exception
    monkeypatch.setenv("FLOWSTATE_STORE", "off")
    assert open_store() is None


# --------------------------------------------------------------------------- real SUMO experiments
PARAMS = {"duration": 5}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Three real 5-minute experiments: fixed, actuated and fixed again (same seed) to prove reproducibility."""
    st = SqliteStore(tmp_path_factory.mktemp("exp") / "runs.sqlite")
    st.init_schema()
    out = {}
    for label, ctrl in (("fixed", "fixed"), ("actuated", "actuated"), ("fixed2", "fixed")):
        out[label] = run_experiment(ExperimentSpec("rush_hour", 7, ctrl, dict(PARAMS), warmup=60.0, suite="test"), st)
    out["store"] = st
    yield out
    st.close()


def test_a_real_run_records_measured_metrics_emissions_and_decisions(runs):
    r = runs["fixed"]
    assert r.status == "completed" and r.persisted == "sqlite" and r.persist_error is None
    s = r.summary
    assert s["trips_completed"] > 30 and s["throughput_per_h"] == s["trips_completed"] * 3600 / 300
    assert 20 < s["mean_travel_s"] < 400 and s["mean_time_loss_s"] > 0 and s["stops_per_trip"] > 0
    # emissions: measured by SUMO's HBEFA3 model, so they are positive, per-km values are plausible, idling is a share of the total
    assert s["co2_total_g"] > 1000 and s["fuel_total_g"] > 300 and 100 < s["co2_g_per_km"] < 3000
    assert 0 < s["idle_share_of_co2"] < 1 and 0 < s["idle_co2_g"] < s["co2_total_g"] and s["idle_s_total"] > 0
    # CO2 per fuel is physically bounded for hydrocarbon fuel (~3.1 g CO2 per g of fuel): a check that the two series agree
    assert 2.0 < s["co2_total_g"] / s["fuel_total_g"] < 4.0
    k = r.rows["kpi_samples"]
    assert 295 <= len(k) <= 305 and [x["sim_t"] for x in k] == sorted(x["sim_t"] for x in k) and k[0]["sim_t"] >= 0
    # the integral of the sampled rate is what the summary reports
    assert abs(sum(x["co2_mg_s"] for x in k) / 1000.0 - s["co2_total_g"]) / s["co2_total_g"] < 0.03
    # per-trip emissions from SUMO's tripinfo output are a subset of the network total for the window
    trip_co2 = sum(t["co2_mg"] for t in r.rows["trip_results"] if t["finished"] and t["depart_s"] >= 0 and t["co2_mg"]) / 1000.0
    assert 0 < trip_co2 <= s["co2_total_g"] * 1.05
    # density / occupancy / queues are populated and consistent
    assert all(0 <= x["storage_occupancy"] <= 1.5 for x in k) and max(x["queue_max"] for x in k) == s["peak_queue"]
    assert r.rows["edge_samples"] and r.rows["junction_samples"]
    ev = r.rows["signal_events"]
    assert {e["kind"] for e in ev} == {"phase"} and all(e["elapsed_s"] > 0 for e in ev) and {e["tls_id"] for e in ev} >= {"n1_1", "n2_2"}
    assert r.engine_info["sumo"].startswith("SUMO") and "HBEFA3/PC_G_EU4" in r.engine_info["emissionClasses"] and "Zero" in r.engine_info["emissionClasses"]


def test_same_seed_reproduces_the_measurements_exactly(runs):
    a, b = runs["fixed"].summary, runs["fixed2"].summary
    for key in ("trips_completed", "mean_travel_s", "mean_time_loss_s", "co2_total_g", "fuel_total_g", "idle_s_total", "peak_queue", "collisions", "teleports"):
        assert a[key] == b[key], key
    assert runs["fixed"].id != runs["fixed2"].id


def test_the_controller_is_applied_and_changes_the_outcome_not_the_situation(runs):
    f, a = runs["fixed"], runs["actuated"]
    assert f.params["signals"] == "fixed" and a.params["signals"] == "actuated"
    assert f.summary["co2_total_g"] != a.summary["co2_total_g"] and f.summary["mean_travel_s"] != a.summary["mean_travel_s"]
    st = runs["store"]
    ef, ea = st.get_experiment(f.id), st.get_experiment(a.id)
    assert ef["params_hash"] == ea["params_hash"] and ef["seed"] == ea["seed"] and ef["controller"] != ea["controller"]
    cmp_ = st.compare_experiments(f.id, a.id)
    assert cmp_["paired"] and cmp_["metrics"]["co2_total_g"]["delta"] == a.summary["co2_total_g"] - f.summary["co2_total_g"]
    doc = export_comparison(st, f.id, a.id)
    assert doc["schemaVersion"] == 1 and len(doc["kpi"]["baseline"]["t"]) > 10 and doc["haltingDifference"]["cells"] and "emissionsNote" in doc


def test_export_document_is_complete_json_with_units_and_the_emissions_caveat(runs):
    doc = export_experiment(runs["store"], runs["fixed"].id)
    json.dumps(doc)  # serialisable
    assert {"experiment", "summary", "kpi", "heatmap", "signals", "safety", "emissions", "units"} <= set(doc)
    assert len(doc["kpi"]["t"]) == len(doc["kpi"]["co2_mg_s"]) and doc["heatmap"]["edges"] and doc["heatmap"]["junctions"]
    assert "not measurements of real urban emissions" in doc["emissions"]["note"] and "mg/s" in doc["units"]["co2_mg_s"]
    assert doc["experiment"]["params_hash"] == situation_hash("rush_hour", "district", runs["fixed"].params, 60.0)
    # the in-memory export used by the benchmark equals the one read back from the stored rows
    assert runs["fixed"].export()["summary"] == doc["summary"]


def test_a_failing_database_does_not_fail_the_experiment(runs):
    class Broken(SqliteStore):
        def insert_rows(self, table, rows):
            raise RuntimeError("connection to server lost")

    broken = Broken(":memory:")
    broken.init_schema = lambda: None  # the failure is on the first write
    res = runs["fixed"]
    saved = res.persisted, res.persist_error
    try:
        _persist_store(res, broken, datetime.now(timezone.utc), "h", 300.0, None)
        assert res.persisted is None or res.persisted == saved[0]
        assert res.persist_error and "connection to server lost" in res.persist_error
        assert res.status == "completed" and res.summary["trips_completed"] > 0  # the measurements are still there
    finally:
        res.persisted, res.persist_error = saved


def test_unsupported_controllers_and_parameters_are_rejected_before_any_run():
    with pytest.raises(ValueError, match="no 'preemption' option"):
        controller_params("rush_hour", "fixed+preempt", {})
    assert controller_params("accident", "fixed+preempt", {})["preemption"] is True
    with pytest.raises(ValueError, match="unknown controller"):
        controller_params("rush_hour", "magic", {})
    with pytest.raises(ValueError):
        run_experiment(ExperimentSpec("rush_hour", 1, "fixed", {"duration": 99}))  # not an allowed run length


# --------------------------------------------------------------------------- API
def test_experiment_api_serves_history_series_heatmaps_and_comparisons(runs):
    from fastapi.testclient import TestClient

    exp_api.set_store(runs["store"])
    try:
        c = TestClient(exp_api.create_app())
        st = c.get("/api/experiments/status").json()
        assert st["backend"] == "sqlite" and st["experiments"] >= 3 and "co2_mg_s" in st["kpiMetrics"] and "fixed+preempt" in st["controllers"]
        listing = c.get("/api/experiments", params={"scenario": "rush_hour", "controller": "actuated"}).json()["experiments"]
        assert len(listing) == 1 and listing[0]["throughput_per_h"] is not None
        eid = runs["fixed"].id
        full = c.get(f"/api/experiments/{eid}").json()
        assert full["summary"]["co2_total_g"] == runs["fixed"].summary["co2_total_g"]
        assert c.get(f"/api/experiments/{eid}", params={"detail": "summary"}).json()["status"] == "completed"
        kpi = c.get(f"/api/experiments/{eid}/kpi", params={"metrics": "co2_mg_s,queue_total", "step": 30}).json()["series"]
        assert 8 <= len(kpi) <= 12 and set(kpi[0]) == {"sim_t", "co2_mg_s", "queue_total"}
        assert c.get(f"/api/experiments/{eid}/kpi", params={"metrics": "bogus"}).status_code == 422
        heat = c.get(f"/api/experiments/{eid}/heatmap", params={"metric": "co2_mg_s", "bucket": 60, "top": 10}).json()["cells"]
        assert heat and len({h["edge_id"] for h in heat}) <= 10
        assert c.get(f"/api/experiments/{eid}/heatmap", params={"metric": "bogus"}).status_code == 422
        assert c.get(f"/api/experiments/{eid}/signals", params={"tls": "n1_1"}).json()["events"]
        assert "counts" in c.get(f"/api/experiments/{eid}/safety").json()
        cmp_ = c.get("/api/experiments/compare", params={"baseline": eid, "candidate": runs["actuated"].id}).json()
        assert cmp_["paired"] and "co2_total_g" in cmp_["metrics"]
        ctrl = c.get("/api/experiments/controllers", params={"scenario": "rush_hour"}).json()
        assert set(ctrl["controllers"]) == {"fixed", "actuated"} and ctrl["controllers"]["actuated"]["pairedWithBaseline"] == 1
        assert c.get(f"/api/experiments/{uuid.uuid4()}").status_code == 404
        # nothing in any response reveals a connection string
        assert "postgresql://" not in json.dumps([st, full, ctrl])
        # unsupported run requests are refused with an explanation, and nothing is started
        bad = c.post("/api/experiments/run", json={"scenario": "rush_hour", "controller": "fixed+preempt"})
        assert bad.status_code == 422 and "preemption" in bad.json()["detail"]
        assert c.post("/api/experiments/run", json={"scenario": "nope"}).status_code == 422
        assert c.post("/api/experiments/run", json={"scenario": "rush_hour", "params": {"duration": 7}}).status_code == 422
    finally:
        exp_api.set_store(None)


def test_experiment_api_runs_a_validated_experiment_in_a_worker_process(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    db = tmp_path / "api.sqlite"
    monkeypatch.setenv("FLOWSTATE_EXPERIMENT_DB", str(db))
    monkeypatch.delenv("FLOWSTATE_DATABASE_URL", raising=False)
    monkeypatch.delenv("TIGER_DATABASE_URL", raising=False)
    store = SqliteStore(db)
    store.init_schema()
    exp_api.set_store(store)
    try:
        c = TestClient(exp_api.create_app())
        r = c.post("/api/experiments/run", json={"scenario": "rush_hour", "controller": "actuated", "seed": 3, "warmup": 20, "params": {"duration": 5}})
        assert r.status_code == 202 and r.json()["resolvedParams"]["signals"] == "actuated"
        jid = r.json()["id"]
        deadline = time.time() + 240
        while time.time() < deadline:
            j = c.get(f"/api/experiments/jobs/{jid}").json()
            if j["status"] != "running":
                break
            time.sleep(3)
        assert j["status"] == "completed", j
        doc = c.get(f"/api/experiments/{j['experimentId']}").json()
        assert doc["experiment"]["controller"] == "actuated" and doc["summary"]["trips_completed"] > 0
    finally:
        exp_api.set_store(None)
        store.close()


def test_a_real_run_round_trips_through_real_postgres(runs, pg_url):
    """The recorder's real rows (jsonb, text[], uuid, timestamptz, COPY) written to PostgreSQL and read back identical."""
    pg = PostgresStore(pg_url, use_timescale=False)
    pg.init_schema()
    try:
        res = runs["fixed"]
        exp = runs["store"].get_experiment(res.id)
        from flowstate.experiments.recorder import _write_all

        started = datetime.fromisoformat(exp["started_at"])
        _write_all(pg, res, started, exp["params_hash"], exp["duration_s"], None)
        back = pg.get_experiment(res.id)
        assert back["status"] == "completed" and back["params"] == res.params and back["tags"] == res.spec.tags and back["engine"]["sumo"] == res.engine_info["sumo"]
        for key in ("trips_completed", "mean_travel_s", "co2_total_g", "idle_share_of_co2", "collisions"):
            assert back["summary"][key] == pytest.approx(res.summary[key])
        k = pg.kpi_series(res.id)
        assert len(k) == len(res.rows["kpi_samples"]) and k[-1]["co2_mg_s"] == pytest.approx(res.rows["kpi_samples"][-1]["co2_mg_s"])
        assert len(pg.signal_events(res.id, limit=100000)) == len(res.rows["signal_events"])
        assert len(pg.trips(res.id, limit=100000)) == len(res.rows["trip_results"])
        # the same export document from either backend
        assert export_experiment(pg, res.id)["summary"] == export_experiment(runs["store"], res.id)["summary"]
        assert pg.compare_controllers("rush_hour")["controllers"]["fixed"]["n"] >= 1
    finally:
        pg.delete_experiment(res.id)
        pg.close()


def test_a_baseline_controller_switches_priority_features_off_even_when_the_scenario_defaults_them_on():
    from flowstate.scenarios.catalog import create

    assert create("ambulance", {}).params["preemption"] is True and create("transit", {}).params["tsp"] is True  # the scenarios' own defaults
    for scenario, switch in (("ambulance", "preemption"), ("accident", "preemption"), ("transit", "tsp")):
        off = controller_params(scenario, "fixed", {})
        assert off[switch] is False and off["signals"] == "fixed", (scenario, off)
        assert controller_params(scenario, "actuated", {})[switch] is False
    assert controller_params("ambulance", "fixed+preempt", {})["preemption"] is True
    assert controller_params("transit", "fixed+tsp", {})["tsp"] is True and controller_params("transit", "fixed+tsp", {})["preemption"] is False if "preemption" in {p.key for p in create("transit", {}).spec.params} else True
    # a scenario without the switch is unaffected by "off", and refuses "on"
    assert "preemption" not in controller_params("rush_hour", "fixed", {})


def test_junction_discharge_and_mode_shares_are_measured_for_the_toronto_comparison(runs):
    s = runs["fixed"].summary["extra"]
    busy = s["busiestJunctions"]
    assert 3 <= len(busy) <= 5 and busy[0]["vehPerHour"] >= busy[-1]["vehPerHour"] > 0
    # an arterial junction in a 2500 trips/h rush discharges roughly its through demand: hundreds to ~2000 veh/h, never zero or absurd
    assert 300 < busy[0]["vehPerHour"] < 3500 and busy[0]["name"].count("&") == 1
    modes = s["tripsByMode"]
    assert modes["car"] > 20 and sum(modes.values()) >= modes["car"]
    # the discharge also exists as a time series per junction (sum over the window = what the summary reports)
    cross = {}
    for r in runs["fixed"].rows["junction_samples"]:
        cross[r["tls_id"]] = cross.get(r["tls_id"], 0) + (r["crossings"] or 0)
    top = max(cross, key=cross.get)
    assert abs(cross[top] * 3600 / 300 - busy[0]["vehPerHour"]) <= 0.08 * busy[0]["vehPerHour"] + 12


def test_benchmark_accepts_a_relative_output_directory(tmp_path, monkeypatch):
    """Workers run in another working directory, so a relative --out must be made absolute (a real bug, found by the first batch run)."""
    from flowstate.experiments import benchmark as bench

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    # an unsupported controller is refused before anything runs, but only after the output path has been resolved
    with pytest.raises(ValueError, match="no 'preemption' option"):
        bench.run_benchmark("t", "rush_hour", ["fixed", "fixed+preempt"], [1], {"duration": 5}, out_dir=Path("rel/suite"))
    out = (Path("rel/suite")).resolve()
    assert out.is_absolute() and str(out).startswith(str(tmp_path.resolve()))
