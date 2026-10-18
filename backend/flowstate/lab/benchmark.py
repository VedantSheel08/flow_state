"""Forecast audit and benchmark: breakdowns, calibration, warning quality, regime-shift check. Never retrains the model.

Everything is computed from the stored dataset (real SUMO runs, split by seed range) and the stored artifact:

* breakdown of held-out error by scenario, junction, approach, true congestion severity, data-generating controller and horizon;
* an episode-clustered bootstrap CI of the model-vs-persistence MAE gap (rows from one episode are not independent);
* split-conformal style intervals: signed residual quantiles on the *validation* seeds, per bin of predicted occupancy (Mondrian),
  then coverage measured on the *test* seeds - reported even where it falls short;
* warning quality (precision / recall of "will be at least half full") for observed-persistence, model point and model upper bound;
* a regime-shift check: forecast accuracy inside recorded replays where the forecast-assisted controller itself was driving.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .contract import SEVERITY, SEVERITY_EDGES, SPILL_FRAC, approach_table
from .forecast import Forecaster, data_dir, lab_dir
from .sim import FEATURES

Q, CAP = FEATURES.index("queue"), FEATURES.index("capacity")
LEVEL = 0.90
PRED_EDGES = (0.0, 0.10, 0.25, 0.50)  # bins of predicted occupancy for the Mondrian calibration
MIN_BIN = 500  # a calibration bin with fewer validation rows than this falls back to the pooled residuals


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _bin(occ: np.ndarray, edges: tuple[float, ...]) -> np.ndarray:
    return np.searchsorted(np.asarray(edges[1:]), occ, side="right")


def predict_chunked(fc: Forecaster, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Offline batch predictions with scikit-learn itself (identical to the live flat evaluation to 1e-13, and faster for big batches)."""
    cap = X[:, CAP]
    return np.clip(fc.models["p30"].predict(X), 0.0, cap), np.clip(fc.models["p60"].predict(X), 0.0, cap)


# ---------------------------------------------------------------------------------------------------------------- calibration
def f1_at(truth: np.ndarray, pred: np.ndarray) -> float:
    tp = int((truth & pred).sum())
    prec, rec = tp / max(1, int(pred.sum())), tp / max(1, int(truth.sum()))
    return 2 * prec * rec / max(1e-9, prec + rec)


def fit_calibration(fc: Forecaster, d: Any, level: float = LEVEL) -> dict[str, Any]:
    X, y = d["val_X"], d["val_y"]
    P = predict_chunked(fc, X)
    cap = X[:, CAP]
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    out: dict[str, Any] = {"schema": "flowstate.calibration/1", "method": "Mondrian signed-residual quantiles on validation seeds", "level": level,
                           "pred_occupancy_edges": list(PRED_EDGES), "fitted_on": "validation seeds 2000-2003 (never used for the test numbers)",
                           "rows_val": int(len(y)), "horizons": {}}
    for j, h in enumerate((30, 60)):
        res = y[:, j] - P[j]
        bins = _bin(P[j] / cap, PRED_EDGES)
        pooled = (float(np.quantile(res, lo_q)), float(np.quantile(res, hi_q)))
        cells = []
        for k in range(len(PRED_EDGES)):
            r = res[bins == k]
            cells.append({"n": int(len(r)), "lo": float(np.quantile(r, lo_q)) if len(r) >= MIN_BIN else pooled[0],
                          "hi": float(np.quantile(r, hi_q)) if len(r) >= MIN_BIN else pooled[1], "pooled_fallback": len(r) < MIN_BIN})
        out["horizons"][str(h)] = {"pooled": {"lo": pooled[0], "hi": pooled[1]}, "bins": cells}
        # alert threshold on the point forecast (share of capacity), chosen to maximise F1 for "queue >= 50% of capacity at t+h" on validation seeds
        truth = y[:, j] >= SPILL_FRAC * cap
        best = max(((f1_at(truth, P[j] >= tau * cap), tau) for tau in np.arange(0.20, 0.601, 0.025)))
        out["horizons"][str(h)]["alert"] = {"tau": round(float(best[1]), 3), "val_f1": round(float(best[0]), 3), "tuned_on": "validation seeds"}
    return out


def apply_calibration(cal: dict[str, Any], h: int, pred: np.ndarray, cap: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cells = cal["horizons"][str(h)]["bins"]
    k = _bin(pred / cap, tuple(cal["pred_occupancy_edges"]))
    lo = np.array([c["lo"] for c in cells])[k]
    hi = np.array([c["hi"] for c in cells])[k]
    return np.clip(pred + lo, 0.0, cap), np.clip(pred + hi, 0.0, cap)


# ---------------------------------------------------------------------------------------------------------------- scoring
def _cell(y: np.ndarray, p: np.ndarray, base: np.ndarray, lo: np.ndarray | None = None, hi: np.ndarray | None = None) -> dict[str, Any]:
    if len(y) == 0:
        return {"n": 0}
    mae, bmae = float(np.abs(p - y).mean()), float(np.abs(base - y).mean())
    c: dict[str, Any] = {"n": int(len(y)), "mae": round(mae, 3), "persistence_mae": round(bmae, 3), "skill_vs_persistence": round(1 - mae / bmae, 3) if bmae > 1e-9 else None,
                         "bias": round(float((p - y).mean()), 3), "rmse": round(float(np.sqrt(((p - y) ** 2).mean())), 3), "mean_true_queue": round(float(y.mean()), 2)}
    if lo is not None:
        c["interval_coverage"] = round(float(((y >= lo - 1e-9) & (y <= hi + 1e-9)).mean()), 3)
        c["interval_mean_width"] = round(float((hi - lo).mean()), 2)
    return c


def _episode_meta() -> tuple[list[str], list[int], list[str]]:
    man = json.loads((lab_dir() / "dataset_manifest.json").read_text())
    names = list(man["episodes"])
    return [n.split(":")[1] for n in names], [int(n.split(":")[2]) for n in names], [n.split(":", 3)[3] for n in names]


def _spill_scores(yt: np.ndarray, score: np.ndarray, cap: np.ndarray, tau: float = SPILL_FRAC) -> dict[str, Any]:
    truth, pred = yt >= SPILL_FRAC * cap, score >= tau * cap
    tp = int((truth & pred).sum())
    prec, rec = tp / max(1, int(pred.sum())), tp / max(1, int(truth.sum()))
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(2 * prec * rec / max(1e-9, prec + rec), 3), "alerts": int(pred.sum()), "positives": int(truth.sum())}


def _cluster_bootstrap(e: np.ndarray, err_model: np.ndarray, err_base: np.ndarray, n_boot: int = 2000) -> dict[str, Any]:
    ids = np.unique(e)
    idx = {k: i for i, k in enumerate(ids)}
    ei = np.array([idx[k] for k in e])
    cnt = np.bincount(ei, minlength=len(ids)).astype(float)
    sm, sb = np.bincount(ei, weights=err_model, minlength=len(ids)), np.bincount(ei, weights=err_base, minlength=len(ids))
    rng = np.random.default_rng(0)
    pick = rng.integers(0, len(ids), size=(n_boot, len(ids)))
    mm, bb = sm[pick].sum(1) / cnt[pick].sum(1), sb[pick].sum(1) / cnt[pick].sum(1)
    gap = mm - bb
    return {"episodes": int(len(ids)), "mae_model": round(float(sm.sum() / cnt.sum()), 3), "mae_persistence": round(float(sb.sum() / cnt.sum()), 3),
            "mae_model_ci95": [round(float(np.percentile(mm, 2.5)), 3), round(float(np.percentile(mm, 97.5)), 3)],
            "gap_model_minus_persistence_ci95": [round(float(np.percentile(gap, 2.5)), 3), round(float(np.percentile(gap, 97.5)), 3)], "resampled": "episodes"}


def regime_shift_check() -> dict[str, Any]:
    """Accuracy of the live forecasts recorded while the forecast-assisted controller was in control (traffic the model never saw in training)."""
    from .record import replays_dir

    rows = []
    for f in sorted(replays_dir().glob("*.json.gz")):
        doc = json.loads(gzip.decompress(f.read_bytes()))
        for side in ("left", "right"):
            run = doc[side]
            obs = [o for o in run["obs"] if "p30" in o]
            if not obs:
                continue
            by_t = {o["t"]: o for o in run["obs"]}
            for o in obs:
                for h, key in ((30, "p30"), (60, "p60")):
                    later = by_t.get(o["t"] + h)
                    if later:
                        rows.append((doc["id"], side, run["controllerInfo"].get("mlInControl", False), h, np.array(o[key]), np.array(later["q"]), np.array(o["q"])))
    out: dict[str, Any] = {"note": "recorded replays only (a handful of episodes); a direction check, not a benchmark"}
    for ctl_in_loop in (False, True):
        for h in (30, 60):
            sel = [r for r in rows if r[2] == ctl_in_loop and r[3] == h]
            if not sel:
                continue
            y = np.concatenate([r[5] for r in sel])
            c = _cell(y, np.concatenate([r[4] for r in sel]), np.concatenate([r[6] for r in sel]))
            c["episodes"] = len({r[0] + r[1] for r in sel})
            out[f"{'ml_controlled' if ctl_in_loop else 'not_ml_controlled'}_{h}s"] = c
    return out


def run(write: bool = True) -> dict[str, Any]:
    t0 = time.time()
    fc = Forecaster.load_default()
    if fc is None:
        raise RuntimeError("no forecaster artifact")
    d = np.load(data_dir() / "dataset.npz")
    cal = fit_calibration(fc, d)
    scen, seeds, ctrl = _episode_meta()
    table = approach_table()
    X, Y, A, E = d["test_X"], d["test_y"], d["test_a"], d["test_e"]
    cap, q = X[:, CAP], X[:, Q]
    lag30 = X[:, FEATURES.index("q_lag30")]
    P = predict_chunked(fc, X)
    scen_row = np.array(scen)[E]
    ctrl_row = np.array(ctrl)[E]
    junc_row = np.array([a["junction"] for a in table])[A]
    key_row = np.array([a["key"] for a in table])[A]
    out: dict[str, Any] = {"schema": "flowstate.forecast-benchmark/1", "model_sha256": sha256_file(lab_dir() / "forecaster.pkl"), "rows_test": int(len(Y)),
                           "episodes_test": int(len(np.unique(E))), "calibration": {"level": LEVEL, "fitted_on_rows": cal["rows_val"]}, "horizons": {}}
    for j, h in enumerate((30, 60)):
        y, p = Y[:, j], P[j]
        base = q
        lo, hi = apply_calibration(cal, h, p, cap)
        sev = np.array(SEVERITY)[_bin(y / cap, SEVERITY_EDGES)]
        blk: dict[str, Any] = {"overall": _cell(y, p, base, lo, hi)}
        blk["overall"]["episode_bootstrap"] = _cluster_bootstrap(E, np.abs(p - y), np.abs(base - y))
        for name, labels in (("by_scenario", scen_row), ("by_junction", junc_row), ("by_approach", key_row), ("by_true_severity", sev), ("by_data_controller", ctrl_row)):
            order = SEVERITY if name == "by_true_severity" else sorted(set(labels))
            blk[name] = {k: _cell(y[labels == k], p[labels == k], base[labels == k], lo[labels == k], hi[labels == k]) for k in order}
        pb = np.array(["pred_" + n for n in ("lt10pct", "10to25pct", "25to50pct", "ge50pct")])[_bin(p / cap, PRED_EDGES)]
        blk["by_predicted_occupancy"] = {k: _cell(y[pb == k], p[pb == k], base[pb == k], lo[pb == k], hi[pb == k]) for k in sorted(set(pb))}
        blk["by_scenario_x_severity"] = {s: {k: _cell(y[(scen_row == s) & (sev == k)], p[(scen_row == s) & (sev == k)], base[(scen_row == s) & (sev == k)]) for k in SEVERITY}
                                         for s in sorted(set(scen_row))}
        blk["warnings"] = {"definition": f"alert when the score is at least {SPILL_FRAC:.0%} of the approach capacity; truth is the same at t+{h}",
                           "observed_queue_now (persistence)": _spill_scores(y, q, cap), "model_point_forecast": _spill_scores(y, p, cap),
                           f"model_point_forecast_tuned_tau_{cal['horizons'][str(h)]['alert']['tau']}": _spill_scores(y, p, cap, cal["horizons"][str(h)]["alert"]["tau"]),
                           "model_upper_bound_90": _spill_scores(y, hi, cap)}
        blk["warnings_by_scenario"] = {s: {"model_point": _spill_scores(y[scen_row == s], p[scen_row == s], cap[scen_row == s]),
                                           "model_upper_bound_90": _spill_scores(y[scen_row == s], hi[scen_row == s], cap[scen_row == s]),
                                           "persistence": _spill_scores(y[scen_row == s], q[scen_row == s], cap[scen_row == s])} for s in sorted(set(scen_row))}
        if h == 30:  # linear-trend baseline is only defined as the same 30 s window used in the headline evaluation
            blk["overall"]["linear_trend_mae"] = round(float(np.abs(np.maximum(0.0, q + (q - lag30) * h / 30.0) - y).mean()), 3)
        out["horizons"][str(h)] = blk
    out["regime_shift_check"] = regime_shift_check()
    out["wall_s"] = round(time.time() - t0, 1)
    if write:
        lab_dir().mkdir(parents=True, exist_ok=True)
        (lab_dir() / "forecast_breakdown.json").write_text(json.dumps(out, indent=1))
        cal["model_sha256"] = out["model_sha256"]
        (lab_dir() / "calibration.json").write_text(json.dumps(cal, indent=1))
    return out


def summary_lines(b: dict[str, Any]) -> list[str]:
    lines = []
    for h, blk in b["horizons"].items():
        o = blk["overall"]
        lines.append(f"{h}s overall: MAE {o['mae']} vs persistence {o['persistence_mae']} (skill {o['skill_vs_persistence']}), coverage {o['interval_coverage']} (nominal {b['calibration']['level']})")
        for s, c in blk["by_scenario"].items():
            lines.append(f"   {s:10} n={c['n']:6} MAE {c['mae']:5} vs {c['persistence_mae']:5} skill {c['skill_vs_persistence']} bias {c['bias']:+} cover {c['interval_coverage']}")
        for s, c in blk["by_true_severity"].items():
            lines.append(f"   sev {s:10} n={c['n']:6} MAE {c['mae']:5} vs {c['persistence_mae']:5} bias {c['bias']:+} cover {c['interval_coverage']}")
    return lines
