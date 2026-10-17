# FlowState deployment preparation — no deployment performed

## Vercel frontend

Import `VedantSheel08/flowstate` only after publication approval. Project settings:

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Framework | Vite |
| Install command | `npm ci` |
| Build command | `npm run build` |
| Output directory | `dist` |
| Node.js | 22.x (verification uses 22.14.0) |

`frontend/vercel.json` records the framework/build settings on the setup branch.
Root directory is a Vercel project setting, relative to the repository root.
No Vercel project, deployment, production branch change, or GitHub integration
has been created. Keep production on `main`; feature previews are experimental.
Publishing branches does not approve merging them. The setup branch is based on
the baseline and does not contain the frontend or backend feature branches.

## Current connection blocker and exact owner handoff

Both main `0de23f6` and committed frontend `68a9dba` call relative `/api/...`
and same-origin `/ws`. `FLOWSTATE_API` configures only Vite dev/preview proxies;
setting it on Vercel does not connect the built browser application to FastAPI.

`deployment/frontend-urls.patch` is an unapplied patch against frontend `68a9dba`.
It only adds configurable HTTP and WebSocket addresses. The frontend owner should
review/rebase it around their active edits; do not apply it blindly to their dirty
worktree. Local defaults stay unchanged. No rendering/artwork changes are included.

After this patch is integrated into an approved deployment branch, configure:

| Variable | Example | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | `https://api.example.org` | Backend origin, no `/api` suffix |
| `VITE_WS_URL` | `wss://api.example.org/ws` | Complete backend WebSocket URL |
| `FLOWSTATE_API` | `http://127.0.0.1:8000` | Existing local dev/preview proxy only |

Set the two `VITE_` values for the appropriate Preview/Production environments,
then rebuild. They are public browser configuration, never credential storage.
An HTTPS frontend requires HTTPS and WSS endpoints with valid certificates.
No HTTP rewrite is needed when the browser calls the backend directly. Do not
route the simulation socket through an assumed Vite proxy on Vercel.

## Persistent backend recommendation

For this demo use one always-on Linux VM or container host, one FastAPI process,
and the SUMO subprocess on the same machine. Avoid autoscaling or sleeping instances
for an in-memory live simulation. Start with 2 dedicated vCPU and 4 GiB RAM as a
provisional sizing hypothesis, then benchmark dense traffic; no remote sizing has
been validated. Provider selection/provisioning is deferred, with no account or
infrastructure changes. Stripe Directory CLI is unavailable locally; its setup
documentation was consulted, but no provider search or vendor comparison is claimed.

Use a separate checkout pinned to the chosen approved commit, not any agent's
worktree. Install the tested Python dependencies and matching SUMO/TraCI versions;
the existing lower-bound-only requirements are not a reproducible deployment lock.
SUMO version changes can change network generation and simulation outcomes.

From repository root in that separate Linux environment:

```sh
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
PYTHONPATH=backend FLOWSTATE_BACKEND=traci FLOWSTATE_RUNS_DIR=/var/lib/flowstate/runs \
  .venv/bin/python -m uvicorn flowstate.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

Use a process supervisor with restart-on-failure, persistent writable storage for
`data/work`, network cache/output, and `/var/lib/flowstate/runs`, and a TLS reverse
proxy forwarding HTTP and WebSocket upgrades. Keep one worker: the run/controller
state lives in process memory, and multiple workers would disagree about it.
`SUMO_HOME` is optional when the pip SUMO package provides the binaries.
Bind the application port privately behind the TLS proxy. A restarted process
does not resume an in-flight SUMO run; persistent results do not imply live recovery.

Existing FastAPI CORS allows all origins and controls a shared demo session without
authentication. Restrict access for the demo before exposing the control endpoints;
CORS alone is not authentication and does not protect the WebSocket. Coordinate an
explicit allowed-origin/access policy with the backend owner. No policy changed here.

## Release checks (all required before claiming a connected deployment)

1. Publish only approved pinned snapshots; retain the integration/safety hold.
2. Frontend owner integrates the URL patch; build and existing tests pass.
3. Provision the separate backend, validate SUMO executable/version and writable paths.
4. HTTPS `/api/health`, `/api/scenarios`, and `/api/networks/district` return JSON.
5. From the actual Vercel origin, preflight and API requests succeed; WSS `/ws`
   upgrades, receives `hello`, frames/events, and reconnects after disconnect.
6. Start, pause, resume, stop and restart a five-minute run through the deployed UI;
   confirm results persist and a second browser sees the shared-session behavior.
7. Test proxy idle timeouts and host restart explicitly. No hosted smoke test has run.
8. Dense-traffic certification stays blocked on the pending cap, collision/discard
   accounting and measured congestion acceptance gates in the coordination branch.

Sources: [Vercel Vite](https://vercel.com/docs/frameworks/frontend/vite),
[Vite browser environment variables](https://vite.dev/guide/env-and-mode),
[Vercel limits](https://vercel.com/docs/limits).

## Production backend package (branch `deploy/production-backend`)

`deployment/backend/` holds a Dockerfile, a compose file (API + Caddy for automatic
HTTPS/WSS) and `.env.example`. The API reads `FLOWSTATE_ALLOWED_ORIGINS` (CORS and
`/ws` origin check), `FLOWSTATE_MAX_WS_CLIENTS` and an optional
`FLOWSTATE_ACCESS_TOKEN` (bearer token for non-GET requests). Unset, behaviour is
unchanged for local development. Origin checks stop other websites, not scripted
clients, and the UI does not send a token, so this is not real authentication.

Frontend: the Vercel project's Production env needs
`VITE_FLOWSTATE_API_URL=https://<api-domain>` (origin only, no `/api`), then redeploy.
(`VITE_API_BASE_URL`/`VITE_WS_URL` above are superseded by this variable on `main`.)
