"""Live checks against the real Gemini API (free tier). Skipped unless a key is configured (``GEMINI_API_KEY`` or ``FLOWSTATE_ENV_FILE``).
A handful of requests per run; nothing is printed that could reveal the key."""

import json
import os
from pathlib import Path

import pytest

from intel_support import use_integrations

HAVE = use_integrations()
from flowstate.intelligence.explain import explain, facts_from_evaluation  # noqa: E402
from flowstate.intelligence.gemini import GeminiSettings, GeminiUnavailable, GuardedGemini  # noqa: E402
from flowstate.intelligence.secrets import gemini_api_key  # noqa: E402

EVAL = Path(__file__).resolve().parents[1] / "artifacts" / "rl" / "evaluation_standard.json"
pytestmark = [pytest.mark.skipif(not (HAVE and gemini_api_key()), reason="no Gemini key / experiments package available"),
              pytest.mark.skipif(not EVAL.exists(), reason="stored evaluation not present")]


@pytest.fixture(scope="module")
def guard():
    return GuardedGemini()


def test_live_structured_proposal_validates_against_the_assistants_model(guard):
    from flowstate.experiments import assistant as A

    d = guard.generate_json("Translate the request into a JSON proposal. Controllers: fixed, actuated. Scenarios: rush_hour, normal.",
                            "Compare fixed and actuated signals in rush hour on three seeds.", A.proposal_json_schema())
    p = A.Proposal.model_validate(d)
    assert p.intent == "compare_controllers" and p.scenario == "rush_hour"
    assert guard.status()["counters"].get(f"ok.{guard.s.primary}", 0) + guard.status()["counters"].get("used_fallback_model", 0) >= 1


def test_live_explanation_is_grounded_cached_and_secret_free(guard):
    facts = facts_from_evaluation(json.loads(EVAL.read_text()))
    out = explain(facts, guard)
    assert out["source"] in ("gemini", "measurements") and out["summary"]
    from flowstate.experiments.assistant import numbers_are_grounded

    assert numbers_are_grounded(out["summary"], facts)[0]  # whichever source produced it, every number is a measured one
    before = guard.status()["cache"]["hits"]
    assert explain(facts, guard)["summary"] == out["summary"]
    assert guard.status()["cache"]["hits"] == before + 1
    key = gemini_api_key()
    assert key not in json.dumps(guard.status()) and key not in repr(guard)


def test_live_invalid_key_degrades_to_offline_and_stops_calling():
    bad = GuardedGemini(GeminiSettings(), api_key="AI" + "za" + "NotARealKey_0123456789abcdefghijk")
    with pytest.raises(GeminiUnavailable) as e:
        bad.generate_text("s", "u")
    assert e.value.reason == "auth" and bad.available is False
