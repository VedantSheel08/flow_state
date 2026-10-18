"""Model registry manifest and a lightweight health check (no SUMO, about 50 ms).

``MODEL_MANIFEST.json`` registers the artifact: id, semantic version, sha256, the exact feature list (and its hash), horizons and the
files its evaluation came from. ``check`` reports ``ok`` / ``degraded`` / ``unavailable`` and *why*, covering: missing or unreadable
artifact, hash or feature-schema drift, unsupported manifest version, inference that no longer reproduces the stored golden
predictions, non-finite or out-of-range outputs, flat-vs-sklearn mismatch, latency over budget, and a missing or mismatched calibration.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .forecast import Forecaster, data_dir, lab_dir
from .sim import FEATURES

MANIFEST_SCHEMA = "flowstate.model-manifest/1"
SUPPORTED_MANIFEST = {MANIFEST_SCHEMA}
MODEL_ID = "queue-forecaster"
LATENCY_BUDGET_MS = 50.0  # one 16-approach decision; measured p95 is about 11 ms on the dev machine


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def feature_hash() -> str:
    return hashlib.sha256(json.dumps(list(FEATURES)).encode()).hexdigest()[:16]


def write_manifest(version: str = "1.0.0", write_golden: bool = True) -> dict[str, Any]:
    """Register the artifact currently on disk (does not retrain). Golden vectors are produced by the artifact itself."""
    art = lab_dir() / "forecaster.pkl"
    ev = json.loads((lab_dir() / "forecast_eval.json").read_text())
    dm = lab_dir() / "dataset_manifest.json"
    mf = {"schema": MANIFEST_SCHEMA, "model_id": MODEL_ID, "version": version, "artifact": art.name, "artifact_sha256": _sha(art), "features": list(FEATURES),
          "feature_hash": feature_hash(), "horizons_s": [30, 60], "sample_s": 5, "kind": ev["kind"], "trained_at": ev["trained_at"],
          "dataset_manifest_sha256": _sha(dm) if dm.exists() else None, "evaluation": "forecast_eval.json", "split": ev["split"],
          "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (lab_dir() / "MODEL_MANIFEST.json").write_text(json.dumps(mf, indent=1))
    if write_golden:
        Forecaster._default = False
        fc = Forecaster.load_default()
        X = np.load(data_dir() / "dataset.npz")["test_X"]
        rows = X[np.linspace(0, len(X) - 1, 32).astype(int)]
        p30, p60 = fc.predict(rows)
        (lab_dir() / "golden_inference.json").write_text(json.dumps({"model_sha256": mf["artifact_sha256"], "X": rows.tolist(), "p30": p30.tolist(), "p60": p60.tolist()}))
    return mf


def status() -> dict[str, Any]:
    """Cheap, non-inferring descriptor used in every API response (id, version, hash, calibration)."""
    fc = Forecaster.load_default()
    mf_path = lab_dir() / "MODEL_MANIFEST.json"
    mf = json.loads(mf_path.read_text()) if mf_path.exists() else {}
    return {"modelId": mf.get("model_id", MODEL_ID), "version": mf.get("version"), "sha256": getattr(fc, "sha256", "") if fc else "", "available": fc is not None,
            "calibrated": bool(fc and fc.calibration), "intervalLevel": (fc.calibration or {}).get("level") if fc else None,
            "alert": alert_rules(fc), "skillByControlRegime": skill_by_regime(fc), "health": _last.get("status", "unchecked"), "trainedAt": mf.get("trained_at"), "features": len(FEATURES)}


def skill_by_regime(fc: Forecaster | None) -> dict[str, Any]:
    """Held-out skill vs persistence (1 - MAE/MAE_persistence) per horizon, split by the kind of signal control that generated the test traffic."""
    bf = lab_dir() / "forecast_breakdown.json"
    if fc is None or not bf.exists():
        return {}
    bd = json.loads(bf.read_text())
    if bd.get("model_sha256") != fc.sha256:
        return {}
    return {h: {k: v["skill_vs_persistence"] for k, v in blk["by_data_controller"].items()} for h, blk in bd["horizons"].items()}


def alert_rules(fc: Forecaster | None) -> dict[str, Any]:
    """Per horizon: the alert threshold (share of capacity) and its precision/recall measured on the held-out test seeds."""
    if fc is None or not fc.calibration:
        return {}
    bf = lab_dir() / "forecast_breakdown.json"
    bd = json.loads(bf.read_text()) if bf.exists() and json.loads(bf.read_text()).get("model_sha256") == fc.sha256 else None
    out = {}
    for h, c in fc.calibration["horizons"].items():
        r: dict[str, Any] = {"tau": c["alert"]["tau"], "tunedOn": c["alert"]["tuned_on"]}
        if bd:
            w = next((v for k, v in bd["horizons"][h]["warnings"].items() if k.startswith("model_point_forecast_tuned")), None)
            if w:
                r |= {"heldOutPrecision": w["precision"], "heldOutRecall": w["recall"]}
        out[h] = r
    return out


_last: dict[str, Any] = {}


def check(deep: bool = False) -> dict[str, Any]:
    """Run every check; never raises. ``deep`` also compares flat evaluation with scikit-learn on 2,000 stored rows."""
    t0 = time.perf_counter()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", severity: str = "unavailable") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, **({} if ok else {"severity": severity})})
        return bool(ok)

    art, mf_path = lab_dir() / "forecaster.pkl", lab_dir() / "MODEL_MANIFEST.json"
    mf: dict[str, Any] = {}
    if add("artifact_present", art.exists(), str(art.name)):
        if add("manifest_present", mf_path.exists(), "MODEL_MANIFEST.json", "degraded"):
            try:
                mf = json.loads(mf_path.read_text())
                add("manifest_version_supported", mf.get("schema") in SUPPORTED_MANIFEST, f"schema {mf.get('schema')}")
                add("artifact_hash_matches_manifest", _sha(art) == mf.get("artifact_sha256"), "sha256")
                add("feature_schema_matches_code", mf.get("features") == list(FEATURES), f"manifest {mf.get('feature_hash')} vs code {feature_hash()}")
            except Exception as exc:  # noqa: BLE001
                add("manifest_readable", False, f"{type(exc).__name__}: {exc}")
    cached = Forecaster._default if isinstance(Forecaster._default, Forecaster) else None
    if cached is None or not art.exists() or cached.sha256 != _sha(art):  # reuse the loaded model only while the file on disk is still the same bytes
        Forecaster._default = False
    fc = Forecaster.load_default()
    if add("artifact_loads", fc is not None, Forecaster.load_error or "ok") and fc is not None:
        gold_p = lab_dir() / "golden_inference.json"
        try:
            rng = np.random.default_rng(0)
            probe = np.tile(np.array([[3, 8, 5, 12, 0.4, 1, 0, 10, 40, 6, 2, 3, 2, 2, 1, 7, 47.8, 0, 2]], dtype=np.float32), (16, 1))
            probe[:, 0] += rng.integers(0, 5, 16)
            lat = []
            for _ in range(5):  # best of five: a shared, busy machine inflates single calls; a broken model is slow every time
                t1 = time.perf_counter()
                p30, p60 = fc.predict(probe)
                lat.append((time.perf_counter() - t1) * 1000)
            ms = min(lat)
            add("inference_runs_finite_in_range", bool(np.isfinite(p30).all() and np.isfinite(p60).all() and (p30 >= 0).all() and (p30 <= probe[:, FEATURES.index("capacity")]).all()), "16 rows")
            add("latency_within_budget", ms <= LATENCY_BUDGET_MS, f"{ms:.1f} ms best of 5 (budget {LATENCY_BUDGET_MS:.0f})", "degraded")
            if add("golden_vectors_present", gold_p.exists(), "golden_inference.json", "degraded"):
                g = json.loads(gold_p.read_text())
                if add("golden_bound_to_artifact", g["model_sha256"] == fc.sha256, "sha256", "degraded"):
                    q30, q60 = fc.predict(np.array(g["X"], dtype=np.float32))
                    d = max(float(np.abs(q30 - np.array(g["p30"])).max()), float(np.abs(q60 - np.array(g["p60"])).max()))
                    add("golden_inference_reproduced", d < 1e-4, f"max |diff| {d:.2e}")
            if deep:
                X = np.load(data_dir() / "dataset.npz")["test_X"][:2000]
                add("flat_equals_sklearn", fc.check(X) < 1e-6, "2,000 stored rows")
            add("calibration_bound_to_artifact", fc.calibration is not None, "calibration.json" if fc.calibration else "no calibration for this artifact: forecasts have no intervals", "degraded")
        except Exception as exc:  # noqa: BLE001
            add("inference_runs", False, f"{type(exc).__name__}: {exc}")
    failed = [c for c in checks if not c["ok"]]
    st = "unavailable" if any(c["severity"] == "unavailable" for c in failed) else "degraded" if failed else "ok"
    out = {"schema": "flowstate.model-health/1", "status": st, "usable": st != "unavailable", "model": {"id": mf.get("model_id"), "version": mf.get("version"), "sha256": (mf.get("artifact_sha256") or "")[:16]},
           "checks": checks, "failed": [c["name"] for c in failed], "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _last.update(out)
    return out
