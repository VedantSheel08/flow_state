"""Controlled controller comparisons for the API: request validation, background jobs, result assembly.

Experiments run in a child process (``python -m flowstate.rl.cli compare-json``) because libsumo allows one
simulation per process and the API server may already be hosting the main FlowState engine. The child writes a
JSON result file and prints ``PROGRESS k n`` lines that the job manager turns into progress.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from . import config as C
from .controllers import artifacts_dir, available, default_controller
from .demand import HELD_OUT_SCENARIOS, SCENARIOS, STRESS_INTENSITY, TRAIN_SCENARIOS

MAX_EPISODES = 60


class CompareRequest(BaseModel):
    controllers: list[str] = Field(default_factory=lambda: ["fixed_time", "queue_heuristic", "rl_ppo"], min_length=2, max_length=5)
    scenarios: list[str] = Field(default_factory=lambda: ["balanced"], min_length=1, max_length=5)
    seeds: list[int] = Field(default_factory=lambda: [3000, 3001, 3002], min_length=1, max_length=10)
    intensity: float = Field(1.0, ge=0.3, le=1.6, description="demand multiplier; 1.0 = standard busy peak, 1.18 = over-saturated stress")
    horizon_s: float = Field(900.0, ge=300.0, le=1800.0)
    baseline: str = "fixed_time"
    include_series: bool = True

    @field_validator("controllers")
    @classmethod
    def _known_controllers(cls, v: list[str]) -> list[str]:
        known = available()
        bad = [c for c in v if c not in known]
        if bad:
            raise ValueError(f"unknown controllers {bad}; available: {sorted(known)}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate controller")
        return v

    @field_validator("scenarios")
    @classmethod
    def _known_scenarios(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in SCENARIOS]
        if bad:
            raise ValueError(f"unknown scenarios {bad}; available: {sorted(SCENARIOS)}")
        return v

    def check(self) -> None:
        if self.baseline not in self.controllers:
            raise ValueError("baseline must be one of the compared controllers")
        n = len(self.controllers) * len(self.scenarios) * len(self.seeds)
        if n > MAX_EPISODES:
            raise ValueError(f"{n} episodes requested; the limit is {MAX_EPISODES}")


def catalog() -> dict[str, Any]:
    return {
        "network": {"id": "rl_grid", "junctions": list(C.CONTROLLED), "description": "2 x 2 signalised grid, two-lane streets, 200 m blocks, 8 entry/exit stubs"},
        "scenarios": [{"id": s.id, "title": s.title, "description": s.description, "held_out": s.id in HELD_OUT_SCENARIOS,
                       "trained_on": s.id in TRAIN_SCENARIOS} for s in SCENARIOS.values()],
        "intensity": {"standard": 1.0, "stress": STRESS_INTENSITY, "range": [0.3, 1.6]},
        "controllers": [{"id": k, **v} for k, v in available().items()],
        "default_controller": default_controller(),  # what a UI should preselect: rl_ppo only if it passed the gate, else the verified heuristic
        "safety": C.describe(),
        "decision": {"interval_s": C.DECISION_S, "actions": ["hold (extend green)", "request next stage"],
                     "note": "every controller acts through the same safety layer: minimum green (includes pedestrian walk + clearance), full yellow and all-red, maximum green"},
    }


def model_card() -> dict[str, Any]:
    """What the trained policy is and how it was evaluated, straight from the stored artifacts (no claims are generated)."""
    d = artifacts_dir()
    card: dict[str, Any] = {"checkpoint": (d / "ppo_policy.zip").exists()}
    for key, name in (("training", "training_run.json"), ("validation", "validation.json"), ("evaluation", "evaluation_summary.json")):
        f = d / name
        card[key] = json.loads(f.read_text()) if f.exists() else None
    return card


class Job(dict):
    pass


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, req: CompareRequest) -> dict[str, Any]:
        req.check()
        jid = uuid.uuid4().hex[:10]
        total = len(req.controllers) * len(req.scenarios) * len(req.seeds)
        job = Job(id=jid, status="queued", progress={"done": 0, "total": total}, request=req.model_dump(), created=time.time(), result=None, error=None)
        with self._lock:
            self.jobs[jid] = job
        threading.Thread(target=self._run, args=(job, req), daemon=True).start()
        return self.view(jid)

    def view(self, jid: str) -> dict[str, Any] | None:
        job = self.jobs.get(jid)
        if job is None:
            return None
        return {k: v for k, v in job.items()}

    def _run(self, job: Job, req: CompareRequest) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="flowstate-rl-"))
        (tmp / "request.json").write_text(req.model_dump_json())
        cmd = [os.environ.get("FLOWSTATE_RL_PYTHON", sys.executable), "-u", "-m", "flowstate.rl.cli", "compare-json", "--request", str(tmp / "request.json"),
               "--out", str(tmp / "result.json")]
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2]) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        job["status"] = "running"
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            tail: list[str] = []
            for line in proc.stdout or []:
                if line.startswith("PROGRESS "):
                    k, n = line.split()[1:3]
                    job["progress"] = {"done": int(k), "total": int(n)}
                else:
                    tail = (tail + [line.rstrip()])[-12:]
            rc = proc.wait()
            if rc != 0 or not (tmp / "result.json").exists():
                job.update(status="failed", error="\n".join(tail) or f"worker exited with {rc}")
                return
            job.update(status="done", result=json.loads((tmp / "result.json").read_text()), finished=time.time())
        except Exception as e:  # noqa: BLE001
            job.update(status="failed", error=str(e))


def run_comparison(req: CompareRequest, progress=None) -> dict[str, Any]:
    """Run the comparison in this process (the worker side) and return the API result document."""
    from .controllers import make
    from .evaluate import evaluate, summarise

    req.check()
    fallbacks = []
    for cid in req.controllers:
        _, info = make(cid)
        if info.get("fallback"):
            fallbacks.append(info)
    t0 = time.time()
    rows = evaluate(req.controllers, req.scenarios, req.seeds, horizon_s=req.horizon_s, intensity=req.intensity, workers=min(6, os.cpu_count() or 2),
                    progress=progress)
    summary = summarise(rows, baseline=req.baseline)
    titles = {cid: v["title"] for cid, v in available().items()}
    first_seed = {sc: min(req.seeds) for sc in req.scenarios}
    episodes = []
    for r in rows:
        e = {k: v for k, v in r.items() if k != "series"}
        e["controller_used"] = "queue_heuristic" if r.get("fallback") and r["requested_controller"] == "rl_ppo" else r["requested_controller"]
        if req.include_series and r["seed"] == first_seed[r["scenario"]]:
            e["series"] = {"columns": ["t_s", "queued_vehicles", "mean_wait_s", "vehicles_in_network"], "rows": r["series"]}
        episodes.append(e)
    demand = {f"{r['scenario']}/{r['seed']}": r["demand_sha"] for r in rows}
    same = all(len({x["demand_sha"] for x in rows if (x["scenario"], x["seed"]) == (r["scenario"], r["seed"])}) == 1 for r in rows)
    return {"request": req.model_dump(), "titles": titles, "fallbacks": fallbacks, "summary": summary, "episodes": episodes,
            "demand": {"identical_across_controllers": same, "hashes": demand},
            "wall_seconds": round(time.time() - t0, 1),
            "caveat": "Results come from SUMO runs of the 2 x 2 RL grid, not from the FlowState district network."}


manager = JobManager()
