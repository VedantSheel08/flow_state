"""Intelligence contract v1, model health, calibration, benchmark integrity and Gemini fallback (no SUMO unless stated)."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from flowstate.lab import benchmark as B
from flowstate.lab import contract, export, health
from flowstate.lab import forecast as F
from flowstate.lab.sim import FEATURES

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
ART = F.lab_dir()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A private copy of the artifact directory, so corruption tests can never touch the real model."""
    d = tmp_path / "lab"
    shutil.copytree(ART, d, ignore=shutil.ignore_patterns("comparison_eval.json", "dataset_manifest.json"))
    shutil.copy(ART / "dataset_manifest.json", d / "dataset_manifest.json")
    monkeypatch.setattr(F, "lab_dir", lambda: d)
    monkeypatch.setattr(health, "lab_dir", lambda: d)
    monkeypatch.setattr(health, "LATENCY_BUDGET_MS", 5000.0)  # the machine running tests may be saturated; latency has its own test below
    F.Forecaster._default = False
    yield d
    F.Forecaster._default = False


def test_registered_artifact_is_the_verified_baseline():
    """The trained model must stay byte-identical to the one the baseline numbers were measured on."""
    mf = json.loads((ART / "MODEL_MANIFEST.json").read_text())
    assert mf["artifact_sha256"] == B.sha256_file(ART / "forecaster.pkl") and mf["artifact_sha256"].startswith("b624154e4e128faa")
    e = json.loads((ART / "forecast_eval.json").read_text())
    assert e["test"]["p30"]["model"]["mae"] == 2.232 and e["test"]["p60"]["model"]["mae"] == 2.591
    assert mf["features"] == list(FEATURES)


def test_health_ok_and_detects_each_failure_mode(sandbox):
    r = health.check()
    assert r["status"] == "ok" and r["usable"] and not r["failed"]
    # 1. invalid artifact version (manifest from the future)
    mf = json.loads((sandbox / "MODEL_MANIFEST.json").read_text())
    (sandbox / "MODEL_MANIFEST.json").write_text(json.dumps({**mf, "schema": "flowstate.model-manifest/9"}))
    r = health.check()
    assert "manifest_version_supported" in r["failed"] and r["status"] == "unavailable"
    # 2. feature schema drift
    (sandbox / "MODEL_MANIFEST.json").write_text(json.dumps({**mf, "features": mf["features"][:-1]}))
    assert "feature_schema_matches_code" in health.check()["failed"]
    # 3. tampered artifact: refused before it is ever unpickled
    (sandbox / "MODEL_MANIFEST.json").write_text(json.dumps(mf))
    raw = (sandbox / "forecaster.pkl").read_bytes()
    (sandbox / "forecaster.pkl").write_bytes(raw[:-10] + b"0123456789")
    r = health.check()
    assert r["status"] == "unavailable" and {"artifact_hash_matches_manifest", "artifact_loads"} <= set(r["failed"])
    assert F.Forecaster.load_default() is None and "sha256" in F.Forecaster.load_error
    # 4. missing artifact
    (sandbox / "forecaster.pkl").unlink()
    r = health.check()
    assert r["status"] == "unavailable" and r["failed"][0] == "artifact_present"


def test_health_detects_broken_inference(sandbox, monkeypatch):
    assert health.check()["status"] == "ok"
    fc = F.Forecaster.load_default()
    monkeypatch.setattr(fc.fast["p30"], "predict", lambda X: np.full(len(X), np.nan))
    assert "inference_runs_finite_in_range" in health.check()["failed"]
    monkeypatch.setattr(fc.fast["p30"], "predict", lambda X: np.full(len(X), 3.0))  # finite but wrong: only the golden vectors catch it
    r = health.check()
    assert "golden_inference_reproduced" in r["failed"] and r["status"] == "unavailable"


def test_health_flags_slow_inference_as_degraded_but_usable(sandbox, monkeypatch):
    import time

    fc = F.Forecaster.load_default()
    real = fc.predict
    monkeypatch.setattr(fc, "predict", lambda X: (time.sleep(0.02), real(X))[1])
    monkeypatch.setattr(health, "LATENCY_BUDGET_MS", 5.0)
    r = health.check()
    assert r["failed"] == ["latency_within_budget"] and r["status"] == "degraded" and r["usable"]


def test_calibration_must_match_artifact(sandbox):
    cal = json.loads((sandbox / "calibration.json").read_text())
    (sandbox / "calibration.json").write_text(json.dumps({**cal, "model_sha256": "0" * 64}))
    F.Forecaster._default = False
    fc = F.Forecaster.load_default()
    assert fc.calibration is None  # a stale calibration is ignored, never guessed
    out = fc.predict_full(np.zeros((16, len(FEATURES)), dtype=np.float32) + 1)
    assert "lo30" not in out
    r = health.check()
    assert r["status"] == "degraded" and r["usable"] and r["failed"] == ["calibration_bound_to_artifact"]


def test_intervals_are_ordered_in_range_and_cover_on_heldout():
    fc = F.Forecaster.load_default()
    X = np.load(F.data_dir() / "dataset.npz")["test_X"][::97][:3000]
    out = fc.predict_full(X)
    cap = X[:, FEATURES.index("capacity")]
    for h in (30, 60):
        assert (out[f"lo{h}"] >= 0).all() and (out[f"hi{h}"] <= cap + 1e-6).all() and (out[f"lo{h}"] <= out[f"hi{h}"]).all()
    bd = json.loads((ART / "forecast_breakdown.json").read_text())
    assert bd["model_sha256"] == fc.sha256
    for h in ("30", "60"):  # measured coverage on test seeds is close to nominal overall and in every scenario
        assert abs(bd["horizons"][h]["overall"]["interval_coverage"] - 0.9) < 0.02
        for s, c in bd["horizons"][h]["by_scenario"].items():
            assert c["interval_coverage"] > 0.85, (h, s)


def test_split_has_no_episode_or_seed_overlap():
    man = json.loads((ART / "dataset_manifest.json").read_text())
    seeds = {sp: set() for sp in ("train", "val", "test")}
    for k in man["episodes"]:
        sp, _, seed, _ = k.split(":", 3)
        seeds[sp].add(int(seed))
    assert not (seeds["train"] & seeds["val"]) and not (seeds["train"] & seeds["test"]) and not (seeds["val"] & seeds["test"])
    d = np.load(F.data_dir() / "dataset.npz")
    assert not (set(np.unique(d["train_e"])) & set(np.unique(d["test_e"]))) and not (set(np.unique(d["val_e"])) & set(np.unique(d["test_e"])))
    assert {"rush_ramp"} == {k.split(":")[1] for k in man["episodes"] if k.split(":")[1] not in ("balanced", "ns_heavy", "ew_heavy", "pulse")}
    assert all(k.split(":")[0] == "test" for k in man["episodes"] if k.split(":")[1] == "rush_ramp")


def test_report_labels_and_separation():
    q = [0.0] * 16
    q[3] = 30.0
    cap = [a["capacity"] for a in contract.approach_table()]
    fc = {"p30": [0.0] * 16, "p60": [0.0] * 16, "lo30": [0.0] * 16, "hi30": [0.0] * 16, "lo60": [0.0] * 16, "hi60": [0.0] * 16}
    fc["p30"][5] = cap[5] * 0.45
    fc["hi30"][5] = cap[5] * 0.7
    rep = contract.build_report(t=100, queues=q, forecast=fc, model=contract.model_block(health.status()) | {"alert": {"30": {"tau": 0.425, "heldOutPrecision": 0.67, "heldOutRecall": 0.66}}},
                                controller={"id": "ml_forecast", "mlInControl": True}, decisions=[{"t": 95, "junction": "A0", "action": "held_longer", "evidence": {}}])
    assert rep["schema"] == contract.SCHEMA == "flowstate.intelligence/v1"
    assert rep["observed"]["kind"] == "observed" and rep["predicted"]["kind"] == "prediction" and rep["interventions"]["kind"] == "controller_intervention"
    assert "p30" not in json.dumps(rep["observed"])  # observations never carry forecast fields
    assert rep["predicted"]["approaches"][5]["p30"]["targetT"] == 130 and rep["predicted"]["approaches"][5]["p30"]["possibleSpillback"] is True
    kinds = {(w["kind"], w["basis"]) for w in rep["warnings"]}
    assert ("bottleneck_now", "observed") in kinds and ("spillback_risk", "predicted") in kinds
    risk = next(w for w in rep["warnings"] if w["kind"] == "spillback_risk")
    assert risk["ruleHeldOut"] == {"precision": 0.67, "recall": 0.66} and "not a measurement" in risk["message"]
    assert contract.build_report(t=1, queues=q)["predicted"] is None  # no model: predictions are null, not zeros


def test_api_v1_end_to_end(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from flowstate.lab import app as A
    from flowstate.lab import record as R

    with TestClient(A.app) as c:
        assert c.get("/api/intelligence/v1/").json()["versions"] == ["v1"]
        m = c.get("/api/intelligence/v1/model").json()
        assert m["health"]["status"] == "ok" and m["model"]["calibrated"] and m["gemini"]["inControlLoop"] is False and m["gemini"]["mode"] == "offline_fallback"
        sk = m["model"]["skillByControlRegime"]
        assert sk["60"]["queue_heuristic"] < 0.1 < sk["60"]["fixed_time"] and sk["30"]["queue_heuristic"] > 0.4  # the documented 60 s weakness under adaptive control
        assert len(c.get("/api/intelligence/v1/approaches").json()["approaches"]) == 16
        assert c.get("/api/intelligence/v1/health?deep=true").json()["status"] == "ok"
        b = c.get("/api/intelligence/v1/benchmarks").json()
        assert b["schema"] == "flowstate.benchmark-export/1" and b["forecast"]["horizons"]["30"]["byScenario"] and b["controllers"]["pairs"]["ml_vs_heuristic"]
        assert c.get("/api/intelligence/v1/benchmarks?section=nope").status_code == 404
        assert c.get("/api/intelligence/v1/replays/..%2Fx/report").status_code in (400, 404)
        ids = list(R.index())
        ml = next((i for i in ids if i.startswith("queue_heuristic__ml_forecast")), None)
        if ml:  # replays are local, git-ignored data
            r = c.get(f"/api/intelligence/v1/replays/{ml}/report", params={"t": 300, "side": "right"}).json()
            assert r["interventions"]["controller"]["mlInControl"] is True and r["predicted"]["model"]["version"] == "1.0.0"
            s = c.get(f"/api/intelligence/v1/replays/{ml}/series").json()
            assert len(s["observed"]["t"]) == len(s["observed"]["networkQueued"]) and s["predicted30"]["targetT"][0] == s["predicted30"]["madeAtT"][0] + 30
        e = c.post("/api/intelligence/v1/explain", json={"question": "How accurate is the forecast and where is it weak?"}).json()
        assert e["source"] == "measurements" and "under-predict" in e["answer"]


def test_gemini_failures_always_fall_back(monkeypatch):
    """Missing key, rate limit, 401/billing and timeouts all end in a deterministic answer, never an exception."""
    import httpx

    from flowstate.intelligence.gemini import GuardedGemini
    from flowstate.lab import ask as K

    facts = lambda r, l: {"scenarios": {"pulse": "Pulse"}, "forecastBenchmark": {"30": {"overall": {"mae": 2.2, "persistence_mae": 4.4, "skill_vs_persistence": 0.49, "bias": -0.1, "interval_coverage": 0.9}, "byTrueSeverityBias": {"spillback": -6.6}, "alertRuleHeldOut": None},
                                                                                "60": {"overall": {"mae": 2.6, "persistence_mae": 3.5, "skill_vs_persistence": 0.25, "bias": -0.2, "interval_coverage": 0.9}}}}
    monkeypatch.setattr(K, "facts", facts)

    class Boom:
        def __init__(self, exc):
            self.exc = exc

        def generate_json(self, *a):
            raise self.exc

    for exc in (RuntimeError("Gemini returned 429 quota"), RuntimeError("Gemini returned 402 billing"), RuntimeError("Gemini returned 401"), httpx.ReadTimeout("slow"), ValueError("garbage")):
        g = GuardedGemini(api_key="k-not-real", client_factory=lambda m, t, k, e=exc: Boom(e))
        monkeypatch.setattr(K, "_guard", lambda g=g: g)
        r = K.answer("How accurate is the forecast?", None, None)
        assert r["source"] == "measurements" and "2.2" in r["answer"], exc
    monkeypatch.setattr(K, "_guard", lambda: GuardedGemini(api_key=""))
    assert K.answer("How accurate is the forecast?", None, None)["source"] == "measurements"


def test_forecast_aware_decisions_carry_evidence():
    """Behaviour is unchanged by evidence logging (same decisions as the heuristic when the forecast adds nothing) and every ML change is explained."""
    from flowstate.lab.control import ForecastAware
    from flowstate.lab.sim import LabSim
    from flowstate.rl import config as C
    from flowstate.rl.controllers import QueueResponsive

    sim = LabSim("t-evid")
    try:
        sim.reset("pulse", 3002, 300, 0, 1.18)
        base, ml = QueueResponsive(), ForecastAware(beta=1.0, bound=6.0, spill=0.3)
        ml.reset(sim)
        sim.forecaster = F.Forecaster.load_default()
        for _ in range(40):
            sim.observe()
            plain = base.decide(sim)
            act = ml.decide(sim)
            for k in ml.last_changed:
                assert act[k] != plain[k]
            assert len(ml.last_evidence) == len(ml.last_changed)
            for jid, a in zip(C.CONTROLLED, act):
                if a:
                    sim.request(jid)
            sim.advance(C.DECISION_S)
        assert ml.changed == len(ml.evidence_log) == len(ml.log)
        for e in ml.evidence_log:
            assert e["action"] in ("switched_earlier", "held_longer") and {"current", "next"} <= set(e)
            assert abs(e["current"]["forecastShift"]) <= 6.0 and e["current"]["mlDemand"] == pytest.approx(e["current"]["baseDemand"] + e["current"]["forecastShift"], abs=0.02)
    finally:
        sim.close()


def test_export_is_compact_and_sourced():
    b = export.build(write=False)
    assert len(json.dumps(b)) < 120_000 and b["model"]["sha256"] == F.Forecaster.load_default().sha256
    h = b["forecast"]["horizons"]["30"]
    assert {r["scenario"] for r in h["byScenario"]} == {"balanced", "ns_heavy", "ew_heavy", "pulse", "rush_ramp"}
    assert {r["junction"] for r in h["byJunction"]} == {"A0", "A1", "B0", "B1"} and [r["severity"] for r in h["byTrueSeverity"]] == list(contract.SEVERITY)
    assert h["overall"]["episodeBootstrap"]["gap_model_minus_persistence_ci95"][1] < 0  # the model beats persistence beyond episode-level noise


def _integrations_client_class():
    """The real GeminiClient from task/integrations, if it is on the path (or FLOWSTATE_INTEGRATIONS_PATH points at a checkout's backend/flowstate)."""
    import os

    import flowstate

    extra = os.environ.get("FLOWSTATE_INTEGRATIONS_PATH")
    if extra and extra not in flowstate.__path__:
        flowstate.__path__.append(extra)
    try:
        from flowstate.experiments.assistant import GeminiClient
    except ImportError:
        pytest.skip("flowstate.experiments (task/integrations) is not installed; set FLOWSTATE_INTEGRATIONS_PATH to test the real client")
    return GeminiClient


def test_real_gemini_client_through_guard_and_grounding(monkeypatch):
    """Ask -> GuardedGemini -> the real REST client over a mocked transport: success is grounded and cached, every HTTP failure falls back."""
    import httpx

    from flowstate.intelligence.gemini import GuardedGemini
    from flowstate.lab import ask as K

    Client = _integrations_client_class()
    monkeypatch.setattr(K, "facts", lambda r, l: {"scenarios": {"pulse": "Pulse"}, "forecastBenchmark": {"30": {"overall": {"mae": 2.23, "persistence_mae": 4.38}}}})
    calls = {"n": 0, "keys": set()}
    mode = {"status": 200, "answer": "The 30 s forecast error is 2.23 vehicles against 4.38 for persistence."}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        calls["keys"].add(req.headers.get("x-goog-api-key"))
        assert "key=" not in str(req.url)  # the credential travels in a header only
        if mode["status"] != 200:
            return httpx.Response(mode["status"], json={"error": {"message": "nope"}})
        body = json.dumps({"answer": mode["answer"], "scenario_request": None})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": body}]}}]})

    guard = GuardedGemini(api_key="k-not-real", client_factory=lambda m, t, k: Client(api_key=k, model=m, timeout=t, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(K, "_guard", lambda: guard)
    r = K.answer("How accurate is the forecast?", None, None)
    assert r["source"] == "gemini" and "2.23" in r["answer"]
    n = calls["n"]
    assert K.answer("How accurate is the forecast?", None, None)["source"] == "gemini" and calls["n"] == n  # served from the guard's cache
    mode["answer"] = "It is wrong 41.7 percent of the time."  # a number nobody measured
    r = K.answer("Is it reliable?", None, None)
    assert r["source"] == "measurements" and r["rejectedNumbers"] == ["41.7"]
    for status in (429, 500, 401):
        mode["status"] = status
        r = K.answer(f"question for status {status}", None, None)
        assert r["source"] == "measurements" and r["answer"], status
    assert calls["keys"] == {"k-not-real"}
