"""Additive ``/api/intelligence`` router: Gemini interpretation layer (status, grounded explanations, natural-language planning).

Gemini is used for *interpretation only*: a request becomes a validated proposal (the assistant's validator decides what may run) or a
stored comparison becomes a short narrative whose numbers must match measurements. Nothing here touches signals. The shared
:class:`~flowstate.intelligence.gemini.GuardedGemini` supplies the timeouts, cache, rate limit, concurrency cap and breakers, and every
failure degrades to the assistant's offline rules or to a deterministic summary. No endpoint returns key material.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..rl.controllers import artifacts_dir, default_controller
from .explain import explain, facts_from_evaluation
from .gemini import GuardedGemini
from .secrets import install_log_redaction

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])
_guard: GuardedGemini | None = None
_lock = threading.Lock()


def get_guard() -> GuardedGemini:
    global _guard
    with _lock:
        if _guard is None:
            install_log_redaction()
            _guard = GuardedGemini()
        return _guard


class ExplainRequest(BaseModel):
    evaluation: Literal["standard", "stress"] = "standard"  # stored held-out evaluations only: no client-supplied paths or numbers


class PlanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("/status")
def status() -> dict[str, Any]:
    return {"layer": "interpretation only: Gemini is never in the signal-control loop", "defaultController": default_controller(),
            "gemini": get_guard().status()}


@router.post("/explain")
def explain_evaluation(req: ExplainRequest) -> dict[str, Any]:
    f = Path(artifacts_dir()) / f"evaluation_{req.evaluation}.json"
    if not f.exists():
        raise HTTPException(404, f"no stored {req.evaluation} evaluation")
    return explain(facts_from_evaluation(json.loads(f.read_text())), get_guard())


@router.post("/plan")
def plan(req: PlanRequest) -> dict[str, Any]:
    try:
        from flowstate.experiments import assistant as A
    except ImportError as exc:
        raise HTTPException(503, "the experiment assistant is not installed in this deployment") from exc
    return A.plan_request(req.text, llm=get_guard()).to_json()
