"""Ask the lab: Gemini explains measured results and proposes scenarios; it never touches signal control.

Uses the existing guarded client (:func:`flowstate.intelligence.api.get_guard`: server-side key, cache, rate limit, timeouts,
breakers). The model only sees *facts* assembled here from measured artifacts (paired evaluation, the selected replay, the
live snapshot, the forecaster's held-out scores). A Gemini answer is accepted only if every number in it occurs in those facts;
otherwise, or whenever Gemini is unavailable, a deterministic answer built from the same facts is returned and ``source`` says so.
A proposed scenario is validated against the catalog before it is offered to the browser (which still needs a user click to run it).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..rl.demand import SCENARIOS
from .forecast import lab_dir

SYSTEM = ("You are FlowState's traffic analyst. Answer in at most 5 sentences for a city-planning audience. Use ONLY numbers present in the "
          "FACTS JSON (quote them exactly or rounded to one decimal). These are SUMO simulations on a small 2x2 signalised grid, not a real "
          "city. Distinguish measured values from forecasts. If a paired difference's 95% CI includes zero, say the difference is not clear. "
          "Never claim the ML controller helped unless the facts show it. If the user asks for a scenario, fill scenario_request with one "
          "of the scenario ids in FACTS.scenarios; otherwise set it to null.")
SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"},
    "scenario_request": {"type": "object", "nullable": True, "properties": {
        "scenario": {"type": "string"}, "intensity": {"type": "number"}, "left": {"type": "string"}, "right": {"type": "string"}}}},
    "required": ["answer"]}
_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _guard() -> Any:
    from ..intelligence.api import get_guard

    return get_guard()


def guard_status() -> dict[str, Any]:
    try:
        s = _guard().status()
    except Exception as exc:  # noqa: BLE001
        return {"configured": False, "error": type(exc).__name__}
    try:
        import flowstate.experiments.assistant  # noqa: F401
        client = True
    except ImportError:
        client = False
    return {**s, "restClientInstalled": client, "inControlLoop": False}


def _numbers(obj: Any) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _numbers(v)]
    if isinstance(obj, (list, tuple)):
        return [x for v in obj for x in _numbers(v)]
    if isinstance(obj, str):
        return [float(m) for m in _NUM.findall(obj)]
    return []


def grounded(text: str, facts: dict[str, Any]) -> tuple[bool, list[str]]:
    known = _numbers(facts)
    bad = []
    for m in _NUM.findall(text):
        v = float(m)
        if v in (0.0, 1.0, 2.0, 30.0, 60.0, 95.0) or v.is_integer() and 1990 <= v <= 2100:  # horizons, CI level, "2x2"
            continue
        if not any(abs(v - k) <= max(0.051, 0.0051 * abs(k)) or abs(abs(v) - abs(k)) <= 0.051 for k in known):
            bad.append(m)
    return not bad, bad


def facts(replay_id: str | None, live: dict[str, Any] | None) -> dict[str, Any]:
    from . import record as R

    out: dict[str, Any] = {"scenarios": {k: s.title for k, s in SCENARIOS.items()},
                           "controllers": {"fixed_time": "fixed-time 30 s", "queue_heuristic": "queue-responsive heuristic, no ML",
                                           "ml_forecast": "queue heuristic + bounded ML queue forecast"}}
    f = lab_dir() / "comparison_eval.json"
    if f.exists():
        ev = json.loads(f.read_text())
        out["pairedEvaluation"] = _trim_eval(ev.get("summary") or {})
    f = lab_dir() / "forecast_eval.json"
    if f.exists():
        fe = json.loads(f.read_text())
        out["forecaster"] = {"kind": fe["kind"], "heldOutMAE_30s": fe["test"]["p30"]["model"]["mae"], "persistenceMAE_30s": fe["test"]["p30"]["persistence"]["mae"],
                             "heldOutMAE_60s": fe["test"]["p60"]["model"]["mae"], "persistenceMAE_60s": fe["test"]["p60"]["persistence"]["mae"],
                             "inferenceMsPerDecision": fe["inference_ms_per_decision"]["mean"]}
    try:  # measured forecasting benchmarks (held-out): what the model gets right and where it is weak
        from . import export, health

        fx = export.load().get("forecast")
        if fx:
            out["forecastBenchmark"] = {h: {"overall": {k: b["overall"][k] for k in ("mae", "persistence_mae", "skill_vs_persistence", "bias", "interval_coverage") if k in b["overall"]},
                                            "byScenario": {r["scenario"]: {"mae": r["mae"], "persistence_mae": r["persistence_mae"], "skill": r["skill_vs_persistence"]} for r in b["byScenario"]},
                                            "byTrueSeverityBias": {r["severity"]: r["bias"] for r in b["byTrueSeverity"]},
                                            "alertRuleHeldOut": next((v for k, v in b["warnings"].items() if k.startswith("model_point_forecast_tuned")), None)}
                                        for h, b in fx["horizons"].items()}
            out["forecastBenchmark"]["note"] = "bias < 0 means the model under-predicts; 30 and 60 are forecast horizons in seconds; interval_coverage is measured on test seeds"
        out["modelHealth"] = health.status()["health"]
    except (OSError, KeyError, ValueError):  # a missing or stale artifact: the answer simply has fewer facts (programming errors are not swallowed)
        pass
    if replay_id:
        s = R.index().get(replay_id)
        if s:
            out["replay"] = {"scenario": s["scenario"], "seed": s["seed"], "intensity": s["intensity"], "left": s["left"], "right": s["right"],
                             "mlChangedDecisions": s.get("ml"), "measured": {r["key"]: {"left": r["left"], "right": r["right"], "diff": r["diff"], "pct": r["pct"]}
                                                                             for r in s["diff"]}}
    if live:
        obs = live.get("obs") or {}
        q, p30 = obs.get("q") or [], obs.get("p30") or []
        geo_keys = _approach_keys()
        top = sorted(range(len(q)), key=lambda i: -q[i])[:3]
        out["live"] = {"scenario": live["scenario"], "controller": live["controller"], "simTime_s": live["t"], "measuredNow": live["kpis"],
                       "worstApproachesNow": [{"approach": geo_keys[i] if i < len(geo_keys) else i, "queuedVehicles": q[i],
                                               "forecastIn30s": p30[i] if i < len(p30) else None} for i in top]}
    return out


_EVAL_PAIRS = ("ml_vs_heuristic", "ev_rule_effect_on_heuristic", "ml_ev_vs_heuristic", "heuristic_vs_fixed")
_EVAL_METRICS = ("mean_wait_s", "mean_wait_civilian_s", "mean_travel_s", "queue_total_mean_veh", "ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s")


def _trim_eval(s: dict[str, Any]) -> dict[str, Any]:
    """Only the pairs and metrics an answer can use: keeps the prompt small (every number kept is a measured value)."""
    out: dict[str, Any] = {"episodes": s.get("episodes"), "arms": s.get("arms")}
    for p in _EVAL_PAIRS:
        if p in s:
            out[p] = {m: {k: s[p][m][k] for k in ("left_mean", "right_mean", "mean_diff", "ci95", "pairs")} for m in _EVAL_METRICS if m in s[p]}
    return out


def _approach_keys() -> list[str]:
    from .contract import approach_table

    return [a["key"] for a in approach_table()]


def deterministic(question: str, F: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    q = question.lower()
    parts: list[str] = []
    req = None
    if any(w in q for w in ("scenario", "simulate", "run ", "try", "test")):
        sc = next((k for k in SCENARIOS if k.replace("_", " ") in q or SCENARIOS[k].title.lower() in q), None)
        if sc is None:
            sc = "rush_ramp" if any(w in q for w in ("rush", "peak", "heavy", "busy")) else "pulse" if "pulse" in q else "balanced"
        inten = 1.18 if any(w in q for w in ("heavy", "stress", "gridlock", "congest", "rush")) else 1.0
        req = {"scenario": sc, "intensity": inten, "left": "queue_heuristic", "right": "ml_forecast"}
        parts.append(f"Proposed paired run: {SCENARIOS[sc].title} at demand intensity {inten}, heuristic without ML on the left and with the ML forecast on the right.")
    if "live" in F and any(w in q for w in ("why", "congest", "queue", "bottleneck", "jam", "now")):
        L = F["live"]
        w = L["worstApproachesNow"]
        if w:
            parts.append(f"At {L['simTime_s']:.0f} s the longest measured queue is on approach {w[0]['approach']} with {w[0]['queuedVehicles']} vehicles"
                         + (f", forecast {w[0]['forecastIn30s']} in 30 s." if w[0]['forecastIn30s'] is not None else "."))
        parts.append(f"{L['measuredNow']['halting']} vehicles are halted on approaches and {L['measuredNow']['pending']} are waiting to enter; "
                     "congestion builds where arrivals exceed what a green phase can discharge, and backs up when an exit street fills.")
    if "replay" in F and (not parts or any(w in q for w in ("compar", "ml", "improve", "better", "differ", "result", "why"))):
        r = F["replay"]
        m = r["measured"]["mean_wait_s"]
        parts.append(f"In this paired run ({r['scenario']}, seed {r['seed']}), mean waiting time was {m['left']} s for {r['left']} and {m['right']} s for {r['right']} "
                     f"({m['diff']:+} s). Both runs used identical demand.")
    ev = (F.get("pairedEvaluation") or {}).get("ev_rule_effect_on_heuristic")
    if ev and any(w in q for w in ("ambulance", "emergency", "siren")):
        t, r, qu, cw = ev["ev_travel_mean_s"], ev["ev_stopped_red_s"], ev["ev_stopped_queue_s"], ev["mean_wait_civilian_s"]
        parts.append(f"The simulator gives ambulances no priority: they stop at red lights and queue behind other cars. When the controller turns the lights ahead of an ambulance green, "
                     f"its average travel time changes by {t['mean_diff']} s (95% CI {t['ci95'][0]} to {t['ci95'][1]}), time stopped at red lights by {r['mean_diff']} s and time stuck behind queues by "
                     f"{qu['mean_diff']} s. It still waits about {qu['right_mean']} s in queues, because cars in front cannot be cleared. Other vehicles' mean wait changes by "
                     f"{cw['mean_diff']} s (CI {cw['ci95'][0]} to {cw['ci95'][1]}).")
    fb = F.get("forecastBenchmark")
    if fb and fb.get("30") and fb.get("60") and any(w in q for w in ("forecast", "predict", "accura", "reliab", "confiden", "model", "trust", "wrong", "weak")):
        o30, o60 = fb["30"]["overall"], fb["60"]["overall"]
        sev = fb["30"].get("byTrueSeverityBias", {})
        rule = fb["30"].get("alertRuleHeldOut") or {}
        parts.append(f"On held-out simulations the 30 s queue forecast has a mean error of {o30['mae']} vehicles against {o30['persistence_mae']} for assuming the queue stays the same; "
                     f"at 60 s it is {o60['mae']} against {o60['persistence_mae']}. Its 90% intervals contained the measured queue {o30['interval_coverage']} of the time at 30 s. "
                     f"The model tends to under-predict the worst queues (bias {sev.get('spillback')} vehicles when the queue later fills half the approach)"
                     + (f", and its spillback alert rule had precision {rule['precision']} and recall {rule['recall']} on held-out data." if rule else "."))
    pe = F.get("pairedEvaluation")
    if pe and (not parts or any(w in q for w in ("ml", "improve", "better", "overall", "compar", "help"))):
        d = pe.get("ml_vs_heuristic", {}).get("mean_wait_s")
        if d:
            lo, hi = d["ci95"]
            verdict = "lower (better)" if hi < 0 else "higher (worse)" if lo > 0 else "not clearly different"
            parts.append(f"Across {d['pairs']} held-out paired episodes, ML-assisted mean wait differs from the plain heuristic by {d['mean_diff']} s "
                         f"(95% CI {lo} to {hi}): {verdict}.")
    if not parts:
        parts.append("I can explain the measured comparison, the current live congestion, or propose a scenario to run. All numbers come from SUMO runs on a 2x2 grid.")
    return " ".join(parts), req


def _valid_request(r: Any) -> dict[str, Any] | None:
    if not isinstance(r, dict) or r.get("scenario") not in SCENARIOS:
        return None
    ok = {"fixed_time", "queue_heuristic", "ml_forecast"}
    left, right = r.get("left") if r.get("left") in ok else "queue_heuristic", r.get("right") if r.get("right") in ok else "ml_forecast"
    if left == right:
        left, right = "queue_heuristic", "ml_forecast"
    try:
        inten = max(0.5, min(1.4, float(r.get("intensity", 1.0))))
    except (TypeError, ValueError):
        inten = 1.0
    return {"scenario": r["scenario"], "intensity": round(inten, 2), "left": left, "right": right}


def answer(question: str, replay_id: str | None, live: dict[str, Any] | None) -> dict[str, Any]:
    from ..intelligence.gemini import GeminiUnavailable

    F = facts(replay_id, live)
    text, req = deterministic(question, F)
    source, note, rejected = "measurements", None, []
    user = json.dumps({"QUESTION": question, "FACTS": F}, sort_keys=True)
    sent, draft, checked = False, None, None
    g = _guard()
    if g.available:
        try:
            sent = True
            out = g.generate_json(SYSTEM, user, SCHEMA)
            draft = str(out.get("answer", "")).strip()
            ok, bad = grounded(draft, F)
            checked = {"numbersInDraft": len(_NUM.findall(draft)), "ungrounded": bad}
            if draft and ok:
                text, source = draft, "gemini"
                req = _valid_request(out.get("scenario_request")) or (req if out.get("scenario_request") else None)
            else:
                rejected, note = bad, "Gemini answer discarded: it quoted numbers that were not measured" if bad else "empty Gemini answer"
        except GeminiUnavailable as exc:
            note = f"Gemini unavailable ({exc.reason}); answer built from measurements"
        except ImportError:
            note = "Gemini REST client (flowstate.experiments.assistant) is not installed on this branch; answer built from measurements"
        except Exception as exc:  # noqa: BLE001
            note = f"Gemini error ({type(exc).__name__}); answer built from measurements"
    else:
        note = "Gemini not configured (no server-side key) or temporarily closed; answer built from measurements"
    return {"answer": text, "source": source, "note": note, "rejectedNumbers": rejected, "scenarioRequest": req, "facts": F,
            # exactly what Gemini is given (never raw simulation state, never any control API), for the inspector
            "prompt": {"system": SYSTEM, "user": user, "schema": SCHEMA, "chars": len(SYSTEM) + len(user), "approxTokens": (len(SYSTEM) + len(user)) // 4},
            "sentToGemini": sent, "geminiDraft": draft, "grounding": checked}
