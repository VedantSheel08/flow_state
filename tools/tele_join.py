import json, re, collections, sys
d=json.load(open(sys.argv[1])); log=open(sys.argv[2]).read()
cause={}
for m in re.finditer(r"Teleporting vehicle '([^']*)'; waited too long \(([a-z ]*)\), lane='([^']*)', time=([\d]+\.[\d]+)",log):
    cause[(m.group(1),round(float(m.group(4)),2))]=m.group(2)
ct=collections.Counter(); 
for x in d["teleports"]:
    c=None
    for dt in (0,-.25,.25,-.5,.5):
        c=cause.get((x["veh"],round(x["t"]+dt,2))) or c
    x["cause"]=c
    ct[(c, str(x.get("tlsState"))[:1], "leader" if x.get("leader") else "noleader")]+=1
for k,v in sorted(ct.items(), key=lambda a:-a[1]): print(k,v)
json.dump(d,open(sys.argv[1],"w"),default=str,indent=1)
