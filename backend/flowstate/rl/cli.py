"""Command line: ``python -m flowstate.rl.cli {train,tune-fixed,evaluate,validate-policy}``.

Run from the repository root with ``PYTHONPATH=backend`` (the RL tooling is a separate package; nothing in the
existing FlowState entry points changes).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .controllers import artifacts_dir
from .demand import HELD_OUT_SCENARIOS, STRESS_INTENSITY, TRAIN_SCENARIOS

TEST_SEEDS = tuple(range(3000, 3010))


def cmd_train(a: argparse.Namespace) -> None:
    from .train import TrainConfig, train

    cfg = TrainConfig(seed=a.seed, total_steps=a.steps, n_envs=a.envs, validate_every=a.validate_every, out_dir=a.out,
                      norm_reward=a.norm_reward, agent_mode=a.agent, blend=a.blend,
                      validation_seeds=tuple(range(2000, 2000 + a.val_seeds)), shared_policy=a.shared, gamma=a.gamma, n_steps=a.n_steps, batch_size=a.batch_size, ent_coef=a.ent_coef)
    print(json.dumps(train(cfg), indent=1))


def cmd_tune_fixed(a: argparse.Namespace) -> None:
    """Pick the best uniform green length for fixed-time control using *training* seeds only."""
    import numpy as np

    from .evaluate import evaluate

    best, table = None, {}
    for g in a.greens:
        rows = evaluate([f"fixed_time@{g}"], list(TRAIN_SCENARIOS), list(range(1000, 1000 + a.seeds)), workers=a.workers)
        w = float(np.mean([r["mean_wait_s"] for r in rows]))
        table[g] = {"mean_wait_s": w, "mean_travel_s": float(np.mean([r["mean_travel_s"] for r in rows]))}
        print(f"green {g:>3} s -> mean wait {w:.1f} s", flush=True)
        if best is None or w < table[best]["mean_wait_s"]:
            best = g
    out = {"best_green_s": best, "table": table, "scenarios": list(TRAIN_SCENARIOS), "seeds": f"1000..{999 + a.seeds}", "note": "training seeds only"}
    artifacts_dir().mkdir(parents=True, exist_ok=True)
    (artifacts_dir() / "fixed_time_tuning.json").write_text(json.dumps(out, indent=1))
    print("best uniform green:", best)


def cmd_tune_heuristic(a: argparse.Namespace) -> None:
    """Grid-search the heuristic's switching margin and minimum hold on the *training* seeds."""
    import itertools

    import numpy as np

    from .evaluate import evaluate

    best, table = None, {}
    for margin, hold in itertools.product(a.margins, a.holds):
        cid = f"queue_heuristic@{margin},{hold},0.25"
        rows = evaluate([cid], list(TRAIN_SCENARIOS), list(range(1000, 1000 + a.seeds)), workers=a.workers)
        w = float(np.mean([r["mean_wait_s"] for r in rows]))
        table[f"margin={margin},hold={hold}"] = w
        print(f"margin {margin} hold {hold} -> mean wait {w:.1f} s", flush=True)
        if best is None or w < best[0]:
            best = (w, {"margin": float(margin), "hold_s": float(hold), "weight": 0.25})
    out = {"best": best[1], "best_mean_wait_s": best[0], "table": table, "scenarios": list(TRAIN_SCENARIOS), "seeds": f"1000..{999 + a.seeds}", "note": "training seeds only"}
    artifacts_dir().mkdir(parents=True, exist_ok=True)
    (artifacts_dir() / "heuristic_tuning.json").write_text(json.dumps(out, indent=1))
    print("best:", best)


def cmd_validate_policy(a: argparse.Namespace) -> None:
    """Gate a candidate checkpoint on the *gate* seeds (2004-2015: not used for checkpoint selection, never the test seeds).

    The policy passes only when, on identical demand, its mean waiting time is not worse than the better baseline's and it
    has no more teleports or collisions than that baseline. Every verdict is recorded under ``validation_records/``; the
    checkpoint is published (``ppo_policy.zip`` + ``validation.json`` carrying its SHA-256) *only if it passes*, so a failed
    policy is neither offered by the API nor selectable.
    """
    import hashlib
    import shutil

    import numpy as np

    from .evaluate import evaluate
    from .train import GATE_SEEDS, VALIDATION_INTENSITY

    cand = Path(a.checkpoint)
    cand_zip = cand if cand.suffix == ".zip" else Path(str(cand) + ".zip")
    ids = ["fixed_time", "queue_heuristic", f"rl_ppo@{cand}"]
    rows = evaluate(ids, list(TRAIN_SCENARIOS), list(GATE_SEEDS), intensity=VALIDATION_INTENSITY, workers=a.workers)
    agg = {}
    for cid in ids:
        r = [x for x in rows if x["requested_controller"] == cid]
        agg[cid.split("@")[0]] = {"mean_wait_s": float(np.mean([x["mean_wait_s"] for x in r])), "mean_travel_s": float(np.mean([x["mean_travel_s"] for x in r])),
                                  "teleports": int(sum(x["teleports"] for x in r)), "collisions": int(sum(x["collisions"] for x in r)), "episodes": len(r)}
    base = min((agg["fixed_time"], agg["queue_heuristic"]), key=lambda m: m["mean_wait_s"])
    rl = agg["rl_ppo"]
    reasons = []
    if rl["mean_wait_s"] > base["mean_wait_s"]:
        reasons.append(f"mean wait {rl['mean_wait_s']:.1f} s is worse than the better baseline ({base['mean_wait_s']:.1f} s)")
    if rl["teleports"] > base["teleports"] or rl["collisions"] > base["collisions"]:
        reasons.append("more teleports or collisions than the better baseline")
    sha = hashlib.sha256(cand_zip.read_bytes()).hexdigest()
    result = {"passed": not reasons, "reason": "; ".join(reasons) or "not worse than the better baseline on the gate seeds, no extra teleports or collisions",
              "gate_seeds": list(GATE_SEEDS), "scenarios": list(TRAIN_SCENARIOS), "intensity": VALIDATION_INTENSITY, "results": agg,
              "checkpoint_source": str(cand), "checkpoint_sha256": sha}
    rec = artifacts_dir() / "validation_records"
    rec.mkdir(parents=True, exist_ok=True)
    tag = cand_zip.parent.name + "_" + cand_zip.stem
    (rec / f"{tag}.json").write_text(json.dumps(result, indent=1))
    if result["passed"]:
        shutil.copyfile(cand_zip, artifacts_dir() / "ppo_policy.zip")
        (artifacts_dir() / "validation.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print("PUBLISHED" if result["passed"] else "REJECTED (not published)")


def cmd_evaluate(a: argparse.Namespace) -> None:
    from .evaluate import evaluate, save, summarise

    scenarios = a.scenarios or list(TRAIN_SCENARIOS + HELD_OUT_SCENARIOS)
    seeds = list(range(a.seed_start, a.seed_start + a.seeds))
    t0 = time.time()
    rows = evaluate(a.controllers, scenarios, seeds, intensity=a.intensity, workers=a.workers,
                    progress=lambda k, n: print(f"\r{k}/{n}", end="", flush=True))
    print()
    summary = summarise(rows, baseline=a.baseline)
    meta = {"scenarios": scenarios, "seeds": seeds, "intensity": a.intensity, "controllers": a.controllers, "wall_seconds": round(time.time() - t0)}
    save(rows, summary, Path(a.out), meta)
    print(f"saved {a.out}  ({len(rows)} episodes, {meta['wall_seconds']} s)")


def cmd_compare_json(a: argparse.Namespace) -> None:
    """Worker side of ``POST /api/rl/compare``: read a request document, run it, write the result document."""
    from .service import CompareRequest, run_comparison

    req = CompareRequest.model_validate_json(Path(a.request).read_text())
    result = run_comparison(req, progress=lambda k, n: print(f"PROGRESS {k} {n}", flush=True))
    Path(a.out).write_text(json.dumps(result), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="flowstate.rl")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--seed", type=int, default=1)
    t.add_argument("--steps", type=int, default=300_000)
    t.add_argument("--envs", type=int, default=6)
    t.add_argument("--validate-every", type=int, default=30_000)
    t.add_argument("--out", default="artifacts/rl/run")
    t.add_argument("--norm-reward", action="store_true")
    t.add_argument("--agent", action="store_true", help="per-junction agents, observation v2, blended reward")
    t.add_argument("--blend", type=float, default=0.5, help="weight of the junction's own reward")
    t.add_argument("--val-seeds", type=int, default=2, help="selection seeds 2000..2000+n-1")
    t.add_argument("--shared", action="store_true", help="parameter-shared per-junction policy")
    t.add_argument("--gamma", type=float, default=0.99)
    t.add_argument("--n-steps", type=int, default=90)
    t.add_argument("--batch-size", type=int, default=270)
    t.add_argument("--ent-coef", type=float, default=0.01)
    t.set_defaults(fn=cmd_train)
    f = sub.add_parser("tune-fixed")
    f.add_argument("--greens", type=int, nargs="+", default=[15, 20, 25, 30, 40, 50])
    f.add_argument("--seeds", type=int, default=3)
    f.add_argument("--workers", type=int, default=6)
    f.set_defaults(fn=cmd_tune_fixed)
    h = sub.add_parser("tune-heuristic")
    h.add_argument("--margins", type=float, nargs="+", default=[1, 3, 6, 10])
    h.add_argument("--holds", type=float, nargs="+", default=[14, 20, 28])
    h.add_argument("--seeds", type=int, default=3)
    h.add_argument("--workers", type=int, default=6)
    h.set_defaults(fn=cmd_tune_heuristic)
    v = sub.add_parser("validate-policy")
    v.add_argument("checkpoint")
    v.add_argument("--workers", type=int, default=6)
    v.set_defaults(fn=cmd_validate_policy)
    e = sub.add_parser("evaluate")
    e.add_argument("--controllers", nargs="+", default=["fixed_time", "queue_heuristic"])
    e.add_argument("--scenarios", nargs="*")
    e.add_argument("--seeds", type=int, default=10)
    e.add_argument("--seed-start", type=int, default=TEST_SEEDS[0])
    e.add_argument("--intensity", type=float, default=1.0)
    e.add_argument("--workers", type=int, default=6)
    e.add_argument("--baseline", default="fixed_time")
    e.add_argument("--out", default="artifacts/rl/evaluation.json")
    e.set_defaults(fn=cmd_evaluate)
    c = sub.add_parser("compare-json")
    c.add_argument("--request", required=True)
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_compare_json)
    a = p.parse_args(argv)
    a.fn(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
