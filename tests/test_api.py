"""REST + WebSocket integration with a live SUMO run."""

import json
import time

from fastapi.testclient import TestClient

from flowstate.api.app import app


def test_api_end_to_end():
    with TestClient(app) as client:
        h = client.get("/api/health").json()
        assert h["ok"] and h["sumo"]
        assert len(client.get("/api/scenarios").json()) >= 10
        g = client.get("/api/networks/district").json()
        assert g["edges"] and g["junctions"] and g["city"]["buildings"]
        assert client.get("/api/networks/nope").status_code == 404
        assert client.post("/api/runs", json={"scenario": "accident", "params": {"severity": "bogus"}}).status_code == 400
        assert client.post("/api/runs", json={"scenario": "nope"}).status_code == 400

        with client.websocket_connect("/ws") as ws:
            assert json.loads(ws.receive_text())["type"] == "hello"
            run = client.post("/api/runs", json={"scenario": "rush_hour", "params": {"duration": 5}, "seed": 99, "speed": 16}).json()
            assert run["seed"] == 99 and run["briefing"]
            frames, stats = [], []
            deadline = time.time() + 60
            while time.time() < deadline and (len(frames) < 5 or not stats):
                m = json.loads(ws.receive_text())
                if m.get("run") != run["id"]:
                    continue
                if m["type"] == "frame":
                    frames.append(m)
                elif m["type"] == "stats":
                    stats.append(m)
            assert len(frames) >= 5 and stats
            assert frames[-1]["t"] > frames[0]["t"]
            vid = frames[-1]["v"][0][0]
            assert client.get(f"/api/runs/current/vehicles/{vid}").status_code in (200, 404)
            jid = next(iter(frames[-1]["tls"]))
            assert client.get(f"/api/runs/current/junctions/{jid}").json()["signal"]

            assert client.post("/api/runs/current/control", json={"action": "pause"}).json()["paused"]
            time.sleep(0.3)
            t0 = client.get("/api/runs/current").json()["status"]["t"]
            time.sleep(0.6)
            assert client.get("/api/runs/current").json()["status"]["t"] == t0
            client.post("/api/runs/current/control", json={"action": "resume"})
            assert client.post("/api/runs/current/control", json={"action": "speed", "speed": 2}).json()["speed"] == 2
            assert client.post("/api/runs/current/params", json={"traffic": 1.2}).json() == {"traffic": 1.2}
            assert client.post("/api/runs/current/params", json={"duration": 30}).status_code == 400
            again = client.post("/api/runs/current/control", json={"action": "restart"}).json()
            assert again["seed"] == 99 and again["briefing"] == run["briefing"]
            assert client.post("/api/runs/current/control", json={"action": "stop"}).json()["state"] == "idle"
        assert any(r["seed"] == 99 for r in client.get("/api/runs").json())
