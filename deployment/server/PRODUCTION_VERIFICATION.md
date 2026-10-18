# Production verification — 2026-10-18 (release `0064bcb`, https://flowstate.nyc + https://api.flowstate.nyc)

Run from a Windows workstation against the public site; server inspected over SSH (read-only except the additions listed at the end).
Note: the live run is **shared** and other people were using the site during the tests (their control calls appear in the journal).

## State
`/opt/flowstate/app/VERSION` = `0064bcb`; `flowstate` and `caddy` active, 0 restarts; SUMO 1.28.0 / traci; effective env `FLOWSTATE_MAX_WS_PER_IP=12`, `FLOWSTATE_MAX_WS_CLIENTS=40` (drop-in); ufw allows only 22/80/443; app listens on `127.0.0.1:8000`; Let's Encrypt cert (TLS 1.3) valid to 2027-01-15.

## Browser (Playwright/Chromium against https://flowstate.nyc)
| Check | Result |
|---|---|
| Toronto King West map, intersection picker, scenarios list load from the Vultr backend | pass |
| WebSocket connects; no "Simulation server unreachable" | pass |
| Generate & Run → first frame | pass, **8.1 s** (includes warm-up) |
| Streaming at 1× | 4.0 frames/s, inter-frame gap p50 261 ms / p95 291 ms / max 301 ms; sim clock advanced 9.8 s per 10 s |
| Signal states in frames (g/y/r), live metrics endpoint, UI leaves "No simulation running" | pass |
| Pause freezes the sim clock; Resume advances | pass |
| Speed 2× / 4× / 8× (rush hour) | 1.94 / 3.76 / 7.80 sim-s per s |
| 6 tabs from one IP, all connected and streaming | pass (5/5 extra tabs) |
| Forced WebSocket drop (server-side close) → client reconnect | pass, resumed in **2.5 s**, no error banner |
| Scenario switch (Road Accident) starts a new run; End run returns to idle | pass |
A first script of mine had three wrong assertions (counted frames during warm-up, wrong scenario id, offline emulation that did not actually drop the socket); they were fixed and re-run, results above are the corrected ones.

## Load (severe congestion preset, speed 16× requested, 6 WS clients, 60 s)
Achieved **4.7×** sim speed (SUMO is single-threaded; 16× cannot be delivered under severe load — consistent with `docs/BACKEND_PERFORMANCE.md`). All 6 clients received identical streams: 6.2 frames/s, 78 kB/s each, gap p50 66 ms / p95 89 ms / max ≈150 ms.
Server: ~14 % of 8 vCPU (≈1.1 cores, the SUMO process), service cgroup ≈ 64 MB idle → 88 MB under load, load average < 0.3. Bandwidth scales with viewers: 40 clients ≈ 3 MB/s ≈ 25 Mbit/s at that load.

## Protections (checks/security.py)
- Per-IP WS cap holds under **spoofed `X-Forwarded-For`** (Caddy overwrites it; 11 accepted of 14 attempts while other viewers shared the IP; the rest got HTTP 403). Slot freed after close.
- WS with `Origin: https://evil.example` → 403. CORS: only `flowstate.nyc`/`www.flowstate.nyc` get `Access-Control-Allow-Origin`; evil-origin preflight → 400.
- Ports 8000/5000/3000 time out from outside; HTTP → HTTPS 308.
- Run starts limited to 6/min per IP (429 from the 6th; invalid scenario used, nothing started). `duration=99` → 400 (cap 15 min).
- Gaps (not changed): no HSTS header; origin checks are not authentication — any visitor can control the shared run; global-cap (40) rejection not exercised from a single IP; one-comparison-job guard (409) not exercised live (heavy job), covered by the test suite.

## Backend test suite vs the deployed release
`release/toronto-candidate` @ `0064bcb`, clean venv built from its own `backend/requirements.txt` (+pytest): **179 passed, 0 failed, 0 skipped** in 2121 s (machine heavily loaded by other jobs), 1 warning (Starlette TestClient/httpx deprecation, pre-existing). Native SUMO runs in these tests.

## 40-global-connection trade-off
Raised from 25 and per-IP from 4 to 12. Cost: WebSockets are cheap here (one shared simulation; ~78 kB/s per viewer at heavy load; a few KB of memory each), so resource risk is mainly bandwidth and the 1-worker event loop fan-out — measured fine at 6 viewers. Security: one IP can now hold 12 of the 40 slots, so **4 IPs can fill the cap and lock everyone else out** (previously 7 IPs for 25). The cap blocks no run-level abuse beyond viewing; run starts/writes keep their own per-IP limits. Behind a shared NAT (office/campus) 12 is still a per-network ceiling. Reasonable for team testing; lower it (and/or add the access token) for a public demo.

## Changes made to the server in this verification (all additive)
`/etc/systemd/system/flowstate.service.d/ws-limits.conf` (earlier session); in `/opt/flowstate/releases/`: `toronto-0064bcb.tgz`, `requirements-0064bcb.txt`, `Caddyfile-0064bcb`, `ws-limits.conf-0064bcb`, `SHA256SUMS`. No code deployed, no config of the running service changed, no restarts.
