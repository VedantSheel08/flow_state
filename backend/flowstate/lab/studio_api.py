"""``/api/studio``: read-only endpoints for the FlowState Studio app (results, ambulance stories, what the AI sees). Additive; nothing here controls a signal."""

from __future__ import annotations

import asyncio
import gzip
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..rl import config as C
from . import intel_api
from .forecast import lab_dir
from .perception import OBS_GROUPS, OBS_NAMES, Perception

router = APIRouter(prefix="/api/studio", tags=["studio"])
_status = Perception()
STATUS_NAMES = {0: "moving", 1: "stopped at a red light", 2: "stuck behind queued cars", 3: "moving, green light ahead"}


@router.get("/meta")
def meta() -> dict[str, Any]:
    from .ask import SCHEMA, SYSTEM, guard_status
    from .health import status

    ev = lab_dir() / "forecast_eval.json"
    e = json.loads(ev.read_text()) if ev.exists() else {}
    return {"model": status(), "forecaster": {"features": e.get("features"), "importance30s": e.get("permutation_importance_p30"), "kind": e.get("kind")},
            "ppo": _status.ppo_status(), "camera": _status.camera_status(), "junctions": list(C.CONTROLLED),
            "gemini": {"status": guard_status(), "system": SYSTEM, "schema": SCHEMA, "inControlLoop": False,
                       "sees": "measured facts only (paired evaluation, forecast benchmarks, the selected replay, a live snapshot)",
                       "neverSees": "raw vehicle state, signal-control APIs, anything it could change"},
            "observation": {"names": OBS_NAMES, "groups": [{"name": n, "from": a, "to": b} for n, a, b in OBS_GROUPS]},
            "statusNames": STATUS_NAMES}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    replayId: str | None = Field(None, max_length=200)
    live: bool = False


@router.post("/gemini")
async def gemini(req: AskRequest) -> dict[str, Any]:
    """Like ``/api/lab/ask`` but with the exact prompt, the Gemini draft and the grounding verdict, so the inspector can show what it saw."""
    from .ask import answer

    if req.replayId:
        intel_api._guard_id(req.replayId)
    snap = None
    if req.live:
        s = intel_api.live_provider()
        snap = s and s.get("_snapshot")
    return await asyncio.to_thread(answer, req.question, req.replayId, snap)


@router.get("/replays/{rid}/story")
def story(rid: str) -> dict[str, Any]:
    """Every ambulance in a recorded pair: when it moved, sat at a red light, or was stuck behind queued cars, plus its measured totals."""
    intel_api._guard_id(rid)
    doc = intel_api._replay(rid)
    out: dict[str, Any] = {"replay": rid, "statusNames": STATUS_NAMES, "sides": {}}
    for side in ("left", "right"):
        run = doc[side]
        segs: dict[int, list[list[int]]] = {}
        for fr in run["frames"]:
            for vid, code, _ in fr.get("e", []):
                s = segs.setdefault(vid, [])
                if s and s[-1][2] == code and s[-1][1] == fr["t"] - 1:
                    s[-1][1] = fr["t"]
                else:
                    s.append([fr["t"], fr["t"], code])
        units = {int(u["id"][1:]): u for u in run["metrics"].get("ev_units", [])}
        out["sides"][side] = {"controller": run["controller"], "emergencyGreen": bool(run["controllerInfo"].get("emergencyGreen")),
                              "ambulances": [{"id": vid, "segments": s, "metrics": units.get(vid)} for vid, s in sorted(segs.items(), key=lambda kv: kv[1][0][0])],
                              "totals": {k: run["metrics"].get(k) for k in ("ev_total", "ev_unfinished", "ev_travel_mean_s", "ev_stopped_red_s", "ev_stopped_queue_s")}}
    return out
