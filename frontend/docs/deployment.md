# Deploying the frontend on Vercel

`vercel.json` at the repository root defines one Vercel service, `frontend`: the Vite build in `frontend/`. It is served
for every path, with a service-scoped catch-all to `index.html`. There are no bindings, because no server-side code
calls another service.

## Why the backend is not a Vercel service

The FlowState API keeps one live SUMO simulation in process memory and streams it to the browser. A background thread
steps SUMO between requests, and `POST /api/runs`, run controls, the inspector and the `/ws` stream must all reach that
same process. That conflicts with Vercel Functions in several ways:

* There is no instance affinity, and instances scale to zero.
* WebSockets close at the function's max duration, while runs last 5–60 simulated minutes.
* Only `/tmp` is writable, but the API writes to `data/work` and `data/runs`.
* The API reads `data/networks`, which sits outside `backend/`.
* The SUMO packages are about 700 MB installed.

The backend therefore runs on a long-lived host (VM or container service), unchanged.

## Pointing the frontend at the backend

Set this Vercel environment variable for the build:

```
VITE_FLOWSTATE_API_URL=https://<backend public origin>
```

* **Request URLs.** The frontend then calls `https://<origin>/api/...` and opens `wss://<origin>/ws`
  (`src/lib/endpoints.ts`).
* **When unset.** Everything stays same-origin, exactly as in local development, where the Vite server proxies `/api`
  and `/ws`.
* **Build-time only.** The value is compiled into the bundle, so redeploy after changing it.

Requirements on the backend host:

* **HTTPS.** An `https://` page cannot open `ws://` sockets or call `http://` APIs.
* **CORS.** It already sends `Access-Control-Allow-Origin: *`. Tighten that to the Vercel domains if the API should not
  be open to other sites.
* **One process.** Run a single process, not several workers, because the live run is in memory.

Preview deployments all share the one backend URL. Two people using two previews at the same time control the same
live run.
