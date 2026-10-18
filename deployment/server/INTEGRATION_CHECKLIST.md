# Integration-readiness checklist (analysis of 2026-10-18; nothing below has been deployed)

Baseline = live release `0064bcb` (`release/toronto-candidate`). Conflicts were computed with `git merge-tree` (no worktree touched).
Re-run before releasing: `git merge-tree --write-tree --name-only release/toronto-candidate <branch>`.

## Branch status against the release candidate

| Branch | Ahead | Merge into `0064bcb` | New Python deps | Router / app.py | Artifacts | Notes |
|---|---|---|---|---|---|---|
| `task/rl-signal-control` (`109108e`) | 1 | clean | none in `requirements.txt` (code imports `torch`, `stable_baselines3`, `gymnasium`, `numpy`, `scipy`) | `+include_router(rl_router)` | ~10 MB `artifacts/rl/**` (PPO zips) | already contained in `ml-intelligence` |
| `task/ml-intelligence` (`366b925`) | 6 | **conflicts: `backend/flowstate/api/app.py`, `README.md`** | none declared; additionally `sklearn` (forecaster), `httpx` | `rl_router` + intelligence router | `artifacts/lab/forecaster.pkl` (4.3 MB pickle) + RL zips (10.3 MB total) | **worktree `../ml-intelligence` has uncommitted edits** under `backend/flowstate/lab/` — owner must commit before release |
| `task/vision` (`386fe37`) | 1 | **conflicts: `app.py`** | `opencv-python-headless`, `numpy`, `python-multipart` | `+include_router(vision_router)` | `data/vision-samples/short-intersection.mp4` | **app fails to import without `cv2`** (tested in a clean venv): install deps *before* deploy |
| `task/integration-design` (`e6d7912`) | 4 | **conflicts: `app.py` + 6 frontend files** | same as vision | vision router | same mp4 | overlaps `dashboard-redesign` in the same frontend files |
| `task/dashboard-redesign` (`68a6fee`) | 1 | **conflicts: 6 frontend files** | – | – | – | frontend only |
| `task/city-redesign` (`865bba8`) | 2 | clean | – | – | – | frontend only |
| `origin/task/traffic-realism`, `origin/task/integrations` | 5 / 8 | **no merge base: unrelated history** (rooted at `0de23f6`, not `main`) | – | – | – | cannot be merged normally; owners must port changes onto the release lineage (cherry-pick/patch per file) or decide an explicit `--allow-unrelated-histories` strategy |
| `origin/colton-updates`, `origin/krishna-updates` | 1 / 6 | clean | – | – | – | frontend only (Vercel) |

Import check in the clean release venv (no torch/sb3/sklearn/cv2): `ml-intelligence` and `rl-signal-control` **import fine** (heavy imports are lazy, so the API starts; trained-policy features need the packages or fall back to the heuristic); `vision` **fails** (`ModuleNotFoundError: cv2`).

## app.py conflict resolution
`0064bcb` appends a single-origin static-file block at the **end** of `app.py` (`FLOWSTATE_STATIC_DIR`, catch-all `/{path:path}`; off unless the env var is set).
Feature branches add `from ..<pkg>.api import router` + `app.include_router(...)` near the `GZipMiddleware` line.
Resolution: keep **both**, and keep every `include_router` **before** the static catch-all block (otherwise the catch-all shadows the routes). Do not set `FLOWSTATE_STATIC_DIR` on Vultr (the frontend is on Vercel).

## Dependencies and artifacts (server impact)
- Install into the production venv *first* (additive, does not affect the running service), pinned to what was tested: `ssh root@HOST '/opt/flowstate/venv/bin/pip install -r /dev/stdin' < backend/requirements.txt`; then `pip freeze > requirements-<sha>.txt` into `releases/`.
- `torch` + `stable_baselines3` are large (CPU wheel several hundred MB, import adds RAM/seconds). Only install if RL *policy inference* is wanted live; the branch documents a verified heuristic default. Decide explicitly — current limits are `MemoryMax=10G`, `CPUQuota=600%`.
- Artifacts are resolved relative to the code root (`parents[3]/artifacts`), so `deploy.sh` (git archive of the ref) ships them. `forecaster.pkl` is a **pickle**: only load from a reviewed commit, and match the `scikit-learn`/`numpy` versions used for training (record them in `requirements-<sha>.txt`).
- Gemini: key from `GEMINI_API_KEY`/`GOOGLE_API_KEY` env only. Put it in a root-owned `0600` file referenced by a systemd `EnvironmentFile=` drop-in; **never commit it** (branch scan found no keys). Server needs outbound HTTPS. The code has its own rate limit/cache/breakers/offline fallback.

## Protections the new routes do NOT get automatically (fix before exposing)
1. The per-IP **start limit** (6/min) applies only to `/api/runs` and `/api/compare` (`app.py`: `starts = path in (...)`). RL comparison and Gemini POSTs get only the generic 30 writes/min. Add them to `starts` (or give them their own bucket).
2. **Concurrency:** only `/api/compare` has the one-job guard (409). RL comparison jobs (`rl/service.py` `JobManager`, up to 60 episodes, runs in a child process) — confirm one-at-a-time and that they cannot starve the live SUMO run (1 worker, CPUQuota 600%).
3. **Uploads (vision):** no body-size limit in the app. Add `request_body { max_size 25MB }` for the route in Caddy and an app-side check; upload parsing with OpenCV on untrusted files is a resource/attack surface — consider gating behind the access token (`FLOWSTATE_ACCESS_TOKEN`, supported by the middleware).
4. WebSocket cap: now 12/IP, 40 total (see README). Keep `--workers 1`; counters are in-process.

## Release procedure (when approved)
1. Owners commit and push; freeze exact SHAs. Merge into a **new integration branch** off `release/toronto-candidate` (not `main`): `rl/ml` → `vision` → frontend branches; resolve `app.py` as above. Decide the traffic-realism/integrations strategy first.
2. Locally: full backend suite (`PYTHONPATH=backend pytest tests -q`), `npm ci && npm test && npm run build` in `frontend/`, and an import check in a **clean venv built only from the integration `requirements.txt`**.
3. Server: `toronto-0064bcb.tgz` already exists in `/opt/flowstate/releases/`; `deploy.sh` adds its own `pre-<sha>` snapshot. Install new deps (pip, additive). Add secrets/env drop-ins (Gemini, any new limits) and Caddy body limit if vision is included; `systemctl daemon-reload`.
4. `deployment/server/deploy.sh <integration-sha>` (auto-rollback on failed health/scenario checks).
5. Verify: `checks/security.py`, `checks/e2e-live.mjs`, `checks/load.py`; new endpoints; `journalctl -u flowstate -n 100`.
6. Frontend: Vercel production from `main` after the backend is verified (the frontend must tolerate missing new endpoints during the gap); confirm `VITE_FLOWSTATE_API_URL` is unchanged.
7. Rollback if anything fails: README → *Rollback* (`toronto-0064bcb.tgz`), then Vercel promote-previous.
