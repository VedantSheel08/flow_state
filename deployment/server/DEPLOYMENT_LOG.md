# FlowState production deployment log

Running record of what Claude (Agent 4) did to the Vultr production backend. Newest state first; append new entries at the bottom of "History".
Related docs in this folder: `README.md` (runbook), `PRODUCTION_VERIFICATION.md`, `INTEGRATION_CHECKLIST.md`, `checks/` (repeatable test scripts).

## Current state (as of 2026-10-18)
| Item | Value |
|---|---|
| Live backend release | `602a72f` = branch `release/toronto-main-sync` (pushed) = Toronto release `0064bcb` + `origin/main` `9b7894a` |
| Frontend | Vercel, auto-deploys from `main` (`9b7894a` frontend verified live) |
| Host | `137.220.52.142` (Vultr, Ubuntu 24.04), SSH key `~/.ssh/flowstate_vultr` |
| Limits | 12 WebSocket per IP, 40 total (systemd drop-in `ws-limits.conf`); 30 writes/min and 6 run starts/min per IP; runs capped at 15 min |
| Rollback snapshots | `/opt/flowstate/releases/`: `pre-602a72f-20261018-045128.tgz` (= `0064bcb`, state just before the latest deploy), `toronto-0064bcb.tgz`, `pre-0064bcb-20261017-203458.tgz` (`fec21e6`), `known-good.tgz` (`811b2fd`) |

**Known problem in the live release:** main's regenerated network files (`data/networks/district*.net.xml`) make ambulances collide with other vehicles at some junctions in the seeded ambulance scenario (seed 7). Test `tests/test_scenarios_sumo.py::test_drivers_make_way_and_resume_their_trips` fails on `602a72f` (passes on `0064bcb`; passes on `602a72f` with the old net files swapped in). Deployed anyway on the owner's explicit instruction. Not fixed: the network/signal logic is the teammates' area.

## History
### 2026-10-17 — Resumed after restart; fixed WebSocket limit
- Found worktrees: `../release-candidate` (`release/toronto-candidate` @ `0064bcb`), `../deploy-production` (`deploy/production-backend` @ `fec21e6`). Server was healthy on `0064bcb`; WebSocket limit was still the code default of 4 per IP.
- Server change: backed up the unit file to `/opt/flowstate/releases/flowstate.service.bak-<ts>`, added drop-in `/etc/systemd/system/flowstate.service.d/ws-limits.conf` (`FLOWSTATE_MAX_WS_PER_IP=12`, `FLOWSTATE_MAX_WS_CLIENTS=40`), `daemon-reload`, restarted `flowstate`. Tested 6 simultaneous WebSockets: all connected.
- Repo: commit `6037379` documents the drop-in (`deployment/server/ws-limits.conf`, README).

### 2026-10-18 — Full production verification
- Read-only checks: version, services, ufw, ports, journal. Browser (Playwright) test of flowstate.nyc: run, frames, pause/resume, speed, 6 tabs, forced WebSocket drop/reconnect, scenario switch, end run — all passed after fixing three bugs in my own first test script. Load test (severe preset, 6 clients). Security checks (WS cap vs spoofed `X-Forwarded-For`, origin, CORS, ports, HTTP→HTTPS, rate limit, duration cap). Full backend suite on `0064bcb` in a clean venv: 179 passed. Details: `PRODUCTION_VERIFICATION.md`.
- Server changes (additive only): created `toronto-0064bcb.tgz`, `requirements-0064bcb.txt`, `Caddyfile-0064bcb`, `ws-limits.conf-0064bcb`, `SHA256SUMS` in `/opt/flowstate/releases/`; integrity-tested all snapshots by extracting to `/tmp` and importing the app.
- Integration analysis of the feature branches (merge conflicts, dependencies, artifacts, missing protections): `INTEGRATION_CHECKLIST.md`.
- Pushed `deploy/production-backend` at `6859a5e` (runbook, verification report, checklist, `checks/`). Secrets scan of the diff: clean.

### 2026-10-18 — Deployed teammate's main (`9b7894a`)
- Fetched `origin/main` = `9b7894a` (10 commits since merge base `86480ec`; main does not contain `0064bcb`). Changes: backend network generation (`city.py`, `toronto.py`: real OSM buildings/parks), regenerated network data, new OSM context file, one test file, 16 frontend files.
- Created branch `release/toronto-main-sync` (worktree `D:\flowstate-migration\main-sync`) from `0064bcb`, merged `origin/main` (clean) → `602a72f`. Production protections and the deadlock fix verified still present.
- Verified: frontend 43 tests pass + build; app imports; topology unchanged (77 junctions, 164 edges, same signals/links); 24 of 164 edges changed length (max 35 m). Backend suite: 180 passed, 1 failed (the ambulance test above) — reported to the owner, who said to deploy anyway.
- Deployed with `deployment/server/deploy.sh 602a72f` (run from the `main-sync` worktree). Snapshot `pre-602a72f-20261018-045128.tgz`; health checks passed. Pushed branch `release/toronto-main-sync`.
- Post-deploy checks: `/api/health`, new network data served (635 buildings / 797 trees / 233 features), real run streams at 4.0 frames/s, forced WS drop reconnects in 2 s, scenario switch + end run, security checks, screenshot of the live Network view shows OSM buildings and parks. No restart of `caddy`; service restarted once by `deploy.sh`.
- Not changed: frontend (Vercel already had it), Caddy, firewall, venv, any code.

### 2026-10-18 — "Why does my friend's localhost look different?" (no change made)
- Compared live vs main: Vercel serves main's latest frontend (bundle 21 bytes larger, consistent with the baked-in API URL); live network data equals a local run of main; regenerating the network from main's code gives identical geometry (only line endings/timestamp differ); backend differs from main only by our protections + deadlock fix.
- Conclusion: no code/data cause found; likely his local checkout (unpushed/uncommitted/different branch), browser cache, or view state. Asked for a screenshot and his `git log -1`. Open.

## Local scratch (not in git)
`D:\flowstate-migration\scratch\e2e\` (test scripts, screenshots, test outputs, server samples), `D:\flowstate-migration\scratch\venv-rc\` (clean venv from the release `requirements.txt`), worktrees `release-candidate`, `deploy-production`, `main-sync`.

## Standard procedures
- Deploy: from a clean checkout of the approved ref, `deployment/server/deploy.sh <sha>` (auto-snapshot, auto-restore on failure). Then run `checks/security.py`, `checks/e2e-live.mjs`.
- Roll back: runbook README → *Rollback* (restore a snapshot from `/opt/flowstate/releases/`).
- Change limits: edit `ws-limits.conf` drop-in, `systemctl daemon-reload && systemctl restart flowstate` (ends the live run).
