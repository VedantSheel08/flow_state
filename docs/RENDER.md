# Hosting FlowState on Render

Status: **prepared, not deployed.** No Render service exists yet.

## Layout

One Docker web service (`render.yaml` at the repo root, image from `deployment/render/Dockerfile`) serves everything
from a single origin:

* the built frontend (`frontend/dist`, served by FastAPI when `FLOWSTATE_STATIC_DIR` is set),
* the REST API under `/api`,
* the live stream at `/ws`.

Because the frontend and API share an origin, `VITE_FLOWSTATE_API_URL` stays unset and CORS is not involved.

The live run is held in process memory, so the service runs **one instance with one uvicorn worker**. Do not enable
autoscaling or add instances. A redeploy or restart ends any in-flight run. Saved runs live on a 1 GB persistent disk
at `/var/lib/flowstate/runs` and survive redeploys.

| Setting | Value |
|---|---|
| Plan | `pro` (4 GB / 2 CPU). `standard` (2 GB / 1 CPU) is the smallest sensible size. Free/Starter (512 MB) is too small for SUMO. |
| Region | `virginia` |
| Branch | `colton-updates`, `autoDeploy: false` |
| Health check | `/api/health` |
| URL | `https://flowstate-<suffix>.onrender.com` until a domain is bought |

## Going live

1. In the Render dashboard, choose **New → Blueprint**, connect `VedantSheel08/flow_state`, and pick the
   `colton-updates` branch. Render reads `render.yaml`. Review the plan and disk cost, then **Apply**.
2. Wait for the first build. The SUMO wheels are large, so expect several minutes.
3. Check the deployment:
   * `curl https://<service>.onrender.com/api/health` returns the SUMO version.
   * Open the site, start a run, and confirm vehicles move and the clock advances.
4. Later deploys: use **Manual Deploy** in the dashboard, or set `autoDeploy: true` to deploy on every push.

To run the image locally (needs Docker):
`docker build -f deployment/render/Dockerfile -t flowstate . && docker run -p 8000:8000 flowstate`

## Custom domain (later)

In the service's **Settings → Custom Domains**, add the domain, then create the CNAME record (or the A record for an
apex domain) that Render shows. Render issues the TLS certificate. Same-origin hosting means no code changes.

## Known gaps before a public launch

* CORS is open (`*`), and there are no rate limits or run-duration caps. Anyone with the URL controls the one shared
  live run. Per-IP limits and an origin allowlist exist on the unmerged `deploy/production-backend` branch
  (`backend/flowstate/api/app.py`) and should be ported first.
* The Docker image has not been built yet (no local Docker). The first Render build is its first real test.
