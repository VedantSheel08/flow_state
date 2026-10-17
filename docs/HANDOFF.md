# Handoff: sponsor-integration layer (for the main FlowState integration agent)

Branch `task/integrations`, worktree `D:\SUMA test-wt\integrations`. **Local only: not merged, and the later commits are not pushed** (an earlier push of the branch to `VedantSheel08/flow_state` was explicitly authorised and contains up to `494d434`; `72871d4` is local).

## Commits (in order; each depends on the previous)
| Commit | Content |
|---|---|
| `0de23f6` | Initial FlowState baseline (base) |
| `0b49c08` | task/signals: engineered fixed-time signals, offsets, guardrails |
| `0c5abd9` | task/signals: dense sustained rush hour (**the base this branch sits on**) |
| `05f3150` | Experiment store (Postgres/Timescale/SQLite), emissions, recorder, benchmark runner, REST API |
| `494d434` | Gemini assistant, Toronto context, Vultr config, v2 benchmark suites, docs |
| `72871d4` | Emissions cross-check, OpenAPI contract, fallback verification, docs |

All new code lives in `backend/flowstate/experiments/`, `tests/test_{experiments,assistant,toronto}.py`, `deploy/vultr/`, `data/{toronto,benchmarks/v2,verification}/`, `docs/`. Outside those: `.gitignore` entries (SQLite/WAL, stale benchmark dirs) and `backend/requirements-integrations.txt`. No frontend, engine, scenario or `app.py` files were edited. Integrating = merging the branch (it contains the task/signals commits, so merge order matters: signals first or together).

## Wiring it into the app (not done here, by design)
```python
import flowstate.experiments.api as exp, flowstate.experiments.assistant_api as asst
app.include_router(exp.router)     # /api/experiments
app.include_router(asst.router)    # /api/experiments/assistant
```
Or run standalone: `cd backend && python -m flowstate.experiments serve --port 8010` (both routers + CORS for 127.0.0.1/localhost:5173). `pip install -r backend/requirements-integrations.txt` for PostgreSQL support (`psycopg[binary]`); without it the SQLite store is used.

## API contract
Authoritative: `docs/openapi-experiments.json` (generated) and `docs/DATA_CONTRACT.md` (observed response shapes). Endpoints: `GET /api/experiments/status | "" | compare | controllers | {id} | {id}/kpi | {id}/heatmap | {id}/signals | {id}/safety | jobs/{id}`, `POST /api/experiments/run`; assistant: `GET /status`, `POST /plan`, `POST /run {planId}`, `GET /runs/{id}` under `/api/experiments/assistant`. For presentation claims use `/controllers` (paired, multi-seed, 95% CI), not single pairs. Static equivalents: `data/benchmarks/v2/<suite>/{comparisons,manifest}.json`, `pairs/`, `experiments/`.

## Database configuration
| Variable | Meaning |
|---|---|
| `FLOWSTATE_DATABASE_URL` / `TIGER_DATABASE_URL` | PostgreSQL or Tiger Cloud URL (`?sslmode=require`). **Server-side only; never in frontend** |
| `FLOWSTATE_EXPERIMENT_DB` | SQLite fallback path (default `data/experiments/experiments.sqlite`) |
| `FLOWSTATE_STORE` | `postgres` \| `sqlite` \| `off` |
Behaviour: opening the store never raises; if the DB is unreachable it logs once and uses SQLite (verified with a dead URL). `GET /api/experiments/status` reports the backend actually in use and whether TimescaleDB is active. Schema: generated from `experiments/tables.py`; `python -m flowstate.experiments schema [--dialect sqlite] [--timescale]` prints it. Hypertables are created only if the `timescaledb` extension exists.

## Test results
`cd backend`-level: `PYTHONPATH=<site with psycopg+pixeltable-pgserver>;backend python -m pytest tests -q` → **132 passed, 0 skipped, ~8 min** (real SUMO runs, embedded PostgreSQL 18.4, live Toronto portal). Without psycopg/pgserver the 4 PostgreSQL tests skip and say so. Note: on Windows give Python Windows-style paths in `PYTHONPATH` (msys `/d/...` silently fails and causes skips).

## Optional credentials (none were present; nothing depends on them)
- `FLOWSTATE_DATABASE_URL`: enables Tiger Cloud. **Unverified against Tiger Cloud / TimescaleDB** (verified on plain PostgreSQL only).
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), optional `GEMINI_MODEL`, `GEMINI_API_MODE`: enables LLM interpretation. **Never called live**; model id/response shape unconfirmed. Without a key the rule-based parser runs.
- Sponsor credit/prize terms were not verified against official HTV11 rules (see INTEGRATIONS.md).

## Vultr / Agent 4
Config is in `deploy/vultr/` (Dockerfile, compose with loopback-bound ports 8000/8010, cloud-init, optional Caddy TLS, `.env.example`, `deploy.sh`). It was only YAML-syntax-checked; **the image has never been built and nothing was deployed.** I have no channel to Agent 4 from this session and haven't seen its files, so no coordination actually took place: Agent 4 (or whoever owns deployment) should compare these files against its own, choose one, and keep these invariants: secrets only in the server `.env` (chmod 600), DB/API keys never baked into the image or returned by an endpoint, experiments service separate from the live simulator (SUMO/libsumo is one simulation per process). Deployment still needs your explicit approval.

## Known limits
Tiger/Timescale and Gemini unverified; emissions are HBEFA3 estimates for an assumed fleet (cross-check vs SUMO output 1.0016); 5 seeds per suite (only throughput clearly significant); RL per-decision explanations not recorded; synthetic district is not Toronto (default bike share ~5% vs observed median 13.8%).
Cleanup for the owner: `D:\tmp_sig_nets`, `D:\tmp_base_wt`, `D:\tmp_integ_site`, `D:\tmp_integ_pgdata`, and git-ignored `data/benchmarks/{v1,ambulance-preemption,rush-am-fixed-vs-actuated}`.
