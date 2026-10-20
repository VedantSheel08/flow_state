# Hosting FlowState: Render (API) + Vercel (frontend)

Status: **Render backend and Vercel frontend are configured in the repository.** The dashboard steps below are
still needed, because nothing on Render or Vercel is changed by a commit. The old Vultr server is gone and is no
longer referenced by any active configuration (see `docs/DEPLOYMENT.md` for the historical notes).

## Layout

| Piece | Where | What it serves |
|---|---|---|
| API + SUMO | Render, free web service, Docker (`Dockerfile` at the repo root, `render.yaml`) | REST under `/api`, live stream at `/ws`, and a copy of the built frontend |
| Frontend | Vercel, project root `frontend/` (`frontend/vercel.json`) | The React dashboard; calls the Render API through `VITE_FLOWSTATE_API_URL` |

The Render image also builds and serves the frontend (same origin, `FLOWSTATE_STATIC_DIR`), so `https://<service>.onrender.com`
works on its own for testing. The public site uses Vercel.

## Free tier limits (and what they mean here)

Render's free web services have, as documented for this plan (verify on Render's pricing page before relying on them):

* **512 MB RAM and a fraction of one CPU.** The SUMO engine is the constraint, see the measurements below.
* **No persistent disk.** Saved runs (`/api/runs`) and the replay cache live on the container filesystem and reset
  on every redeploy or restart. `render.yaml` has no disk.
* **Spins down after about 15 minutes without traffic.** The first request after that waits for a cold start (often
  30 to 60 seconds). The frontend reconnects its WebSocket automatically, and the Results page falls back to
  recorded runs (below) when the backend cannot answer.
* **A restart or spin-down ends any live run.** The live run is held in process memory, so the service runs **one
  instance with one uvicorn worker** (`numInstances: 1`, `--workers 1`). Do not scale out.

### Measurements (development machine, full CPU, not the free instance)

These were taken with the same code and `FLOWSTATE_BACKEND=traci`, on the `district` network:

| Workload | Result |
|---|---|
| Idle server (FastAPI + Python) | about 81 MB RSS |
| Live `rush_hour` run, 5 simulated minutes, speed 16 | peak about 131 MB (server plus `sumo` child); finished |
| `POST /api/replay` (two headless 5-minute runs, the Results page's "simulate now") | 73 s wall time, peak about 372 MB |

The live run fits comfortably in 512 MB. The replay is the risky part: it is slow, its memory peak is close to the
limit, and on 0.1 CPU it will take many times longer than the 73 s measured here. Expect it to fail or time out on
the free plan. The frontend then shows the **recorded** pair (the prebuilt replays in `frontend/public/flowstate-demo/`),
so the Results page still works without a backend.

Estimated, not measured on the free instance: at 0.1 CPU a 5-minute live run would advance slower than real time at
speed 16. The engine runs as fast as the CPU allows, so the clock simply falls behind the requested speed.

## Dashboard steps (Render)

1. Open **New > Blueprint**, connect `VedantSheel08/flow_state`, and pick `main`. Render reads `render.yaml`.
   If a `flowstate` service already exists from the earlier attempt, open it instead and check these settings:
   * **Branch:** `main`
   * **Root directory:** blank (repository root)
   * **Dockerfile path:** `./Dockerfile` (the build failed earlier with `open Dockerfile: no such file`; that was
     the default path before the Dockerfile moved to the root)
   * **Instance type:** Free
   * **Persistent disk:** none (remove it if one is attached, since disks are not free)
   * **Health check path:** `/api/health`
2. Click **Apply** or **Manual Deploy > Deploy latest commit**. The first build downloads the SUMO wheels, so expect a
   few minutes.
3. Check the deployment (replace the host with the service's URL):
   * `curl https://flowstate.onrender.com/api/health` returns JSON with `"sumo"` set to the SUMO version.
   * `curl https://flowstate.onrender.com/api/scenarios` returns the scenario list.

## Dashboard steps (Vercel)

Project settings for the frontend (the same as `docs/DEPLOYMENT.md` describes, plus the API URL):

| Setting | Value |
|---|---|
| Root directory | `frontend` (so `frontend/vercel.json` applies; the root `vercel.json` is only read when the root is the repo) |
| Framework | Vite |
| Install / build / output | `npm ci` / `npm run build` / `dist` |
| Node.js | 22.x |
| Environment variable (Production, and Preview if you want previews to hit the API) | `VITE_FLOWSTATE_API_URL` = `https://flowstate.onrender.com` (no trailing slash; use the actual Render URL) |

`VITE_FLOWSTATE_API_URL` is read at **build time** and is public, so **redeploy** after changing it. The API URL is
used for both REST (`/api/...`) and the WebSocket (`wss://<host>/ws`), derived from the same value. The backend
allows any origin (CORS `*`), so no backend change is needed for the Vercel origin.

Domain: `flowstate.nyc` and `www.flowstate.nyc` already resolve to Vercel (checked with DNS lookups). Keep those DNS
records as they are. The frontend is what visitors open; the API hostname is only used by the browser.

## Verifying a deployment

1. `https://<render-host>/api/health`, `/api/scenarios` and `/api/networks/district` return JSON.
2. Open `https://flowstate.nyc`. The connection indicator should move to connected within about a minute (longer after
   a spin-down).
3. Start a run: vehicles move and the clock advances. Pause, resume and stop should respond.
4. Results page: "simulate now" either runs or falls back to the recorded pair. Either way the page shows data.

## Local checks

```sh
# Docker (needs Docker installed):
docker build -t flowstate .
docker run -p 8000:8000 -e PORT=8000 flowstate
# then open http://localhost:8000

# Without Docker, the same server (PYTHONPATH and FLOWSTATE_STATIC_DIR as in the Dockerfile):
PYTHONPATH=backend FLOWSTATE_BACKEND=traci FLOWSTATE_RUNS_DIR=data/runs FLOWSTATE_STATIC_DIR=frontend/dist \
  python -m uvicorn flowstate.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

## Custom domain (later)

Add the domain under the service's **Settings > Custom Domains**, then create the DNS record Render shows. Same-origin
hosting means no code change is needed for the Render-served page. For the Vercel frontend, the existing records stay.

## Known gaps before a public launch

* **Shared live run, no authentication.** Anyone with the API URL can start, stop or change the one shared run. CORS
  is open and there are no rate limits or run-duration caps. Per-IP limits and an origin allowlist exist on the unmerged
  `deploy/production-backend` branch (`backend/flowstate/api/app.py`) and should be ported first.
* **Free-tier capacity.** One live run at a time, slow on 0.1 CPU, and cold starts. A paid instance would fix this
  but is not used here (no paid plans were purchased or enabled).
* **Replays are not reliable on the free instance.** The "simulate now" button depends on the backend and falls back to
  recorded runs.
* **Runs and replay cache are lost on restart.** There is no disk on the free plan.
* **Dependencies are lower bounds** (`backend/requirements.txt`). The Linux wheels for Python 3.12 resolve (checked with
  `pip download --platform manylinux2014_x86_64`), but the image has not been built and run under Docker in this
  environment, so the first Render build is the real test.
