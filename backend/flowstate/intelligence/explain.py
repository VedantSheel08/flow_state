"""Grounded explanations of controller comparisons: Gemini may rephrase measured facts, never invent numbers.

``facts_from_evaluation`` flattens a stored evaluation (``artifacts/rl/evaluation_*.json``: identical demand per scenario and seed,
paired statistics) into the only numbers a summary may quote. ``explain`` asks Gemini for a short narrative and accepts it only if
every number in it matches a measured value (the assistant's own ``numbers_are_grounded`` check, imported lazily). In every other
case (no key, Gemini unavailable, an ungrounded number, the experiments module missing) the deterministic summary built here from the
same facts is returned instead, and ``source`` says which one it is. The measurements always travel with the text.
"""

from __future__ import annotations

import json
from typing import Any

from .gemini import GeminiUnavailable

METRICS = ("mean_wait_s", "mean_travel_s", "throughput_vph", "teleports", "collisions")
SYSTEM = ("You explain traffic-simulation results to a city planner in 3 to 5 sentences. Use ONLY numbers that appear in the JSON, quoted exactly "
          "or rounded to at most one decimal place. Say these are simulated, same-demand paired comparisons on a small 2x2 signalised grid. "
          "If a 95% confidence interval includes zero, say the difference is not clear. Name the best controller only if the data supports it. "
          "Do not speculate about causes or about other networks.")


def _label(cid: str) -> str:
    return "rl_ppo" if cid.startswith("rl_ppo@") else cid


def facts_from_evaluation(doc: dict[str, Any]) -> dict[str, Any]:
    s = doc["summary"]
    base = s["baseline"]
    facts: dict[str, Any] = {"baseline": base, "episodes": s["episodes"], "scenarios": s["scenarios"], "ciLevelPercent": 95,  # bootstrap level used by evaluate.summarise
                             "intensity": doc.get("meta", {}).get("intensity"), "controllers": {}, "pairedVsBaseline": {}}
    for cid, blk in s["overall"].items():
        lab = _label(cid)
        facts["controllers"][lab] = {m: round(blk["metrics"][m]["mean"], 2) for m in METRICS if m in blk["metrics"]}
        if cid != base:
            facts["pairedVsBaseline"][lab] = {
                m: {"meanDifference": round(v["mean_diff"], 2), "ci95": [round(v["ci95"][0], 2), round(v["ci95"][1], 2)], "pairs": v["paired"]}
                for m, v in ((m, blk["metrics"][m].get("vs_baseline")) for m in ("mean_wait_s", "mean_travel_s", "throughput_vph", "teleports")) if v}
    return facts


def deterministic_summary(facts: dict[str, Any]) -> str:
    base = facts["baseline"]
    b = facts["controllers"][base]
    parts = [f"Across {facts['episodes']} simulated episodes with identical demand for every controller, {base} had a mean wait of {b['mean_wait_s']} s "
             f"and mean travel time of {b['mean_travel_s']} s."]
    for lab, stats in facts["controllers"].items():
        if lab == base:
            continue
        d = facts["pairedVsBaseline"].get(lab, {}).get("mean_wait_s")
        if not d:
            continue
        lo, hi = d["ci95"]
        verdict = "lower" if hi < 0 else "higher" if lo > 0 else "not clearly different"
        parts.append(f"{lab}: mean wait {stats['mean_wait_s']} s, {d['meanDifference']:+} s against {base} (95% CI {lo} to {hi}), which is {verdict}.")
    parts.append("These are simulated results on a small 2x2 grid, not measurements of a real network.")
    return " ".join(parts)


def explain(facts: dict[str, Any], llm: Any | None) -> dict[str, Any]:
    text, source, rejected, note = deterministic_summary(facts), "measurements", [], None
    if llm is not None and getattr(llm, "available", True):
        try:
            from flowstate.experiments.assistant import numbers_are_grounded

            draft = llm.generate_text(SYSTEM, json.dumps(facts, sort_keys=True))
            ok, bad = numbers_are_grounded(draft, facts)
            if ok and draft.strip():
                text, source = draft.strip(), "gemini"
            else:
                rejected = bad
                note = "Gemini draft discarded: it quoted a number that was not measured" if bad else "Gemini returned no text"
        except GeminiUnavailable as exc:
            note = f"Gemini unavailable ({exc.reason}); deterministic summary used"
        except ImportError:
            note = "grounding check unavailable; deterministic summary used"
    elif llm is not None:
        note = "Gemini not available; deterministic summary used"
    return {"summary": text, "source": source, "rejectedNumbers": rejected, "note": note, "facts": facts}
