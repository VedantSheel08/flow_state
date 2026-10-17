"""FastAPI router for the RL controller integration (mounted under ``/api/rl``).

Purely additive: no existing route or payload changes. See ``docs/RL_INTEGRATION_CONTRACT.md``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from .service import CompareRequest, catalog, manager, model_card

router = APIRouter(prefix="/api/rl", tags=["rl"])


@router.get("/info")
def info() -> dict[str, Any]:
    """Network, scenarios, controllers (with availability), safety limits and decision interval."""
    return catalog()


@router.get("/controllers")
def controllers() -> list[dict[str, Any]]:
    return catalog()["controllers"]


@router.get("/model")
def model() -> dict[str, Any]:
    """Model card: training record, validation gate and the stored evaluation summary of the trained policy."""
    return model_card()


@router.post("/compare", status_code=202)
def compare(req: CompareRequest) -> dict[str, Any]:
    try:
        return manager.submit(req)
    except (ValueError, ValidationError) as e:
        raise HTTPException(422, str(e)) from e


@router.get("/compare/{job_id}")
def compare_job(job_id: str) -> dict[str, Any]:
    job = manager.view(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job
