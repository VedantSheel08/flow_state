"""Compact table of every exploratory run (one row per condition+seed) so raw per-run reports need not be committed.

    python tools/experiments_table.py reports/experiments-summary.json NAME=dir [NAME=dir ...]
NAME tags the network the runs used (pre-rebuild / post-rebuild)."""
import glob, json, sys
rows = []
for spec in sys.argv[2:]:
    tag, d = spec.split("=", 1)
    for f in sorted(glob.glob(f"{d}/*.json")):
        r = json.load(open(f))
        if "summary" not in r:
            continue
        s, sf, lg = r["summary"], r["safety"], r["ledger"]
        rows.append({"network": tag, "condition": r["condition"], "seed": r["seed"], "tier": r["classification"]["achievedTier"], "density": s["density"]["mean"], "storage": s["storage"],
                     "halted": s["haltedShare"], "travelP50": s["travelSpeedKmh"]["p50"], "active": s["activeVehicles"]["mean"], "tripsPerHour": s["throughputPerHour"],
                     "collisions": sf["native"]["collisionRecordObservations"], "teleports": sf["teleports"], "ledgerResidual": lg["residual"],
                     "rescueOff": r["manifest"]["condition"]["sumo_options"].get("--time-to-teleport") == "-1"})
json.dump(rows, open(sys.argv[1], "w"), separators=(",", ":"))
print(len(rows), "runs")
