"""Drive the real Intelligence Lab API (no frontend) with each demo scenario: status listing, manifest endpoint, a recorded
ML-vs-non-ML pair, and a live WebSocket session whose worker CPU/RAM is sampled. Writes artifacts/demo/lab_integration.json.

    python tools/verify_lab_integration.py [port]
"""

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil
import websockets

ROOT = Path(__file__).resolve().parents[1]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8016
BASE = f"http://127.0.0.1:{PORT}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())


def post(path, body):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


async def live(sc, seconds=45.0, speed=8):
    got = {"frames": 0, "obs": 0, "t_last": 0, "errors": []}
    samples = []
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/live", max_size=None) as ws:
        await ws.send(json.dumps(sc["lab"]["liveStart"] | {"speed": speed}))
        t0 = time.time()
        proc = None
        while time.time() - t0 < seconds:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
            except asyncio.TimeoutError:
                continue
            if m["type"] == "frame":
                got["frames"] += 1
                got["t_last"] = m["t"]
            elif m["type"] == "obs":
                got["obs"] += 1
            elif m["type"] == "error":
                got["errors"].append(m["error"])
                break
            if proc is None and got["frames"] > 3:
                kids = [c for c in psutil.Process(server.pid).children(recursive=True) if "python" in c.name().lower()]
                proc = max(kids, key=lambda c: c.memory_info().rss) if kids else None
                if proc:
                    proc.cpu_percent(None)
            if proc and got["frames"] % 10 == 0 and got["frames"]:
                try:
                    samples.append((proc.cpu_percent(None), proc.memory_info().rss / 1e6))
                except psutil.Error:
                    pass
        await ws.send(json.dumps({"cmd": "stop"}))
    wall = time.time() - t0
    got |= {"wall_s": round(wall, 1), "speed": speed, "frames_per_wall_s": round(got["frames"] / wall, 1),
            "sim_s_per_wall_s": round(got["t_last"] / wall, 1),
            "worker_rss_mb_max": round(max((s[1] for s in samples), default=0), 1)}
    return got


server = subprocess.Popen([sys.executable, "-m", "flowstate.lab.cli", "serve", "--port", str(PORT)], cwd=ROOT / "backend")
try:
    for _ in range(60):
        try:
            status = get("/api/lab/status")
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    manifest = get("/api/lab/demo-scenarios")
    out = {"listed": [s["id"] for s in status["scenarios"] if s["id"].startswith("demo_")], "manifestSchema": manifest["schema"], "scenarios": {}}
    for sc in manifest["scenarios"]:
        job = post("/api/lab/replays", sc["lab"]["pairRequest"])
        while get(f"/api/lab/jobs/{job['id']}")["state"] == "running":
            time.sleep(3)
        job = get(f"/api/lab/jobs/{job['id']}")
        rec = {"job": job["state"], "wall_s": job.get("wall_s"), "error": job.get("error")}
        if job["state"] == "done":
            r = job["result"]
            rec |= {"replayId": r["id"], "demandSha": r["demandSha"], "duration_s": r["duration_s"], "sameDemand": r["demandSha"] == sc["demand"]["demandSha256"],
                    "diff": {d["key"]: [d["left"], d["right"]] for d in r["diff"] if d["key"] in ("mean_wait_s", "queue_max_veh", "spillback_pct", "teleports", "collisions")}}
        rec["live"] = asyncio.run(live(sc))
        out["scenarios"][sc["id"]] = rec
        print(sc["id"], json.dumps(rec)[:400], flush=True)
    (ROOT / "artifacts" / "demo" / "lab_integration.json").write_text(json.dumps(out, indent=1))
finally:
    server.terminate()
