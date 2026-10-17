# Data contract for the dashboard, RL and integration agents

Machine-readable: `docs/openapi-experiments.json` (generated from the router). Every response below was observed against real stored SUMO runs (`data/benchmarks/v2/*`), not hand-written. `schemaVersion: 1`.

Mount: `app.include_router(flowstate.experiments.api.router)` **and** `app.include_router(flowstate.experiments.assistant_api.router)` (prefixes `/api/experiments`, `/api/experiments/assistant`), or run standalone (both included, CORS for the Vite dev origins) `python -m flowstate.experiments serve --port 8010`. This branch does not edit `app.py`.

## Endpoints the integration agent should consume

| Method / path | Query | Response (top-level keys) |
|---|---|---|
| `GET /api/experiments/status` | – | `backend` (`postgres`\|`sqlite`), `timescaledb` (bool), `configured`, `controllers[]`, `kpiMetrics[]`, `summaryMetrics[]`, `experiments` (count) |
| `GET /api/experiments` | `scenario, controller, suite, seed, limit≤500, offset` | `experiments[]` (id, name, scenario, controller, seed, params, params_hash, status, suite, summary…) |
| `GET /api/experiments/{id}` | `detail=full\|summary, step, heat_bucket` | full export (`experiment, summary, extra, kpi, heatmap{edges,junctions,bucketS}, signals, safety, emissions, units, run`); `detail=summary` = metadata + `summary` |
| `GET /api/experiments/{id}/kpi` | `metrics=a,b, step` | `series[]`: `{sim_t, active_vehicles, halted_vehicles, mean_speed_kmh, density_veh_km_lane, storage_occupancy, queue_total, queue_max, queued_junctions, pending, persons, completed_total, throughput_per_h, mean_travel_s, …}` |
| `GET /api/experiments/{id}/heatmap` | `metric=halting, bucket=30, top=60` | `cells[]`: `{edge_id, t0, value, peak, samples}` |
| `GET /api/experiments/{id}/signals` | `tls, kind, limit` | `stats`, `events[]` (kinds written today: `phase`, `preempt`, `tsp`) |
| `GET /api/experiments/{id}/safety` | – | `counts`, `events[]` (`collision`, `teleport`, `emergency_stop`) |
| `GET /api/experiments/compare` | `baseline, candidate, step` | before/after for one pair: `paired`, `metrics[name] = {baseline, candidate, delta, pct, better, improved}`, `kpi{baseline,candidate}` aligned series, `haltingDifference.cells[] = {edge, t0, baseline, candidate, delta}`, `emissionsNote` |
| `GET /api/experiments/controllers` | `scenario` (required), `baseline=fixed, params_hash, suite` | `controllers.<name> = {n, seeds, metrics{mean,std,n}, deltas{meanDelta, std, ci95, pctOfBaseline, better, improvedInEverySeed}, scenarioMetrics, scenarioDeltas}` — **use this for presentation claims**; one seed can mislead (seed 1 throughput +10.2%, 5-seed mean +5.6%) |
| `POST /api/experiments/run` | body `{scenario, controller, seed, params, warmup}` | `202` + job id (runs real SUMO in a worker process) |
| `GET /api/experiments/jobs/{id}` | – | status/progress/result |
| Assistant: `GET /api/experiments/assistant/status`, `POST …/assistant/plan {"message"}`, `POST …/assistant/run {"planId"}`, `GET …/assistant/runs/{id}` | – | see INTEGRATIONS.md. Mounted by `assistant_api.router` (separate router — include it too) |

Errors: unknown id → `404 {"detail"}`. With no database URL configured the API reads the local SQLite store (`status.backend` tells which).

## Static files (no backend needed)

`data/benchmarks/v2/<suite>/`: `manifest.json`, `comparisons.json` (same shape as `/controllers` + `pairs[]`), `pairs/<seed>.<controller>.json` (same shape as `/compare`), `experiments/<id>.json` (same as `GET /{id}`). Suites: `rush-am-fixed-vs-actuated`, `ambulance-preemption`, `toronto-rush-preset`, `toronto-gridlock-preset`. Emissions cross-check: `data/verification/emissions.json`.

Metric names: `trips_completed, throughput_per_h, mean_travel_s, mean_time_loss_s, mean_wait_s, stops_per_trip, mean_speed_kmh, mean_density_veh_km_lane, mean_storage_occupancy, peak_storage_occupancy, mean_queue, peak_queue, pending_max, teleports, collisions, emergency_stops, co2_total_g, co2_g_per_km, co2_g_per_trip, fuel_total_g, idle_s_total, idle_co2_g, idle_fuel_g`, plus scenario metrics (`signals.*`, ambulance response). Controller labels: `fixed, actuated, fixed+preempt, actuated+preempt, fixed+tsp, actuated+tsp` — every governed switch is set explicitly, so `fixed` is a true baseline. Heatmap in an export document: `heatmap.edges[]` / `heatmap.junctions[]` (junction `crossings` = discharge). All emissions are SUMO HBEFA3 simulated estimates — show deltas, label absolutes as modelled.

## For the RL agent

`ExperimentSpec.policy_factory(engine)` (in-process only; not available through the worker/benchmark CLI) may return a `SignalPolicy`, which the recorder appends to `engine.signals.policies`. The recorder stores the resulting phase changes (`signal_events.kind = 'phase'`) plus all KPIs, emissions and safety events, so a policy can be compared with `fixed` on the same seeds via `/controllers`. The schema allows a `policy` decision kind, but **nothing writes it yet**; per-decision explanations would need to be added in the policy and recorder together.
