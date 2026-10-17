"""The /api/intelligence router: status without key material, grounded explanations from stored evaluations, safe degradation."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from intel_support import use_integrations

HAVE = use_integrations()
from flowstate.api.app import app  # noqa: E402
from flowstate.intelligence import api as I  # noqa: E402
from flowstate.intelligence.gemini import GeminiSettings, GuardedGemini  # noqa: E402

KEY = "AI" + "za" + "FakeKeyForTests0123456789abcdefgh"
EVAL = Path(__file__).resolve().parents[1] / "artifacts" / "rl" / "evaluation_standard.json"


@pytest.fixture
def client(monkeypatch):
    I._guard = None
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("FLOWSTATE_ENV_FILE", raising=False)
    yield TestClient(app)
    I._guard = None


def test_router_is_mounted_and_status_without_a_key_is_honest_and_secret_free(client):
    r = client.get("/api/intelligence/status")
    assert r.status_code == 200
    j = r.json()
    assert j["gemini"]["configured"] is False and j["gemini"]["available"] is False
    assert j["defaultController"] == "queue_heuristic"  # PPO is not published; the verified heuristic stays the default
    assert "interpretation only" in j["layer"]


@pytest.mark.skipif(not EVAL.exists(), reason="stored evaluation not present")
def test_explain_without_a_key_returns_the_deterministic_summary_with_measurements(client):
    j = client.post("/api/intelligence/explain", json={"evaluation": "standard"}).json()
    assert j["source"] == "measurements" and "simulated" in j["summary"] and j["facts"]["controllers"]["fixed_time"]["mean_wait_s"] > 0


def test_explain_accepts_only_known_stored_evaluations(client):
    assert client.post("/api/intelligence/explain", json={"evaluation": "../../etc/passwd"}).status_code == 422


def test_plan_rejects_empty_and_oversized_text(client):
    assert client.post("/api/intelligence/plan", json={"text": ""}).status_code == 422
    assert client.post("/api/intelligence/plan", json={"text": "x" * 2001}).status_code == 422


@pytest.mark.skipif(not EVAL.exists(), reason="stored evaluation not present")
def test_explain_with_a_working_model_is_grounded_and_a_failing_one_degrades(client, monkeypatch):
    class Ok:
        def generate_text(self, s, u):
            return "Fixed-time had a mean wait of 83.6 s in these simulated runs."

    I._guard = GuardedGemini(GeminiSettings(), api_key=KEY, client_factory=lambda *a: Ok())
    out = client.post("/api/intelligence/explain", json={"evaluation": "standard"}).json()
    assert out["source"] == ("gemini" if HAVE else "measurements")
    s = client.get("/api/intelligence/status").text
    assert KEY not in s  # status never carries key material

    class Down:
        def generate_text(self, s, u):
            raise RuntimeError("Gemini API returned 402: Your prepayment credits are depleted.")

    I._guard = GuardedGemini(GeminiSettings(), api_key=KEY, client_factory=lambda *a: Down())
    out = client.post("/api/intelligence/explain", json={"evaluation": "standard"}).json()
    assert out["source"] == "measurements" and KEY not in json.dumps(out)
    if HAVE:
        assert I.get_guard().available is False  # billing problem: the breaker keeps Gemini closed
    else:  # without the grounding check nothing Gemini wrote could be verified, so it is not even asked (no API spend)
        assert I.get_guard().status()["counters"] == {} and I.get_guard().available is True
