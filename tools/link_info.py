import sys, xml.etree.ElementTree as ET
net, jid, lanes = sys.argv[1], sys.argv[2], sys.argv[3:]
r = ET.parse(net).getroot()
cons = [c for c in r.iter("connection") if c.get("via") in lanes]
for c in cons:
    print("via", c.get("via"), "from", c.get("from"), c.get("fromLane"), "->", c.get("to"), c.get("toLane"), "dir", c.get("dir"), "state", c.get("state"), "linkIndex", c.get("linkIndex"))
tl = [t for t in r.iter("tlLogic") if t.get("id") == jid]
for t in tl:
    print("program", t.get("programID"))
    idx = [int(c.get("linkIndex")) for c in cons]
    for p in t.findall("phase"):
        print("  ", p.get("duration"), p.get("state"), " links:", [p.get("state")[i] for i in idx])
# foes
for j in r.iter("junction"):
    if j.get("id") == jid: print("junction type", j.get("type"))
