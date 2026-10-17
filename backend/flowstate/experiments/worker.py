"""Run one experiment in its own process (``python -m flowstate.experiments.worker spec.json out.json``).

SUMO's in-process backend allows one simulation per process and the live FlowState simulator must never be disturbed,
so batch and API-triggered experiments always run here. The spec is a JSON file with the fields of ``ExperimentSpec``
plus ``stores``: a list of ``{"sqlite": path}`` / ``{"postgres": "env"}`` targets. The exported result document is written to ``out.json``.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .recorder import ExperimentSpec, run_experiment
from .store import SqliteStore, open_store


def run(spec_path: str, out_path: str) -> int:
    raw = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    stores_cfg = raw.pop("stores", [])
    fields = {k: raw[k] for k in ExperimentSpec.__dataclass_fields__ if k in raw and k != "policy_factory"}
    spec = ExperimentSpec(**fields)
    targets = []
    for cfg in stores_cfg:
        if "sqlite" in cfg:
            st = SqliteStore(cfg["sqlite"])
            st.init_schema()
            targets.append(st)
        elif "postgres" in cfg:
            st = open_store(None if cfg["postgres"] == "env" else cfg["postgres"])
            if st is not None and st.backend == "postgres":
                targets.append(st)
    primary = targets[0] if targets else None
    res = run_experiment(spec, primary)
    errors = [res.persist_error] if res.persist_error else []
    persisted = [res.persisted] if res.persisted else []
    for extra in targets[1:]:  # mirror to the other stores from the in-memory copy
        try:
            from .recorder import _write_all
            from datetime import datetime, timezone

            exp = res.memory.get_experiment(res.id)
            started = datetime.fromisoformat(exp["started_at"]) if isinstance(exp["started_at"], str) else exp["started_at"]
            _write_all(extra, res, started, exp["params_hash"], exp["duration_s"], res.persist_error if res.status == "failed" else None)
            persisted.append(extra.backend)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{extra.backend}: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
    doc = res.export() if res.status == "completed" else {"experiment": {"id": res.id, "status": res.status}, "error": res.persist_error}
    doc["run"] = {"wallSeconds": round(res.wall_s, 1), "persistedTo": persisted, "persistErrors": errors}
    Path(out_path).write_text(json.dumps(doc, default=str), encoding="utf-8")
    return 0 if res.status == "completed" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(run(sys.argv[1], sys.argv[2]))
