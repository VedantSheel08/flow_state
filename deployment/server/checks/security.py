# Protection checks against the public API: WS per-IP cap (with spoofed X-Forwarded-For), bad Origin, CORS, closed ports, HTTP->HTTPS, rate limit (429), duration cap. Starts no simulation.
import asyncio, json, urllib.request, urllib.error, socket, websockets, ssl
async def wsopen(origin="https://flowstate.nyc", xff=None):
    h={"X-Forwarded-For":xff} if xff else {}
    try:
        c=await websockets.connect("wss://api.flowstate.nyc/ws",origin=origin,additional_headers=h,open_timeout=15)
        await asyncio.wait_for(c.recv(),8); return c
    except Exception as e: return str(e)[:60]
async def main():
    cs=[]; res=[]
    for i in range(14):
        r=await wsopen(xff=f"10.9.{i}.1"); res.append(r if isinstance(r,str) else "ok"); 
        if not isinstance(r,str): cs.append(r)
    print("per-IP cap with spoofed XFF per attempt:", res.count("ok"),"accepted of 14; rejected:", [x for x in res if x!="ok"][:2])
    for c in cs: await c.close()
    await asyncio.sleep(2)
    print("after closing, new connection:", "ok" if not isinstance(await wsopen(),str) else "FAIL")
    print("bad-origin WS:", await wsopen(origin="https://evil.example"))
asyncio.run(main())
def req(method,url,headers={},data=None):
    r=urllib.request.Request(url,data,headers,method=method)
    try:
        x=urllib.request.urlopen(r,timeout=15); return x.status,{k.lower():v for k,v in x.headers.items()}
    except urllib.error.HTTPError as e: return e.code,{k.lower():v for k,v in e.headers.items()}
s,h=req("GET","https://api.flowstate.nyc/api/health",{"Origin":"https://evil.example"}); print("CORS evil origin ->",s,"ACAO=",h.get("access-control-allow-origin"))
s,h=req("GET","https://api.flowstate.nyc/api/health",{"Origin":"https://flowstate.nyc"}); print("CORS good origin ->",s,"ACAO=",h.get("access-control-allow-origin"))
s,h=req("OPTIONS","https://api.flowstate.nyc/api/runs",{"Origin":"https://evil.example","Access-Control-Request-Method":"POST"}); print("preflight evil ->",s,h.get("access-control-allow-origin"))
for port in (8000,5000,3000):
    sk=socket.socket(); sk.settimeout(6)
    try: sk.connect(("137.220.52.142",port)); print("port",port,"OPEN!")
    except Exception as e: print("port",port,"blocked/closed:",type(e).__name__)
    sk.close()
try:
    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**k): return None
    o=urllib.request.build_opener(NoRedir); o.open("http://api.flowstate.nyc/api/health",timeout=10)
except urllib.error.HTTPError as e: print("http->https:",e.code,e.headers.get("location"))
print("HSTS:", req("GET","https://api.flowstate.nyc/api/health")[1].get("strict-transport-security"))
cx=ssl.create_default_context(); c=cx.wrap_socket(socket.socket(),server_hostname="api.flowstate.nyc"); c.connect(("api.flowstate.nyc",443)); print("TLS",c.version(), c.getpeercert()["notAfter"], dict(x[0] for x in c.getpeercert()["issuer"])["organizationName"])
H={"Origin":"https://flowstate.nyc","Content-Type":"application/json"}
body=lambda sc,p={}: json.dumps({"scenario":sc,"params":p}).encode()
s,_=req("POST","https://api.flowstate.nyc/api/runs",H,body("rush_hour",{"duration":99})); print("duration 99 min ->",s,"(expect 400)")
codes=[req("POST","https://api.flowstate.nyc/api/runs",H,body("__nope__"))[0] for _ in range(9)]; print("9 rapid run starts (invalid scenario) ->",codes,"(429 expected after 6/min)")
