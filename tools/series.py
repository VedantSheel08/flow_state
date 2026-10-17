import json, sys
for f in sys.argv[1:]:
    r=json.load(open(f)); s=r["series"]; c=r["safety"]
    print(f.split("/")[-1], "teleports", c["teleports"], "coll", c["native"]["collisionRecordObservations"], "resid", r["ledger"]["residual"])
    t=s["t"]; n=len(t)
    print("   t   active halted dens  p50 p90  arrived/min")
    last=None
    for i in range(0,n,60):
        j=min(i+59,n-1)
        arr=s["arrivedCum"][j]-(s["arrivedCum"][i-1] if i else 0)
        print(f"{t[i]:6.0f} {s['active'][j]:6} {sum(s['halted'][i:j+1])/(j-i+1):6.2f} {sum(s['density'][i:j+1])/(j-i+1):6.1f} {s['p50'][j]:4} {s['p90'][j]:4} {arr:6}")
