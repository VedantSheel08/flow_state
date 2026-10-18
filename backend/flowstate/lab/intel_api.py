"""``/api/intelligence/v1``: the stable, versioned intelligence API (router only; mount it in the lab app or in the main FlowState app).

Every report separates ``observed`` (SUMO measurements), ``predicted`` (model forecasts, versioned, optionally with calibrated intervals) and
``interventions`` (controller actions with evidence); see :mod:`flowstate.lab.contract`. Endpoints are read-only except ``/explain``, which
only produces text. Nothing here can influence signal control.
"""

from __future__ import annotations

import functools
import gzip
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..rl import config as C
from . import contract, export, health
from .forecast import lab_dir

router = APIRouter(prefix="/api/intelligence/v1", tags=["intelligence-v1"])
live_provider: Callable[[], dict[str, Any] | None] = lambda: None  # the host app registers its live-session accessor here

KNOWN_VERSIONS = ["v1"]


def _guard_id(rid: str) -> None:
    if "/" in rid or "\\" in rid or ".." in rid:
        raise HTTPException(400, "bad id")


@functools.lru_cache(maxsize=3)
def _replay(rid: str) -> dict[str, Any]:
    from .record import replays_dir

    f = replays_dir() / f"{rid}.json.gz"
    if not f.exists():
        raise HTTPException(404, "no such replay")
    return json.loads(gzip.decompress(f.read_bytes()))


def gemini_block() -> dict[str, Any]:
    from .ask import guard_status

    g = guard_status()
    return {"configured": bool(g.get("configured")), "available": bool(g.get("available")), "clientInstalled": bool(g.get("restClientInstalled")),
            "mode": "gemini" if g.get("available") and g.get("restClientInstalled") else "offline_fallback",
            "dependency": "flowstate.experiments.assistant.GeminiClient (branch task/integrations) plus GEMINI_API_KEY on the server",
            "inControlLoop": False, "guard": {k: g.get(k) for k in ("limits", "cache", "latencySeconds", "counters", "project", "lastError") if k in g}}


@router.get("/")
def index() -> dict[str, Any]:
    return {"schema": contract.SCHEMA, "versions": KNOWN_VERSIONS, "endpoints": [
        "GET /model", "GET /health", "GET /approaches", "GET /live", "GET /replays/{id}/report?t=&side=", "GET /replays/{id}/series?side=",
        "GET /benchmarks", "POST /explain"], "separation": ["observed", "predicted", "interventions"]}


@router.get("/model")
def model() -> dict[str, Any]:
    ev = lab_dir() / "forecast_eval.json"
    e = json.loads(ev.read_text()) if ev.exists() else None
    return {"schema": contract.SCHEMA, "model": health.status(), "health": health.check(), "horizonsS": list(contract.HORIZONS_S),
            "thresholds": {"bottleneckFractionOfCapacity": contract.SPILL_FRAC, "severityEdges": dict(zip(contract.SEVERITY, contract.SEVERITY_EDGES))},
            "headline": e and {"split": e["split"], "rows": e["rows"], "testMae": {h: {"model": e["test"][h]["model"]["mae"], "persistence": e["test"][h]["persistence"]["mae"]} for h in ("p30", "p60")},
                               "inferenceMsPerDecision": e["inference_ms_per_decision"]},
            "gemini": gemini_block()}


@router.get("/health")
def get_health(deep: bool = False) -> dict[str, Any]:
    return health.check(deep=deep)


@router.get("/approaches")
def approaches() -> dict[str, Any]:
    return {"schema": contract.SCHEMA, "approaches": list(contract.approach_table())}


@router.get("/live")
def live() -> dict[str, Any]:
    snap = live_provider()
    if not snap:
        raise HTTPException(404, "no live session has produced an observation yet")
    return snap


def report_at(doc: dict[str, Any], t: float, side: str) -> dict[str, Any]:
    run = doc[side]
    obs = [o for o in run["obs"] if o["t"] <= t]
    if not obs:
        raise HTTPException(404, "no observation at or before t")
    o = obs[-1]
    fc = {k: o[k] for k in ("p30", "p60", "lo30", "hi30", "lo60", "hi60") if k in o}
    decisions = [e for e in run.get("mlEvidence") or [] if o["t"] - 30 < e["t"] <= o["t"]]
    if not decisions and run.get("mlLog"):  # replay recorded before evidence logging: the decision is known, its evidence is not
        decisions = [{"t": ts, "junction": C.CONTROLLED[j], "action": "switched_earlier" if d > 0 else "held_longer", "evidence": None}
                     for ts, j, d in run["mlLog"] if o["t"] - 30 < ts <= o["t"]]
    model = contract.model_block(health.status()) | {"recordedWith": doc.get("forecaster")}
    return contract.build_report(t=o["t"], queues=o["q"], forecast=fc if "p30" in fc else None, model=model if "p30" in fc else None, source="replay",
                                 context={"replay": doc["id"], "side": side, "scenario": doc["scenario"], "seed": doc["seed"], "intensity": doc["intensity"], "demandSha": doc["demandSha"]},
                                 controller={"id": run["controller"], "mlInControl": bool(run["controllerInfo"].get("mlInControl"))}, decisions=decisions)


@router.get("/replays/{rid}/report")
def replay_report(rid: str, t: float = Query(0, ge=0), side: str = Query("right", pattern="^(left|right)$")) -> dict[str, Any]:
    _guard_id(rid)
    return report_at(_replay(rid), t, side)


@router.get("/replays/{rid}/series")
def replay_series(rid: str, side: str = Query("right", pattern="^(left|right)$")) -> dict[str, Any]:
    """Compact time series for charts: observed network queue, the forecast aligned to the time it predicts, and controller interventions."""
    _guard_id(rid)
    doc = _replay(rid)
    run = doc[side]
    cap = [a["capacity"] for a in contract.approach_table()]
    obs = run["obs"]
    out: dict[str, Any] = {"schema": contract.SCHEMA, "replay": rid, "side": side, "controller": run["controller"], "sampleS": 5,
                           "observed": {"kind": "observed", "t": [o["t"] for o in obs], "networkQueued": [round(sum(o["q"]), 1) for o in obs],
                                        "approachesAtOrOverHalf": [sum(1 for q, c in zip(o["q"], cap) if q >= contract.SPILL_FRAC * c) for o in obs]},
                           "interventions": {"kind": "controller_intervention", "decisions": [{"t": e["t"], "junction": e["junction"], "action": e["action"]} for e in run.get("mlEvidence") or []]
                                             or [{"t": ts, "junction": C.CONTROLLED[j], "action": "switched_earlier" if d > 0 else "held_longer"} for ts, j, d in run.get("mlLog") or []]}}
    for h in contract.HORIZONS_S:
        pr = [o for o in obs if f"p{h}" in o]
        if pr:
            s: dict[str, Any] = {"kind": "prediction", "horizonS": h, "madeAtT": [o["t"] for o in pr], "targetT": [o["t"] + h for o in pr],
                                 "networkQueued": [round(sum(o[f"p{h}"]), 1) for o in pr]}
            if f"lo{h}" in pr[0]:  # sum of per-approach bounds is not a calibrated network bound: only per-approach intervals are offered
                s["note"] = "per-approach intervals are available from /report; network totals carry no interval"
            out[f"predicted{h}"] = s
    return out


@router.get("/benchmarks")
def benchmarks(section: str | None = None) -> dict[str, Any]:
    b = export.load()
    if section:
        if section not in b:
            raise HTTPException(404, f"no section '{section}' (have {sorted(b)})")
        return {"schema": b["schema"], section: b[section]}
    return b


class ExplainRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    replayId: str | None = Field(None, max_length=200)
    live: bool = False


@router.post("/explain")
def explain(req: ExplainRequest) -> dict[str, Any]:
    """Text explanation of measured intelligence. Gemini only rephrases measured facts (numbers verified); otherwise a deterministic answer."""
    from .ask import answer

    if req.replayId:
        _guard_id(req.replayId)
    snap = None
    if req.live:
        s = live_provider()
        snap = s and s.get("_snapshot")
    r = answer(req.question, req.replayId, snap)
    return {"schema": contract.SCHEMA, "answer": r["answer"], "source": r["source"], "note": r["note"], "rejectedNumbers": r["rejectedNumbers"],
            "scenarioRequest": r["scenarioRequest"], "labels": {"measured": "values from SUMO runs and held-out evaluation", "model": "forecast values are model output, not measurements"}}
