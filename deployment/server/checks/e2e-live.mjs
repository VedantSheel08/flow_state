// Live browser check of https://flowstate.nyc: run -> frames -> forced WS drop/reconnect -> scenario switch -> end run.
// usage: npm i playwright && node e2e-live.mjs   (drives the SHARED live run: tell the team first)
import { chromium } from 'playwright';
const API='https://api.flowstate.nyc'; const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const api=async p=>(await fetch(API+p,{headers:{origin:'https://flowstate.nyc'}})).json();
const b=await chromium.launch(); const ctx=await b.newContext({viewport:{width:1500,height:900}}); const p=await ctx.newPage();
let routed=[]; const st={frames:0,ts:[],opens:0};
await p.routeWebSocket(/api\.flowstate\.nyc\/ws/, ws=>{ st.opens++; const srv=ws.connectToServer(); routed.push(ws);
  srv.onMessage(m=>{ ws.send(m); try{const d=JSON.parse(String(m)); if(d.type==='frame'){st.frames++; st.ts.push([Date.now(),d.t])}}catch{} });
  ws.onMessage(m=>srv.send(m)); ws.onClose(()=>srv.close()); srv.onClose(()=>{}); });
await p.goto('https://flowstate.nyc',{waitUntil:'networkidle'}); await sleep(2000);
const t0=Date.now(); await p.getByRole('button',{name:/Generate & Run/}).click();
while(!st.frames && Date.now()-t0<90000) await sleep(200);
console.log('click -> first frame', ((Date.now()-t0)/1000).toFixed(1)+'s');
const f0=st.frames, w0=Date.now(); await sleep(10000);
const n=st.frames-f0, w=(Date.now()-w0)/1000; const tsr=st.ts.filter(x=>x[0]>=w0); 
const gaps=tsr.slice(1).map((x,i)=>x[0]-tsr[i][0]); gaps.sort((a,b)=>a-b);
console.log(`1x: ${(n/w).toFixed(1)} frames/s over ${w.toFixed(0)}s; sim advance ${(tsr.at(-1)[1]-tsr[0][1]).toFixed(1)}s; inter-frame gap p50=${gaps[gaps.length>>1]}ms p95=${gaps[Math.floor(gaps.length*.95)]}ms max=${gaps.at(-1)}ms`);
// real reconnect: server-side drop of the routed socket
const before=st.frames, opens=st.opens; routed[0].close({code:1006,reason:'test drop'}); const d0=Date.now();
let rec=false; for(let i=0;i<60;i++){ await sleep(500); if(st.opens>opens && st.frames>before+5){rec=true;break} }
console.log('forced WS drop -> reconnected & streaming:', rec, 'in', ((Date.now()-d0)/1000).toFixed(1)+'s', 'opens',st.opens);
console.log('banner after reconnect:', /unreachable/i.test(await p.locator('body').innerText()));
await p.screenshot({path:'reconnect.png'});
// scenario switch
await p.getByText('Road Accident').first().click(); await p.getByRole('button',{name:/Generate & Run/}).click();
let ok=false; for(let i=0;i<60;i++){ const c=await api('/api/runs/current'); if(c.run?.scenario==='accident'&&c.status?.state==='running'){ok=true;break} await sleep(1000)}
console.log('switch to accident -> running:', ok); await sleep(5000); await p.screenshot({path:'accident.png'});
await p.getByRole('button',{name:'End run'}).click(); await sleep(3000); console.log('final',(await api('/api/runs/current')).status?.state);
await b.close();
