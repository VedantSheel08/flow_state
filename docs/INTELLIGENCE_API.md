# Intelligence API v1 and model operations

The forecasting pipeline is a self-contained component (`backend/flowstate/lab/`). The lab and, later, the main app can use it without changes to
the controllers. Schema id: `flowstate.intelligence/v1`. Router: `flowstate.lab.intel_api.router`, prefix `/api/intelligence/v1`. It is already mounted
in the lab API (`:8015`). To mount it in another FastAPI app, `include_router` it and set `intel_api.live_provider` to a function that returns the
latest live report, or leave the default, which makes `/live` return 404.

## The three kinds of value (never mixed, in every response)

| Block | Meaning | Source |
|---|---|---|
| `observed` (`kind: "observed"`) | Detector measurements at time `t`: queue, vehicles, mean wait, downstream occupancy, signal green, and `emergencyVehicles` (each ambulance's state: moving, stopped at a red light, stuck behind queued cars, green ahead, with metres to its next signal) | SUMO |
| `predicted` (`kind: "prediction"`) | Forecasts made at `madeAtT` for `targetT = madeAtT + 30` or `+ 60`, with the model id, version and sha256, and a calibrated 90% interval when one is bound to the artifact. `null` when no model is available (never zeros) | model |
| `interventions` (`kind: "controller_intervention"`) | The controller in use, whether ML is in control, whether it clears lights ahead of ambulances (`emergencyGreen`), and for each decision the ML changed, its **evidence**: base vs forecast-adjusted demand of the green and waiting stage, the forecast values behind the shift, and any exit forecast to be full | controller log |

`warnings[]` carry `basis: "observed"` (`bottleneck_now`: measured queue at least 50% of the approach) or `basis: "predicted"`
(`spillback_risk`). A predicted warning states its rule (`point forecast >= tau x capacity`) and that rule's held-out precision and recall. Those are
measured rates for the rule, not a probability for any single alert. A forecast cell whose point value is under the threshold but whose calibrated upper
bound is not carries `possibleSpillback: true`.

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/intelligence/v1/` | schema, versions, endpoint list |
| `GET /model` | model status, health, thresholds, held-out headline, Gemini mode |
| `GET /health?deep=` | health check (see below) |
| `GET /approaches` | the 16 approaches in model order: key `A0:N`, junction, direction, capacity, entry |
| `GET /live` | latest live report (404 until a live run has produced an observation) |
| `GET /replays/{id}/report?t=&side=left\|right` | the report at time `t` of a recorded run |
| `GET /replays/{id}/series?side=` | compact chart series: observed network queue, forecasts aligned to the time they predict, interventions |
| `GET /benchmarks[?section=]` | compact dashboard export (below) |
| `POST /explain` `{question, replayId?, live?}` | text answer (Gemini or deterministic fallback), labelled with its `source` |

The live WebSocket (`/ws/live`) is unchanged. Each `obs` message (every 5 s of simulated time) now also carries `intel`, the same v1 report. Existing
fields and the `/api/lab/*` endpoints are untouched, so the current frontend keeps working. Codex can read `intel` instead of assembling its own
view.

## Model registry and health (`artifacts/lab/`)

* `MODEL_MANIFEST.json`: id `queue-forecaster`, version, sha256 of `forecaster.pkl`, the feature list and its hash, horizons, and evaluation sources.
  The artifact is **hash-checked before it is unpickled**, so a swapped or corrupted file is refused.
* `calibration.json`: bound to the artifact by sha256. Without a match, intervals are absent (not guessed).
* `golden_inference.json`: 32 stored inputs with the artifact's own outputs.
* `python -m flowstate.lab.cli health [--deep]` (exit 1 if unusable) and `GET /health` run these checks: artifact present, manifest present and schema
  supported, hash matches, feature schema matches the code, artifact loads, outputs finite and in range, latency within budget (best of 5), golden
  vectors reproduced, calibration bound, and with `deep` flat evaluation equal to scikit-learn. Status: `ok`, `degraded` (usable, for example no
  calibration) or `unavailable`. When the model is unavailable the lab disables the ML controller and returns `predicted: null`. It does not crash.

## Reproducible commands (from `backend/`, using the repo `.venv`)

```
python -m flowstate.lab.cli manifest     # register the artifact on disk; does not retrain
python -m flowstate.lab.cli benchmark    # about 2 min: breakdowns + calibration -> forecast_breakdown.json, calibration.json
python -m flowstate.lab.cli profile      # SUMO / observation / inference / report / memory at demand 1.0 and 1.4 -> performance.json
python -m flowstate.lab.cli export       # compact dashboard export -> benchmark_export.json (about 30 KB)
python -m flowstate.lab.cli health --deep
```

The dataset (`data/lab/dataset.npz`, git-ignored) regenerates with `cli dataset`. `cli train` re-registers the new artifact automatically. After retraining,
run `benchmark` and `export`, because the old calibration no longer matches and health reports `degraded` until you do.

## Benchmark export (`GET /benchmarks`, schema `flowstate.benchmark-export/1`)

`forecast.horizons["30"|"60"]` holds `overall` (with an episode-clustered bootstrap CI), `byScenario`, `byJunction`, `byApproach`, `byTrueSeverity`,
`byPredictedOccupancy`, `byDataController`, `warnings` (persistence vs model point vs tuned alert rule vs upper bound) and `warningsByScenario`.
Alongside it are `regimeShiftCheck`, `controllers` (the paired ML vs heuristic vs fixed-time evaluation) and `performance`.

## Gemini

`/explain` and `/api/lab/ask` use the existing `GuardedGemini` (server-side key, cache, 10 requests/min, timeouts, breakers). They call it from a
worker thread, never from the simulation loop. A Gemini answer is accepted only if every number in it appears in the measured facts. Facts include the
forecast benchmarks, the paired evaluation, the selected replay and the live snapshot. With no key, an open breaker, a 401/402/429/5xx, a timeout or an
ungrounded number, a deterministic answer built from the same facts is returned and `source` is `"measurements"`.

**Dependency:** the REST client, `flowstate.experiments.assistant.GeminiClient`, lives on `task/integrations` and is not on this branch. Until that
branch is merged, `/model` reports `gemini.mode: "offline_fallback"`. The path was verified against the real client from that branch over a mocked
transport (`tests/test_lab_intel.py::test_real_gemini_client_through_guard_and_grounding`). Run it with
`FLOWSTATE_INTEGRATIONS_PATH=<checkout>/backend/flowstate`. Without that variable or the package the test skips. No live Google call has been made, because
no key is configured here.
