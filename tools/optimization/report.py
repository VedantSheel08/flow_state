"""Compact comparison report for optimization v2 -> artifacts/lab/optimization_v2_report.json (and a printed table)."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

REF = "queue_heuristic"
KEY = ["mean_wait_s", "mean_travel_s", "p95_travel_s", "throughput_vph", "queue_total_mean_veh", "queue_max_veh", "spillback_pct",
       "teleports", "collisions", "vehicles_unfinished", "emergency_mean_stopped_s", "co2_kg", "signal_switches"]


def block(rows, ids):
    out = {}
    for c in ids:
        rs = [r for r in rows if r["controller"] == c]
        if not rs or c == REF:
            continue
        p = bench.paired(rows, REF, c, metrics=KEY)
        ml = [r["ml"] for r in rs if r.get("ml")]
        out[c] = {"pairs": p["pairs"], **{m: p[m] for m in KEY},
                  "safety": {"collisions": sum(r["collisions"] for r in rs), "teleports": sum(r["teleports"] for r in rs),
                             "teleports_ref": sum(r["teleports"] for r in rows if r["controller"] == REF and any(
                                 bench.key(x)[1:] == bench.key(r)[1:] for x in rs)),
                             "safesignal_violations": sum(sum(r["audit"].values()) for r in rs)},
                  "ml_influence": ({"decisions": sum(m["decisions"] for m in ml), "changed": sum(m["changed"] for m in ml),
                                    "switchedEarlier": sum(m["switchedEarlier"] for m in ml), "heldLonger": sum(m["heldLonger"] for m in ml)} if ml else None)}
    return out


def main() -> None:
    final = bench.load("final")
    ids = ["ml_forecast", "pressure", "ml_pressure"]
    std = [r for r in final if r["seed"] < 5100]
    unseen = {x: [r for r in final if r["seed"] >= 5100 and abs(r["intensity"] - x) < 1e-6] for x in (0.8, 1.3)}
    doc = {"reference": REF,
           "final_all": block(final, ids),
           "final_standard": block(std, ids),
           "final_by_intensity": {str(x): block(rs, ids) for x, rs in unseen.items()},
           "final_by_scenario": {sc: {c: bench.paired([r for r in final if r["scenario"] == sc], REF, c, metrics=["mean_wait_s"])["mean_wait_s"]
                                      for c in ids} for sc in bench.ALL_SC},
           "prev_heldout_reference": block(bench.load("prev_heldout"), ["pressure", "ml_pressure"]),
           "development": json.loads((bench.ROOT / "artifacts" / "lab" / "optimization_v2_selection.json").read_text())}
    ph = [r for r in bench.load("prev_heldout") if r["controller"] == REF]
    if ph:  # the previous evaluation's heuristic numbers must be reproduced exactly by this harness
        old = json.loads((bench.ROOT / "artifacts" / "lab" / "comparison_eval.json").read_text())["rows"]
        oldh = {(r["scenario"], r["seed"]): r["mean_wait_s"] for r in old if r["controller"] == REF}
        doc["prev_heldout_reproduction"] = {"episodes": len(ph), "max_abs_wait_diff_vs_comparison_eval": round(max(
            abs(r["mean_wait_s"] - oldh[(r["scenario"], r["seed"])]) for r in ph), 6)}
    lat = {}
    for c in [REF] + ids:
        rs = [r for r in final if r["controller"] == c]
        lat[c] = {k: round(float(np.mean([r["latency_ms"][k] for r in rs])), 3) for k in ("observation", "inference", "decide", "action_total_mean", "action_total_p95", "sumo_step")}
    doc["latency_ms_under_load"] = lat
    out = bench.ROOT / "artifacts" / "lab" / "optimization_v2_report.json"
    out.write_text(json.dumps(doc, indent=1))
    for name, blk in [("FINAL all", doc["final_all"]), ("FINAL standard", doc["final_standard"]), ("FINAL x0.8", doc["final_by_intensity"]["0.8"]),
                      ("FINAL x1.3", doc["final_by_intensity"]["1.3"]), ("PREV held-out", doc["prev_heldout_reference"])]:
        print(f"\n== {name}")
        for c, v in blk.items():
            w, t, th, q = v["mean_wait_s"], v["mean_travel_s"], v["throughput_vph"], v["queue_total_mean_veh"]
            print(f"{c:12s} n={v['pairs']:3d} wait {w['left']:6.1f}->{w['right']:6.1f} ({w['diff']:+.2f}, {w['pct']:+.1f}%, CI {w['ci95']}, W/L {w['better']}/{w['worse']}) "
                  f"travel {t['diff']:+.2f} CI {t['ci95']} thru {th['pct']:+.2f}% queue {q['diff']:+.2f} CI {q['ci95']} p95tt {v['p95_travel_s']['diff']:+.1f} "
                  f"safety {v['safety']} ml {v['ml_influence']}")
    print("\nby scenario:", json.dumps({sc: {c: (v["diff"], v["ci95"]) for c, v in d.items()} for sc, d in doc["final_by_scenario"].items()}))
    print("latency:", json.dumps(lat))
    print("reproduction:", doc.get("prev_heldout_reproduction"))


if __name__ == "__main__":
    main()
