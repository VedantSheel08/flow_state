"""Grounded explanations over the stored held-out evaluation: Gemini may rephrase measured numbers, nothing else gets through."""

import json
from pathlib import Path

import pytest

from intel_support import use_integrations

HAVE = use_integrations()  # before importing the guard (see test_intelligence_gemini.py)
from flowstate.intelligence.explain import deterministic_summary, explain, facts_from_evaluation  # noqa: E402
from flowstate.intelligence.gemini import GeminiUnavailable  # noqa: E402

EVAL = Path(__file__).resolve().parents[1] / "artifacts" / "rl" / "evaluation_standard.json"
pytestmark = pytest.mark.skipif(not EVAL.exists(), reason="stored evaluation not present")


class LLM:
    def __init__(self, text=None, exc=None, available=True):
        self.text, self.exc, self.available, self.calls = text, exc, available, 0

    def generate_text(self, system, user):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.text


@pytest.fixture(scope="module")
def facts():
    return facts_from_evaluation(json.loads(EVAL.read_text()))


def test_facts_come_straight_from_the_measured_evaluation(facts):
    doc = json.loads(EVAL.read_text())
    cid = next(c for c in doc["summary"]["overall"] if c.startswith("rl_ppo@"))
    assert "rl_ppo" in facts["controllers"] and cid not in facts["controllers"]  # explicit-checkpoint id is reported as rl_ppo
    assert facts["controllers"]["fixed_time"]["mean_wait_s"] == round(doc["summary"]["overall"]["fixed_time"]["metrics"]["mean_wait_s"]["mean"], 2)
    assert facts["pairedVsBaseline"]["queue_heuristic"]["mean_wait_s"]["pairs"] == 50


def test_deterministic_summary_uses_only_measured_numbers_and_says_it_is_simulated(facts):
    text = deterministic_summary(facts)
    assert "simulated" in text and str(facts["controllers"]["fixed_time"]["mean_wait_s"]) in text


def test_without_a_model_the_deterministic_summary_is_returned(facts):
    out = explain(facts, None)
    assert out["source"] == "measurements" and out["summary"] == deterministic_summary(facts) and out["facts"] is facts


def test_unavailable_gemini_falls_back_with_the_reason(facts):
    out = explain(facts, LLM(available=False))
    assert out["source"] == "measurements" and "not available" in out["note"]
    if HAVE:
        out = explain(facts, LLM(exc=GeminiUnavailable("billing", "credits")))
        assert out["source"] == "measurements" and "billing" in out["note"]


@pytest.mark.skipif(not HAVE, reason="needs the assistant's grounding check (FLOWSTATE_INTEGRATIONS_PATH)")
def test_a_grounded_gemini_draft_is_accepted(facts):
    w = facts["controllers"]["fixed_time"]["mean_wait_s"]
    out = explain(facts, LLM(f"Fixed-time had a mean wait of {w} s in these simulated, same-demand runs."))
    assert out["source"] == "gemini" and out["rejectedNumbers"] == []


@pytest.mark.skipif(not HAVE, reason="needs the assistant's grounding check (FLOWSTATE_INTEGRATIONS_PATH)")
def test_a_draft_with_an_invented_number_is_discarded(facts):
    out = explain(facts, LLM("The heuristic cut waiting time by 61.7 s."))
    assert out["source"] == "measurements" and "61.7" in out["rejectedNumbers"] and "61.7" not in out["summary"]
