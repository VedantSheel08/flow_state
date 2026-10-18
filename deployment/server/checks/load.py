# Opens 6 WS clients on a severe-congestion run at 16x for 60 s; prints per-client frame rate, bandwidth, sim speed, gaps. Starts/stops the SHARED run.
import asyncio, json, time, urllib.request, websockets, statistics
H={"origin":"https://flowstate.nyc","content-type":"application/json"}
def post(p,b): 
    r=urllib.request.Request("https://api.flowstate.nyc"+p,json.dumps(b).encode(),H); return json.load(urllib.request.urlopen(r,timeout=30))
async def client(i,out,stop):
    c=await websockets.connect("wss://api.flowstate.nyc/ws",origin="https://flowstate.nyc",max_size=None)
    n=b=0; ts=[]
    while not stop.is_set():
        try: m=await asyncio.wait_for(c.recv(),3)
        except asyncio.TimeoutError: continue
        d=json.loads(m)
        if d.get("type")=="frame": n+=1; b+=len(m); ts.append((time.time(),d["t"]))
    out[i]=(n,b,ts); await c.close()
async def main():
    for run in range(1):
        r=post("/api/runs",{"scenario":"rush_hour","congestion":"severe","seed":42,"params":{"duration":5}}); print("started",r.get("id") or str(r)[:100])
    stop=asyncio.Event(); out={}
    tasks=[asyncio.create_task(client(i,out,stop)) for i in range(6)]
    await asyncio.sleep(14)  # warm-up
    post("/api/runs/current/control",{"action":"speed","speed":16}); t0=time.time(); print("speed 16 requested")
    await asyncio.sleep(60); stop.set(); await asyncio.gather(*tasks)
    post("/api/runs/current/control",{"action":"stop"})
    w=time.time()-t0
    for i,(n,b,ts) in sorted(out.items()):
        ts=[x for x in ts if x[0]>=t0]; adv=ts[-1][1]-ts[0][1]; g=sorted(ts[j+1][0]-ts[j][0] for j in range(len(ts)-1))
        print(f"client{i}: {len(ts)/w:.1f} fr/s, {b/1024/w:.0f} kB/s, sim advance {adv:.0f}s in {w:.0f}s = {adv/w:.1f}x, gap p50={g[len(g)//2]*1000:.0f}ms p95={g[int(len(g)*.95)]*1000:.0f}ms max={g[-1]*1000:.0f}ms")
asyncio.run(main())
