import sys, xml.etree.ElementTree as ET, collections
net = sys.argv[1]
root = ET.parse(net).getroot()
bad = []; stats = collections.defaultdict(list); n_prog = 0
for tl in root.iter("tlLogic"):
    n_prog += 1
    ph = [(float(p.get("duration")), p.get("state")) for p in tl.findall("phase")]
    L = len(ph)
    for i, (d, s) in enumerate(ph):
        nx = ph[(i + 1) % L][1]
        for k, (a, b) in enumerate(zip(s, nx)):
            # vehicle link going from green to red directly (no yellow) is illegal
            if a in "Gg" and b == "r":
                bad.append((tl.get("id"), tl.get("programID"), i, k, "green->red without yellow"))
        if "y" in s and "G" not in s and "g" not in s:
            stats["yellow_only"].append(d)
        if set(s) <= {"r"}:
            stats["all_red"].append(d)
        if "y" in s:
            stats["yellow"].append(d)
for k, v in stats.items():
    print(k, "n", len(v), "min", min(v), "max", max(v))
print("programs", n_prog, "violations", len(bad))
for b in bad[:10]: print(b)

# classify links: crossing (pedestrian) vs vehicle
ped = collections.defaultdict(set)
for c in root.iter("connection"):
    if c.get("tl") and c.get("linkIndex") is not None:
        f = c.get("from", "")
        if f.startswith(":"):  # internal crossing / walkingarea
            ped[c.get("tl")].add(int(c.get("linkIndex")))
veh_bad = [b for b in bad if b[3] not in ped[b[0]]]
print("violations on vehicle links:", len(veh_bad), "| on pedestrian crossing links:", len(bad) - len(veh_bad))
for b in veh_bad[:10]: print(b)
