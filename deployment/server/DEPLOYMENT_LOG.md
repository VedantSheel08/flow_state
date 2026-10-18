# FlowState production deployment log

Running record of what Claude (Agent 4) did to the Vultr production backend. Newest state first; append new entries at the bottom of "History".
Related docs in this folder: `README.md` (runbook), `PRODUCTION_VERIFICATION.md`, `INTEGRATION_CHECKLIST.md`, `checks/` (repeatable test scripts).

## Current state (as of 2026-10-18)
| Item | Value |
|---|---|
| Live backend release | `d209c7d` = branch `release/toronto-main-sync` (pushed) = Toronto release `0064bcb` + `origin/main` `1cdf1da` |
| Frontend | Vercel, auto-deploys from `main` (`1cdf1da` UI verified live: grey glass panels, new Results tab) |
| Host | `137.220.52.142` (Vultr, Ubuntu 24.04), SSH key `~/.ssh/flowstate_vultr` |
| Limits | 12 WebSocket per IP, 40 total (systemd drop-in `ws-limits.conf`); 30 writes/min and 6 run starts/min per IP; runs capped at 15 min |
| Rollback snapshots | `/opt/flowstate/releases/`: `pre-d209c7d-20261018-065342.tgz` (= `602a72f`, just before the latest deploy), `pre-602a72f-20261018-045128.tgz` (= `0064bcb`), `toronto-0064bcb.tgz`, `pre-0064bcb-20261017-203458.tgz` (`fec21e6`), `known-good.tgz` (`811b2fd`) |

**Known behavior change in the live release (teammate's, deliberate):** main commit `e564c67` removed emergency-vehicle signal priority and overtaking aids (pre-emption, `sim/emergency.py`, bluelight device, drivers yielding to sirens); clearing the way is meant to become the signal controller's job. Consequences: test `tests/test_engine.py::test_accident_physically_blocks_lanes_and_clears` fails (it also fails on pure `origin/main`; the commit message lists it as known — responders arrive after the scene's clear time, so accident scenes clear at the hard-clear cap), and `tests/test_signals.py` lost 123 lines of tests. The earlier ambulance-collision test failure from `602a72f` no longer occurs (that feature is gone). Our own additions kept: production protections and the junction-deadlock clearing (`blocker_clears`).

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

### 2026-10-18 — Deployed teammate's second main update (`1cdf1da`)
- `origin/main` moved to `1cdf1da` (4 commits: Results tab revamp, simulation tab polish, removal of emergency priority/overtaking aids, wider comparison chart; 29 files: backend `sim/engine.py`, `sim/signals.py`, `sim/vtypes.py`, 17 frontend files, 3 test files). Main still lacks `0064bcb`.
- Merged into `release/toronto-main-sync` → `d209c7d`. One conflict (docstring in `scenarios/components.py` against our deadlock-fix docstring); resolved by keeping our `blocker_clears` bullet with main's wording. Production protections verified present.
- Tests: frontend 43 pass + build; app imports (11 scenarios); backend 175 passed, 1 failed (`test_accident_physically_blocks_lanes_and_clears`, same failure on pure `origin/main`, passes on `602a72f`). Deployed on the owner's instruction after being told.
- First `deploy.sh` attempt failed at the `scp` step (SSH connection refused for a few seconds; nothing on the server had changed); retried successfully. Snapshot `pre-d209c7d-20261018-065342.tgz`. Branch pushed (`602a72f..d209c7d`).
- Post-deploy: health OK, version `d209c7d`, WS limits 12/40 intact, no tracebacks; run first frame 7.7 s, 4.0 frames/s, forced WS drop reconnects in 2.0 s, scenario switch works; security checks pass; screenshots confirm the new UI live. Note: a teammate started runs during my first test (shared run), and my own repeated tests hit my IP's 6-starts/min limit (429) — expected behavior; re-measured after the window cleared.

### 2026-10-18 — "Why does my friend's localhost look different?" (no change made)
- Compared live vs main: Vercel serves main's latest frontend (bundle 21 bytes larger, consistent with the baked-in API URL); live network data equals a local run of main; regenerating the network from main's code gives identical geometry (only line endings/timestamp differ); backend differs from main only by our protections + deadlock fix.
- Conclusion: no code/data cause found; likely his local checkout (unpushed/uncommitted/different branch), browser cache, or view state. Asked for a screenshot and his `git log -1`. Open.

## Local scratch (not in git)
`D:\flowstate-migration\scratch\e2e\` (test scripts, screenshots, test outputs, server samples), `D:\flowstate-migration\scratch\venv-rc\` (clean venv from the release `requirements.txt`), worktrees `release-candidate`, `deploy-production`, `main-sync`.

## Standard procedures
- Deploy: from a clean checkout of the approved ref, `deployment/server/deploy.sh <sha>` (auto-snapshot, auto-restore on failure). Then run `checks/security.py`, `checks/e2e-live.mjs`.
- Roll back: runbook README → *Rollback* (restore a snapshot from `/opt/flowstate/releases/`).
- Change limits: edit `ws-limits.conf` drop-in, `systemctl daemon-reload && systemctl restart flowstate` (ends the live run).
