# FlowState sponsor integrations — status, setup, limits

Branch `task/integrations`. Nothing here is deployed or pushed. The simulation, dashboard and RL code paths do not import any of this; the experiment store never raises into the simulation (if the database is unreachable it logs once and the run still finishes and exports JSON).

## Status at a glance

| Integration | State | Verified how | Not verified |
|---|---|---|---|
| Tiger Data (PostgreSQL / TimescaleDB) | Built: schema, bulk writer, history + controller-comparison queries, REST API, SQLite fallback | Real PostgreSQL 18.4 (embedded, local): DDL, COPY ingest, every query, full round trip of a real SUMO run. SQLite path covered by the test suite | **TimescaleDB hypertables / Tiger Cloud connection** — no extension locally and no Tiger account was available. `schema_timescale.sql` is generated from the same table definitions but never executed against Timescale |
| Environmental analytics | Built on SUMO's own HBEFA3 emission, fuel and idle outputs; paired baseline-vs-candidate deltas with 95% t-intervals | Real SUMO runs, 4 benchmark suites (committed) | Absolute grams depend on the assumed fleet mix (see below); they are model estimates, not measured emissions |
| Gemini scenario assistant | Built: propose → validate → clarify → run validated plan → summarise measured results; rule-based offline fallback | 27 tests incl. a local mock transport for the request shape; the fallback ran end-to-end through real SUMO | **No live Gemini call** — no API key in this environment, so the model id and response shape are unconfirmed against the live service |
| Toronto open data | Built: City of Toronto Turning Movement Count summary, pinned snapshot + provenance, plausibility comparison | Live CKAN requests (test hits the portal), snapshot of 248 downtown signalised intersections | — |
| Vultr | `deploy/vultr/` Dockerfile, compose, cloud-init, optional Caddy TLS, `deploy.sh` | YAML syntax-checked | **Image never built (no Docker here), nothing deployed** — deliberately, pending approval |

## Prize / credit eligibility (what I could and could not confirm)

I had no authenticated access to the Hack the Valley 11 portal or sponsor consoles, so **the official prize rules were not read**. Treat everything below as to-be-confirmed by a human against the HTV11 Devpost/MLH page.

- **Tiger Data**: the publicly documented offer I found is a Tiger Cloud trial (about $1,000 / 30 days) for *new* sign-ups via the hackathon/MLH link. An existing account may not qualify. Prize criteria likely reward real use of TimescaleDB features (hypertables, continuous aggregates, compression); the code supports hypertables but **running it on Tiger Cloud is the remaining step** and the only way to claim "uses Tiger Data" honestly.
- **Gemini**: needs a Google AI Studio key; free-tier availability and model names change — confirm `GEMINI_MODEL`. Prize eligibility usually depends on genuine use in the product; the assistant is optional and degrades to rules without a key.
- **Vultr**: a ~$100 promo credit has been advertised for hackathons; confirm the code. Eligibility likely needs the project to actually run on Vultr, which requires a deploy approval.
- **Toronto data**: a City of Toronto open-data licence (Open Government Licence – Toronto); no sponsor prize attached that I could verify.

## Setup

```bash
pip install -r backend/requirements-integrations.txt     # psycopg[binary]; optional
```

| Variable | Purpose |
|---|---|
| `FLOWSTATE_DATABASE_URL` (or `TIGER_DATABASE_URL`) | PostgreSQL / Tiger Cloud URL (`postgresql://…?sslmode=require`). Server-side only |
| `FLOWSTATE_EXPERIMENT_DB` | SQLite path for the fallback store (default `data/experiments/experiments.sqlite`) |
| `FLOWSTATE_STORE` | `postgres` \| `sqlite` \| `off` (default: postgres if a URL is set, else sqlite) |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini assistant. Unset → rule-based parser |
| `GEMINI_MODEL`, `GEMINI_API_MODE` | Model id / API flavour overrides |

Tiger Cloud: create a service, copy its connection string into `FLOWSTATE_DATABASE_URL`, then `python -m flowstate.experiments status` — opening the store applies the schema (`schema --timescale` prints the Timescale SQL).

Run:

```bash
python -m flowstate.experiments serve --port 8010                  # standalone API
# or in app.py:  app.include_router(flowstate.experiments.api.router)   # (not edited by this branch)
python -m flowstate.experiments benchmark --suite my-suite --scenario rush_hour \
    --controllers fixed,actuated --seeds 1,2,3,4,5
```

## Database schema (generated from `experiments/tables.py`)

Tables: `experiments`, `kpi_samples`, `edge_samples`, `junction_samples`, `signal_events`, `safety_events`, `trip_results`, `experiment_summary`. Time-series tables are keyed `(experiment_id, ts, …)` where `ts = started_at + simulation time` (a virtual clock), and become hypertables on `ts` under Timescale. Dialect files: `schema_postgres.sql`, `schema_timescale.sql`, `schema_sqlite.sql`. Queries (`store.py`): `kpi_series` (bucketed), `edge_heatmap`, `junction_heatmap` (crossings = discharge), `signal_events`, `signal_decision_stats`, `safety_events/counts`, `trips`, `compare_experiments`, `compare_controllers` (paired per-seed deltas, t-quantile CI).

## Environmental analytics — what is and is not claimed

- Source: SUMO's HBEFA3 emission model, per-vehicle-type emission classes (PC_G_EU4, LDV_D_EU4, HDV_D_EU4, Bus, Zero for e-vehicles), fuel in mg, and idle time (speed < 0.1 m/s and not at a planned stop), all taken from the running simulation.
- Comparison is paired by seed (same demand, different controller). No percentage reduction is assumed or extrapolated.
- **These are simulated estimates.** The fleet mix is an assumption; absolute idle emissions are model-dependent (HBEFA3 at standstill). Use deltas, not absolutes.

### Measured results (committed benchmark data, 5 seeds, `rush_hour`, 5 min after 90 s warm-up)

- Actuated vs fixed: throughput +5.6% in every seed; idle CO2 −10.3%; total CO2 and mean travel time changes **not statistically significant**.
- Pre-emption (`ambulance` scenario): ambulance response −7.6 s but general traffic worse; collisions +2.6 on average (not significant, n=5).

## Gemini assistant

Interface: `POST /api/experiments/assistant/plan {"message": …}` → `{planId, plan, clarifications, issues}`; `POST …/assistant/run {"planId"}`; `GET …/assistant/runs/{id}`. The model's output is parsed with a strict schema (extra fields forbidden), validated against the real scenario catalogue and network, and the executable plan is **rebuilt from validated fields** — model text is never executed. A run needs a server-issued `planId`. Summaries may only quote numbers present in the measured results; otherwise a deterministic summary is used.

## Toronto context

Dataset: City of Toronto *Traffic Volumes – Multimodal Intersection Turning Movement Counts* (`tmc_summary_data`, CKAN resource `1364bffa-29a3-4c39-af8a-925d8ca7bf1f`). Snapshot `data/toronto/tmc_downtown_peak_context.json` (hash-stamped; licence: Open Government Licence – Toronto; the portal page itself lists none). Used only as a **plausibility band**: where the simulated busiest-junction hourly volume sits in the observed distribution of downtown peak-hour intersection volumes (median 1,344 veh/h, p90 2,054). It is not a calibration and **the FlowState map is a synthetic district, not Toronto**.

Measured against it (3 seeds each):
- `rush_hour` preset: busiest junction 2,064–2,292 veh/h = p90–p94 of observed (×1.5–1.7 median).
- `gridlock` preset: 2,364–2,652 veh/h = p96–p98 (×1.8–2.0 median). Above the observed IQR — intentionally stressful, but busiest-junction volumes are within the observed upper tail, not absurd.
- Bicycle share: simulation ~4–6% vs observed median 13.8% (IQR 6.9–19.9) — **the default bike share is low relative to Toronto** (the `bike_lanes` scenario's 14% matches). Heavy-vehicle share 3.6–8.8% vs median 3.1%.

## Vultr

`deploy/vultr/`: `Dockerfile`, `docker-compose.yml` (ports bound to loopback), `cloud-init.yaml`, `Caddyfile` (optional TLS profile), `.env.example`, `deploy.sh`. Secrets live only in the server's `.env` (chmod 600), never in the image or frontend bundle. Not built, not deployed. Nothing touches the local demo.

## Limitations and remaining work

1. Run a benchmark against a real Tiger Cloud service; confirm hypertable creation (and optionally add continuous aggregates / compression).
2. One live Gemini call to confirm model id and response schema.
3. Build the Docker image and deploy to Vultr (needs approval).
4. 5 seeds gives limited power; most controller differences other than throughput are not significant.
5. Stale pre-v2-schema output under `data/benchmarks/{v1,ambulance-preemption,rush-am-fixed-vs-actuated}` is git-ignored and safe to delete; scratch dirs `D:\tmp_sig_nets`, `D:\tmp_base_wt`, `D:\tmp_integ_site`, `D:\tmp_integ_pgdata` too (my sandbox blocked `rm -rf`).

## Verification log (latest pass)

- Full suite with real PostgreSQL available: **132 passed, 0 skipped** (the previous "3 skipped" were caused by a bad PYTHONPATH, not missing software). Run with `psycopg[binary]` and `pixeltable-pgserver` installed, or set `FLOWSTATE_TEST_PG_URL`.
- Credentials found in this environment: **none** (`FLOWSTATE_DATABASE_URL`, `TIGER_DATABASE_URL`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` all unset). So Tiger Cloud/TimescaleDB and live Gemini remain unverified; the local fallback is verified: with an unreachable `FLOWSTATE_DATABASE_URL` the API logs "falling back to SQLite" and still serves stored runs.
- Emissions: `python -m flowstate.experiments.verify_emissions` reruns a SUMO experiment with SUMO's own `--emission-output` XML and integrates it. Result (`data/verification/emissions.json`): recorder/SUMO-XML = 1.0016 for CO2 and 1.0016 for fuel (boundary-step difference), 3.14 g CO2 per g fuel. The stored numbers are SUMO's output; they remain HBEFA3 model estimates for an assumed fleet.
- API contract: `docs/openapi-experiments.json` generated from the app; every endpoint exercised against real stored runs (see DATA_CONTRACT.md).
