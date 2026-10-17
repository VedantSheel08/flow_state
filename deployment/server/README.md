# Production backend runbook (Vultr, Toronto)

| Item | Value |
|---|---|
| Host | `137.220.52.142` (Ubuntu 24.04, 8 vCPU / 16 GB), SSH as root with the owner's key |
| Public API | `https://api.flowstate.nyc` (Caddy, Let's Encrypt) → `127.0.0.1:8000` |
| Frontend | `https://flowstate.nyc` (Vercel, `main`); Production env `VITE_FLOWSTATE_API_URL=https://api.flowstate.nyc` (build-time, redeploy after changing) |
| Service | `flowstate.service` (user `flowstate`, 1 uvicorn worker, `MemoryMax=10G`, `CPUQuota=600%`) |
| Code / venv | `/opt/flowstate/app` (contains `VERSION` = git SHA) / `/opt/flowstate/venv` |
| Runs | `/var/lib/flowstate/runs` |
| Snapshots | `/opt/flowstate/releases/` (`known-good.tgz` = first verified release, `811b2fd`) |
| Firewall (ufw) | 22, 80, 443 only. **Never open 8000.** |

`flowstate.service`, `Caddyfile` and `requirements.known-good.txt` in this folder are copies of what is
on the server (no secrets). Server config is not changed by `deploy.sh`.

Access limits (in `backend/flowstate/api/app.py`, env-tunable in the unit file): 30 writes/min and 6 run
starts/min per client IP (429), 4 WebSocket viewers per IP and 25 total, runs capped at 15 min,
one comparison job at a time, WebSocket/CORS origin allowlist. Origin checks are not authentication;
the limits are what bound abuse. Any visitor can still control the one shared live run.

## Updating safely
1. Pick an **approved commit** (not a task branch tip by default). Locally run `pytest tests/test_api.py`.
2. If `backend/requirements.txt` changed (e.g. the vision branch adds `opencv-python-headless`, `numpy`,
   `python-multipart`), install first, pinned to what you tested:
   `ssh root@HOST '/opt/flowstate/venv/bin/pip install -r /dev/stdin' < backend/requirements.txt`
   (additive installs do not affect the running service until restart; check `ldd`/import for new native libs).
3. `deployment/server/deploy.sh <ref>` — snapshots the live app, swaps in the new code, restarts,
   checks `/api/health` (SUMO version) and `/api/scenarios`, and **restores the snapshot automatically** on failure.
4. Verify publicly: `curl https://api.flowstate.nyc/api/health`, then start a run from https://flowstate.nyc
   (Generate & Run) and confirm vehicles move and the clock advances. `journalctl -u flowstate -n 50`.
5. Frontend-only changes deploy through Vercel and need no server action.

Merge notes: `task/integration-design` and `task/vision` both add `from ..vision.api import router` and
`app.include_router(vision_router)` right after `app.add_middleware(GZipMiddleware…)` in `app.py`; merging with this
branch conflicts textually there — keep **both** blocks. New POST routes (e.g. vision uploads) automatically fall under
the per-IP write rate limit but have **no body-size limit**; add one (and a Caddy `request_body max_size`) before
exposing uploads publicly.

## Rollback
Fastest (known-good `811b2fd`, verified end-to-end on the public site):
```sh
ssh root@137.220.52.142 'systemctl stop flowstate && rm -rf /opt/flowstate/app \
  && tar xzf /opt/flowstate/releases/known-good.tgz -C /opt/flowstate \
  && chown -R flowstate: /opt/flowstate/app && systemctl start flowstate \
  && sleep 6 && curl -s localhost:8000/api/health'
```
To return to the version before a specific deploy use the `pre-<sha>-<timestamp>.tgz` printed by `deploy.sh` in the same way.
Dependencies: `requirements.known-good.txt` lists the exact working versions
(`/opt/flowstate/venv/bin/pip install -r …` if a later install broke the venv).
Frontend rollback: Vercel dashboard → Deployments → promote the previous deployment (or `vercel rollback`).
If the whole API is down, `systemctl status flowstate caddy`, `journalctl -u flowstate -u caddy -n 100`.
