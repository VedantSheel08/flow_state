"""HTTP interface of the scenario assistant.

``POST /plan``  text -> validated proposal (stored server-side under a ``planId``)
``POST /run``   {planId} -> runs *that stored, validated plan* (a client cannot submit its own plan or any parameter)
``GET  /runs/{id}`` progress and, when done, the measured comparison and the grounded summary

Gemini is used only if ``GEMINI_API_KEY`` is set on the server; the key never appears in any response.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import assistant as A

router = APIRouter(prefix="/api/experiments/assistant", tags=["assistant"])

_plans: dict[str, tuple[float, A.PlanResult]] = {}
_runs: dict[str, dict[str, Any]] = {}
_world: A.World | None = None
_llm_override: A.LLM | None = None
_lock = threading.Lock()
PLAN_TTL_S = 3600


class PlanRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class RunRequest(BaseModel):
    planId: str


def set_llm(llm: A.LLM | None) -> None:
    """Replace the model client (tests, or an application that supplies its own)."""
    global _llm_override
    _llm_override = llm


def _get_world() -> A.World:
    global _world
    if _world is None:
        _world = A.load_world()
    return _world


@router.get("/status")
def status() -> dict[str, Any]:
    client = A.GeminiClient()
    return {"gemini": {"keyConfigured": client.available, "model": client.model, "verified": False,
                       "note": "the key is read from the server environment; 'verified' stays false until a live call has succeeded in this process"},
            "fallback": "rule-based parser (same validation and execution path)", "limits": {"maxSeeds": A.MAX_SEEDS, "maxDurationMin": A.MAX_DURATION_MIN, "maxRuns": A.MAX_RUNS},
            "controllers": sorted(A.CONTROLLERS), "centralIntersection": _get_world().central}


@router.post("/plan")
def plan(req: PlanRequest) -> dict[str, Any]:
    res = A.plan_request(req.text, _get_world(), _llm_override)
    out = res.to_json()
    if res.validation.status == "ok":
        pid = uuid.uuid4().hex[:12]
        with _lock:
            now = time.time()
            for k in [k for k, (t, _) in _plans.items() if now - t > PLAN_TTL_S]:
                _plans.pop(k, None)
            _plans[pid] = (now, res)
        out["planId"] = pid
    return out


@router.post("/run", status_code=202)
def run(req: RunRequest) -> dict[str, Any]:
    with _lock:
        item = _plans.get(req.planId)
        if item is None:
            raise HTTPException(404, "unknown or expired planId: request a new plan")
        if any(r["status"] == "running" for r in _runs.values()):
            raise HTTPException(429, "an assistant experiment is already running")
        plan_res = item[1]
        rid = uuid.uuid4().hex[:12]
        run_ = {"id": rid, "status": "running", "planId": req.planId, "started": time.time(), "plan": plan_res.validation.plan.to_json()}
        _runs[rid] = run_

    def work() -> None:
        try:
            pl = plan_res.validation.plan
            result = A.run_plan(pl, suite=f"assistant-{rid}")
            llm = _llm_override if _llm_override is not None else (A.GeminiClient() if A.GeminiClient().available else None)
            run_["result"] = {**result, **A.summarize(pl, result["comparisons"], llm)}
            run_["status"] = "completed"
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            run_["status"] = "failed"
            run_["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        run_["finished"] = time.time()

    threading.Thread(target=work, daemon=True).start()
    return {"id": rid, "status": "running"}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    r = _runs.get(run_id)
    if r is None:
        raise HTTPException(404, "unknown run")
    return r
