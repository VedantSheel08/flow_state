"""One line per evaluation report: python tools/summarize.py reports/final/*.json"""
import json, sys
for f in sorted(sys.argv[1:]):
    r = json.load(open(f)); s, c, sf, lg = r["summary"], r["classification"], r["safety"], r["ledger"]
    bg = lg.get("background", {})
    res = r.get("resources", {})
    print(f"{r['condition']:<12}{r['seed']:>3} dens={s['density']['mean']:>5} stor={s['storage']} halt={s['haltedShare']} travelP50={s['travelSpeedKmh']['p50']:>5} instP90={s['speedKmh']['p90']:>5} "
          f"act={s['activeVehicles']['mean']} thru/h={s['throughputPerHour']} qa={s['queueApproaches']} sg={s['stopGoVehicles']} spill={s['corridorSpillSeconds']}s cyc={s['longestBlockedCycleSeconds']}s "
          f"coll={sf['native']['collisionRecordObservations']} tele={sf['teleports']} requests={bg.get('requests')} submitted={bg.get('submitted')} dropped={bg.get('dropped')} held={bg.get('heldAtEnd')} resid={lg['residual']} "
          f"stepMs={res.get('stepMs', {}).get('mean')} tier={c['achievedTier']}")
