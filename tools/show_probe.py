import json, sys
for f in sys.argv[1:]:
    print(f)
    for r in json.load(open(f)):
        print(f"  {r['scenario'][5:]:12s}{r['controller'][:6]:8s} veh={r['vehicles']} wait={r['mean_wait_s']:6.1f} p95tr={r['p95_travel_s']:6.0f} qpk={r['queue_peak_veh']:4.0f} ({r['queue_peak_pct_storage']}%) spillLinks={r['spilled_links_peak']} spill%={r['spillback_pct']} minspd={r['min_speed_ms_30s_avg']} t_clr={r['t_cleared_s']} compl={r['completed_fraction']} tele={r['teleports']} coll={r['collisions']} ins={r['never_inserted']}")
