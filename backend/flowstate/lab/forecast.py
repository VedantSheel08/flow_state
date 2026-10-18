"""Queue forecasting: dataset generation from real SUMO runs, a lightweight CPU model, baselines and held-out evaluation.

Target: the number of halting vehicles on each of the 16 signalised approaches 30 s and 60 s after the observation.
Features: :data:`flowstate.lab.sim.FEATURES` (detector-like counts, signal state, upstream platoons, 10/20/30 s history).

Leakage control: every row carries its episode; train / validation / test are *disjoint seed ranges* (and the rush-hour ramp
scenario appears only in test), so no episode contributes to more than one split. Baselines are scored on exactly the same test
rows as the model.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..rl import config as C
from .sim import FEATURES

# 16-row batches: OpenMP thread start-up costs far more than the trees themselves, and one thread bounds CPU per SUMO worker.
# Set before scikit-learn is imported (it is imported lazily below).
os.environ.setdefault("OMP_NUM_THREADS", "1")

H30, H60 = 6, 12  # horizons in decision samples (5 s each)
SPILL_FRAC = 0.5  # an approach queued over half its length is a bottleneck / spillback risk

SPLITS: dict[str, dict[str, Any]] = {
    "train": {"scenarios": ["balanced", "ns_heavy", "ew_heavy", "pulse"], "seeds": list(range(1000, 1012)),
              "controllers": ["fixed_time", "queue_heuristic", "queue_heuristic@3,14,0.25"]},
    "val": {"scenarios": ["balanced", "ns_heavy", "ew_heavy", "pulse"], "seeds": list(range(2000, 2004)),
            "controllers": ["fixed_time", "queue_heuristic"]},
    "test": {"scenarios": ["balanced", "ns_heavy", "ew_heavy", "pulse", "rush_ramp"], "seeds": list(range(3000, 3008)),
             "controllers": ["fixed_time", "queue_heuristic"]},
}


def root() -> Path:
    return Path(__file__).resolve().parents[3]


def lab_dir() -> Path:
    return root() / "artifacts" / "lab"


def data_dir() -> Path:
    return root() / "data" / "lab"


def intensity_for(seed: int) -> float:
    """Half of the episodes run at the over-saturated stress level so the data contains real spillback."""
    return 1.18 if seed % 2 else 1.0


# ---------------------------------------------------------------------------------------------------------------- dataset
def _episode(job: tuple[str, str, int]) -> dict[str, Any]:
    from .control import make_controller
    from .sim import LabSim

    cid, scenario, seed = job
    sim = LabSim(f"ds{mp.current_process().pid}")
    try:
        sim.reset(scenario, seed, 900.0, 300.0, intensity_for(seed))
        ctl, _ = make_controller(cid, sim, shadow_forecast=False)
        ctl.reset(sim)
        xs, ts = [], []
        while not sim.done:
            xs.append(sim.observe().copy())
            ts.append(sim.t)
            for jid, a in zip(C.CONTROLLED, ctl.decide(sim)):
                if a:
                    sim.request(jid)
            sim.advance(C.DECISION_S)
        return {"X": np.stack(xs), "t": np.asarray(ts), "scenario": scenario, "seed": seed, "controller": cid,
                "metrics": {k: sim.metrics()[k] for k in ("mean_wait_s", "teleports", "vehicles_scheduled")}}
    finally:
        sim.close()


def rows_from(ep: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(features, targets[30, 60], approach index) for every sample that has both future targets inside the episode."""
    X = ep["X"]  # (T, 16, F)
    n = X.shape[0] - H60
    if n <= 0:
        return np.zeros((0, X.shape[2])), np.zeros((0, 2)), np.zeros(0, dtype=int)
    feats = X[:n].reshape(-1, X.shape[2])
    y = np.stack([X[H30:H30 + n, :, 0].reshape(-1), X[H60:H60 + n, :, 0].reshape(-1)], axis=1)
    appr = np.tile(np.arange(X.shape[1]), n)
    return feats, y, appr


def generate(workers: int = 4, out: Path | None = None) -> dict[str, Any]:
    out = out or data_dir() / "dataset.npz"
    jobs = [(split, (c, sc, s)) for split, spec in SPLITS.items() for sc in spec["scenarios"] for s in spec["seeds"] for c in spec["controllers"]]
    t0 = time.time()
    ctx = mp.get_context("spawn")
    with ctx.Pool(workers) as pool:
        eps = pool.map(_episode, [j for _, j in jobs])
    arrays: dict[str, list[np.ndarray]] = {}
    manifest: dict[str, Any] = {"episodes": {}, "features": list(FEATURES), "horizons_s": [30, 60], "sample_s": C.DECISION_S}
    for (split, job), ep in zip(jobs, eps):
        f, y, a = rows_from(ep)
        e_id = np.full(len(y), len(manifest["episodes"]))
        for k, v in (("X", f), ("y", y), ("a", a), ("e", e_id)):
            arrays.setdefault(f"{split}_{k}", []).append(v)
        manifest["episodes"][f"{split}:{job[1]}:{job[2]}:{job[0]}"] = {"rows": int(len(y)), **ep["metrics"]}
    data = {k: np.concatenate(v) for k, v in arrays.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **data)
    manifest["rows"] = {s: int(len(data[f"{s}_y"])) for s in SPLITS}
    manifest["wall_s"] = round(time.time() - t0, 1)
    manifest["splits"] = SPLITS
    lab_dir().mkdir(parents=True, exist_ok=True)
    (lab_dir() / "dataset_manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


# ---------------------------------------------------------------------------------------------------------------- model
class FlatTrees:
    """A fitted HistGradientBoostingRegressor (squared error, numeric features, no missing values) as flat arrays.

    scikit-learn walks its trees one Python call per tree, which costs ~15 us x 300 trees for a 16-row batch. Here every tree is
    descended at once with NumPy indexing (depth iterations), which is exact: ``Forecaster.check`` compares against scikit-learn.
    """

    def __init__(self, model: Any):
        trees = [p[0].nodes for p in model._predictors]
        sizes = np.array([len(t) for t in trees])
        off = np.concatenate([[0], np.cumsum(sizes)[:-1]])
        n = np.concatenate(trees)
        shift = np.repeat(off, sizes)
        leaf = n["is_leaf"].astype(bool)
        idx = np.arange(len(n), dtype=np.int64)
        # leaves point to themselves, so every row can take the same number of steps without branching
        self.feat = np.where(leaf, 0, n["feature_idx"]).astype(np.int64)
        self.thr = np.where(leaf, np.inf, n["num_threshold"]).astype(np.float64)
        self.left = np.where(leaf, idx, n["left"].astype(np.int64) + shift)
        self.right = np.where(leaf, idx, n["right"].astype(np.int64) + shift)
        self.value = n["value"].astype(np.float64)
        self.roots = off.astype(np.int64)
        self.depth = int(n["depth"].max())
        self.base = float(np.asarray(model._baseline_prediction).ravel()[0])

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.ascontiguousarray(X, dtype=np.float64)
        flat = X.ravel()
        base_idx = (np.arange(len(X)) * X.shape[1])[:, None]
        node = np.broadcast_to(self.roots, (len(X), len(self.roots))).copy()
        for _ in range(self.depth):
            node = np.where(flat[base_idx + self.feat[node]] <= self.thr[node], self.left[node], self.right[node])
        return self.base + self.value[node].sum(axis=1)


class Forecaster:
    """Two gradient-boosted trees (30 s, 60 s). Predictions are clipped to [0, approach capacity]."""

    _default: "Forecaster | None | bool" = False

    def __init__(self, models: dict[str, Any], meta: dict[str, Any]):
        self.models, self.meta = models, meta
        self.fast = {k: FlatTrees(m) for k, m in models.items()}

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        cap = X[:, FEATURES.index("capacity")]
        p30 = np.clip(self.fast["p30"].predict(X), 0.0, cap)
        p60 = np.clip(self.fast["p60"].predict(X), 0.0, cap)
        return p30, p60

    def check(self, X: np.ndarray) -> float:
        """Largest absolute difference between the flat evaluation and scikit-learn's own predict."""
        return max(float(np.abs(self.fast[k].predict(X) - m.predict(X)).max()) for k, m in self.models.items())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps({"models": self.models, "meta": self.meta}))  # FlatTrees are rebuilt on load

    @classmethod
    def load(cls, path: Path) -> "Forecaster":
        d = pickle.loads(path.read_bytes())  # our own artifact, written by train(); never loaded from user input
        return cls(d["models"], d["meta"])

    @classmethod
    def load_default(cls) -> "Forecaster | None":
        if cls._default is False:
            p = lab_dir() / "forecaster.pkl"
            cls._default = cls.load(p) if p.exists() else None
        return cls._default  # type: ignore[return-value]


def _baselines(X: np.ndarray, h_s: int, mean_by_appr: np.ndarray, appr: np.ndarray) -> dict[str, np.ndarray]:
    q = X[:, FEATURES.index("queue")]
    lag30 = X[:, FEATURES.index("q_lag30")]
    return {"persistence": q, "linear_trend": np.maximum(0.0, q + (q - lag30) * h_s / 30.0), "approach_mean": mean_by_appr[appr]}


def _scores(y: np.ndarray, p: np.ndarray, cap: np.ndarray) -> dict[str, float]:
    err = p - y
    ss = float(((y - y.mean()) ** 2).sum())
    hit_t, hit_p = y >= SPILL_FRAC * cap, p >= SPILL_FRAC * cap
    tp = int((hit_t & hit_p).sum())
    prec = tp / max(1, int(hit_p.sum()))
    rec = tp / max(1, int(hit_t.sum()))
    return {"mae": round(float(np.abs(err).mean()), 3), "rmse": round(float(np.sqrt((err ** 2).mean())), 3),
            "r2": round(1.0 - float((err ** 2).sum()) / ss, 3) if ss else 0.0,
            "spill_precision": round(prec, 3), "spill_recall": round(rec, 3), "spill_f1": round(2 * prec * rec / max(1e-9, prec + rec), 3)}


def train(path: Path | None = None) -> dict[str, Any]:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge

    d = np.load(path or data_dir() / "dataset.npz")
    Xtr, ytr, atr = d["train_X"], d["train_y"], d["train_a"]
    Xva, yva = d["val_X"], d["val_y"]
    Xte, yte, ate = d["test_X"], d["test_y"], d["test_a"]
    cap_te = Xte[:, FEATURES.index("capacity")]
    mean_by_appr = np.array([ytr[atr == k, 0].mean() for k in range(int(atr.max()) + 1)])
    t0 = time.time()
    models, val, chosen = {}, {}, {}
    for j, name in enumerate(("p30", "p60")):
        best = None
        for lr, leaves in ((0.05, 31), (0.1, 31), (0.1, 63)):  # small grid, selected on validation seeds only
            m = HistGradientBoostingRegressor(max_iter=300, learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=40, l2_regularization=1.0,
                                              early_stopping=True, validation_fraction=0.1, random_state=0)
            m.fit(Xtr, ytr[:, j])
            mae = float(np.abs(m.predict(Xva) - yva[:, j]).mean())
            val[f"{name}:lr={lr},leaves={leaves}"] = round(mae, 4)
            if best is None or mae < best[0]:
                best = (mae, m, {"learning_rate": lr, "max_leaf_nodes": leaves, "iterations": int(m.n_iter_)})
        models[name], chosen[name] = best[1], best[2]
    train_s = time.time() - t0
    ridge = {name: Ridge(alpha=1.0).fit(Xtr, ytr[:, j]) for j, name in enumerate(("p30", "p60"))}
    fc = Forecaster(models, {})
    p30, p60 = fc.predict(Xte)
    results: dict[str, Any] = {}
    for j, (name, h, p) in enumerate((("p30", 30, p30), ("p60", 60, p60))):
        r = {"model": _scores(yte[:, j], p, cap_te), "ridge": _scores(yte[:, j], np.clip(ridge[name].predict(Xte), 0, cap_te), cap_te)}
        for bname, bp in _baselines(Xte, h, mean_by_appr, ate).items():
            r[bname] = _scores(yte[:, j], bp, cap_te)
        r["improvement_vs_persistence_mae_pct"] = round(100.0 * (1 - r["model"]["mae"] / r["persistence"]["mae"]), 1)
        results[name] = r
    # held-out scenario on its own (never seen in training)
    is_rr = _rush_mask(d)
    if is_rr is not None and is_rr.any():
        results["rush_ramp_only_p30"] = {"model": _scores(yte[is_rr, 0], p30[is_rr], cap_te[is_rr]),
                                         "persistence": _scores(yte[is_rr, 0], Xte[is_rr, 0], cap_te[is_rr])}
    exact = fc.check(Xte[:20000])
    assert exact < 1e-6, f"flat tree evaluation differs from scikit-learn by {exact}"
    lat = []
    for k in range(200):  # one control decision = 16 approaches x both horizons
        x = Xte[(k * 16) % (len(Xte) - 16):][:16]
        t1 = time.perf_counter()
        fc.predict(x)
        lat.append((time.perf_counter() - t1) * 1000)
    imp = {f: round(float(v), 4) for f, v in zip(FEATURES, _importance(models["p30"], Xte[:4000], yte[:4000, 0]))}
    meta = {"kind": "HistGradientBoostingRegressor x2 (30 s, 60 s)", "features": list(FEATURES), "chosen": chosen, "validation_mae": val,
            "rows": {"train": int(len(ytr)), "val": int(len(yva)), "test": int(len(yte))}, "train_wall_s": round(train_s, 1),
            "test": results, "inference_ms_per_decision": {"mean": round(float(np.mean(lat)), 3), "p95": round(float(np.percentile(lat, 95)), 3)},
            "flat_vs_sklearn_max_abs_diff": exact,
            "permutation_importance_p30": imp, "split": "by episode: train seeds 1000-1011, val 2000-2003, test 3000-3007 (+ held-out rush_ramp scenario)",
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    fc.meta = meta
    fc.save(lab_dir() / "forecaster.pkl")
    (lab_dir() / "forecast_eval.json").write_text(json.dumps(meta, indent=1))
    Forecaster._default = False
    return meta


def _rush_mask(d: Any) -> np.ndarray | None:
    man = json.loads((lab_dir() / "dataset_manifest.json").read_text())
    names = list(man["episodes"])
    e = d["test_e"]
    rr = np.array([names[i].split(":")[1] == "rush_ramp" for i in range(len(names))])
    return rr[e]


def _importance(model: Any, X: np.ndarray, y: np.ndarray) -> np.ndarray:
    rng = np.random.default_rng(0)
    base = np.abs(model.predict(X) - y).mean()
    out = []
    for k in range(X.shape[1]):
        Xp = X.copy()
        Xp[:, k] = rng.permutation(Xp[:, k])
        out.append(np.abs(model.predict(Xp) - y).mean() - base)
    return np.asarray(out)
