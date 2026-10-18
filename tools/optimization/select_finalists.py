"""Pre-registered selection on development data only (dev + dev_ext seeds), written before the extended dev results were seen.

Rule: among candidates with all 64 development pairs, a candidate is *eligible* if, versus the queue heuristic on the same pairs,
it has zero collisions, zero SafeSignal audit violations, at most 10 % more teleports and at most 0.5 % lower throughput.
The deterministic pick is the eligible ``pressure@...`` with the lowest mean paired waiting-time difference; the ML pick is the
eligible ``ml_pressure@...`` with the lowest one. Both go to the final test, unchanged, together with the heuristic and the
previous ML controller. Writes ``artifacts/lab/optimization_v2_selection.json``.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

REF = "queue_heuristic"


def main() -> None:
    rows = bench.load("dev") + bench.load("dev_ext")
    full = {c for c in {r["controller"] for r in rows} if sum(r["controller"] == c for r in rows) == 64}
    ref_rows = [r for r in rows if r["controller"] == REF]
    table = {}
    for c in sorted(full - {REF}):
        rs = [r for r in rows if r["controller"] == c]
        p = bench.paired(rows, REF, c)
        tele, tele_ref = sum(r["teleports"] for r in rs), sum(r["teleports"] for r in ref_rows)
        ok = (sum(r["collisions"] for r in rs) == 0 and sum(sum(r["audit"].values()) for r in rs) == 0 and tele <= 1.1 * tele_ref
              and p["throughput_vph"]["pct"] >= -0.5)
        table[c] = {"eligible": ok, "pairs": p["pairs"], "wait_diff": p["mean_wait_s"]["diff"], "wait_ci95": p["mean_wait_s"]["ci95"],
                    "travel_diff": p["mean_travel_s"]["diff"], "throughput_pct": p["throughput_vph"]["pct"], "teleports": tele,
                    "teleports_ref": tele_ref, "queue_diff": p["queue_total_mean_veh"]["diff"]}
    pick = {}
    for fam in ("pressure", "ml_pressure"):
        el = [c for c, v in table.items() if v["eligible"] and c.split("@")[0] == fam]
        if el:
            best = min(el, key=lambda c: table[c]["wait_diff"])
            ctl = __import__("flowstate.lab.optimize", fromlist=["parse"]).parse(best)
            pick[fam] = {"id": best, "margin": ctl.MARGIN, "gamma": ctl.gamma, "fc_down": float(ctl.fc_down), "beta": ctl.beta,
                         "dev_wait_diff": table[best]["wait_diff"], "dev_wait_ci95": table[best]["wait_ci95"]}
    doc = {"rule": __doc__.strip(), "development_seeds": "4000-4015 x balanced, ns_heavy, ew_heavy, pulse (64 pairs)", "reference": REF,
           "candidates": table, "selected": pick}
    out = bench.ROOT / "artifacts" / "lab" / "optimization_v2_selection.json"
    out.write_text(json.dumps(doc, indent=1))
    print(json.dumps({"selected": pick, "candidates": {c: {k: v[k] for k in ("eligible", "wait_diff", "wait_ci95", "teleports")} for c, v in table.items()}}, indent=1))


if __name__ == "__main__":
    main()
