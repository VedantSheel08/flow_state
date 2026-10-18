# Production backend runbook (Vultr, Toronto)

| Item | Value |
|---|---|
| Host | `137.220.52.142` (Ubuntu 24.04, 8 vCPU / 16 GB), SSH as root with the owner's key |
| Public API | `https://api.flowstate.nyc` (Caddy, Let's Encrypt) → `127.0.0.1:8000` |
| Frontend | `https://flowstate.nyc` (Vercel, `main`); Production env `VITE_FLOWSTATE_API_URL=https://api.flowstate.nyc` (build-time, redeploy after changing) |
| Service | `flowstate.service` (user `flowstate`, 1 uvicorn worker, `MemoryMax=10G`, `CPUQuota=600%`) |
| Code / venv | `/opt/flowstate/app` (contains `VERSION` = git SHA) / `/opt/flowstate/venv` |
| Runs | `/var/lib/flowstate/runs` |
| Snapshots | `/opt/flowstate/releases/` — see *Rollback* (`toronto-0064bcb.tgz` = current Toronto release; `SHA256SUMS`) |
| Live release | `0064bcb` (`release/toronto-candidate`), verified 2026-10-18; deployed from this branch's `deploy.sh` |
| Firewall (ufw) | 22, 80, 443 only. **Never open 8000.** |

`flowstate.service`, `ws-limits.conf`, `Caddyfile` and `requirements.known-good.txt` in this folder are copies of what is
on the server (no secrets). Server config is not changed by `deploy.sh`.
`ws-limits.conf` is the systemd drop-in `/etc/systemd/system/flowstate.service.d/ws-limits.conf`; to change limits edit it,
then `systemctl daemon-reload && systemctl restart flowstate` (restart ends the live run). `systemctl show flowstate -p Environment` shows the effective values.
The app is `uvicorn --workers 1`: rate-limit and WS counters are in-process, so never raise workers without moving them to a shared store.

Access limits (in `backend/flowstate/api/app.py`, env-tunable in the unit file): 30 writes/min and 6 run
starts/min per client IP (429), 12 WebSocket viewers per IP and 40 total (code default is 4/25; raised on the server by the drop-in `ws-limits.conf` -> `/etc/systemd/system/flowstate.service.d/`, so multiple tabs/teammates behind one IP work), runs capped at 15 min,
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
Snapshots in `/opt/flowstate/releases/` (each extracts to `app/`; all verified to extract and `import flowstate.api.app` with the production venv, 2026-10-18; checksums in `SHA256SUMS`):

| File | Contains | Use |
|---|---|---|
| `toronto-0064bcb.tgz` | **current live Toronto release** `0064bcb` | restore after a failed future deploy |
| `pre-0064bcb-20261017-203458.tgz` | `fec21e6` (pre-Toronto district network) | older fallback |
| `known-good.tgz` / `known-good-811b2fd.tgz` | `811b2fd` (first verified release, pre-Toronto) | last resort |
| `requirements-0064bcb.txt`, `Caddyfile-0064bcb`, `ws-limits.conf-0064bcb` | pip freeze / Caddy / drop-in matching the live release | rebuild a venv or config |

`deploy.sh` restores the snapshot it just took automatically if the health/scenario checks fail. To roll back by hand to the Toronto release:
```sh
ssh root@137.220.52.142 'systemctl stop flowstate && rm -rf /opt/flowstate/app   && tar xzf /opt/flowstate/releases/toronto-0064bcb.tgz -C /opt/flowstate   && chown -R flowstate: /opt/flowstate/app && systemctl start flowstate   && sleep 8 && curl -s localhost:8000/api/health && cat /opt/flowstate/app/VERSION'
```
(For older fallbacks substitute the snapshot name. Code rollback does not undo pip installs: if a deploy installed new packages the old code still runs because installs are additive; if the venv itself broke use `pip install -r requirements-0064bcb.txt`.)
Take a new `toronto-<sha>.tgz`-style snapshot of whatever is live **before** each deploy of a new release; `deploy.sh` already writes `pre-<sha>-<timestamp>.tgz`.
Frontend rollback: Vercel dashboard → Deployments → promote the previous deployment (or `vercel rollback`).
If the whole API is down, `systemctl status flowstate caddy`, `journalctl -u flowstate -u caddy -n 100`.

## Verifying production (repeatable)
`checks/` holds the scripts used for the 2026-10-18 verification. They run from any machine and need no server access:
`checks/security.py` (no simulation started), `checks/e2e-live.mjs` (Playwright; drives the **shared** live run), `checks/load.py` (starts and stops the shared run). Results: `PRODUCTION_VERIFICATION.md`.
