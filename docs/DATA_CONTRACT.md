# Data contract for dashboard and RL agents

Schema version: `export.SCHEMA_VERSION`. This branch edits none of their files; they consume these endpoints or the committed JSON.

## REST (mount `flowstate.experiments.api.router`, or run `python -m flowstate.experiments serve --port 8010`)

| Method / path | Returns |
|---|---|
| `GET /api/experiments/status` | store backend (`postgres`/`sqlite`/`off`), reachable, counts |
| `GET /api/experiments?scenario=&controller=&suite=` | experiment list |
| `GET /api/experiments/{id}` | full export document (below) |
| `GET /api/experiments/{id}/kpi?step=30` | bucketed KPI time series |
| `GET /api/experiments/{id}/heatmap` | per-edge density/occupancy/queue + per-junction crossings |
| `GET /api/experiments/{id}/signals` | signal decisions + stats |
| `GET /api/experiments/{id}/safety` | collisions / teleports / emergency stops |
| `GET /api/experiments/compare?baseline=&candidate=` | paired before/after document |
| `GET /api/experiments/controllers?scenario=&baseline=fixed` | per-controller mean/std + paired per-seed deltas (95% CI) |
| `POST /api/experiments/run` | start a real SUMO run in a worker process → `jobs/{id}` |
| assistant: `GET …/assistant/status`, `POST …/assistant/plan`, `POST …/assistant/run {planId}`, `GET …/assistant/runs/{id}` | see INTEGRATIONS.md |

## Static files (no backend needed for charts)

`data/benchmarks/v2/<suite>/`: `manifest.json`, `comparisons.json` (`controllers.<name>.metrics.<metric> = {mean,std,n}`, `pairs[]` with paired deltas), `pairs/<seed>.<controller>.json` (aligned series + per-edge difference), `experiments/<id>.json`. Suites: `rush-am-fixed-vs-actuated`, `ambulance-preemption`, `toronto-rush-preset`, `toronto-gridlock-preset`.

Metrics names (per experiment and in comparisons): `trips_completed, throughput_per_h, mean_travel_s, mean_time_loss_s, mean_wait_s, stops_per_trip, mean_speed_kmh, mean_density_veh_km_lane, mean_storage_occupancy, peak_storage_occupancy, mean_queue, peak_queue, pending_max, teleports, collisions, emergency_stops, co2_total_g, co2_g_per_km, co2_g_per_trip, fuel_total_g, idle_s_total, idle_co2_g, idle_fuel_g, idle_share_of_…` plus scenario-specific `scenarioMetrics`/`scenarioDeltas` (e.g. `signals.*`, ambulance response).

Controller labels: `fixed, actuated, fixed+preempt, actuated+preempt, fixed+tsp, actuated+tsp` — every governed switch is set explicitly, so `fixed` is a true baseline.

Congestion heatmap: use `heatmap.edges[].occupancy` / `queue` (colour) and `heatmap.junctions[].crossings` (discharge). Emissions comparison: `co2_*`, `fuel_total_g`, `idle_*` in `comparisons.json`. All emissions are SUMO HBEFA3 simulated estimates.

## For the RL agent

`ExperimentSpec.policy_factory(engine)` (in-process only; not available through the worker/benchmark CLI) may return a `SignalPolicy`, which the recorder appends to `engine.signals.policies`. The recorder then stores the **resulting phase changes** (`signal_events.kind = 'phase'`) plus all KPIs, emissions and safety events, so a policy can be compared with `fixed` on the same seeds via `/controllers`. The schema has a `policy` decision kind, but **nothing writes it yet**: per-decision explanations and a `record_decision` helper were not built. Add them in the policy/recorder together if the explainable-decision demo needs it.
