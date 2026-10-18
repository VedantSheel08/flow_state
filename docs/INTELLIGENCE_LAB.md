# FlowState Intelligence Lab

Standalone local demo of queue forecasting and ML-assisted signal control on real SUMO runs. It is separate from the production app.

```powershell
.\run-lab.ps1        # API http://127.0.0.1:8015, UI http://localhost:5176
```

Manual start: `cd backend; ..\.venv\Scripts\python -m flowstate.lab.cli serve --port 8015` and `cd lab; npm run dev`.
Paired replays live in `data/lab/replays/` (git-ignored, about 10 MB). `run-lab.ps1` records them if they are missing
(`python -m flowstate.lab.cli record`, about 2 minutes).

See also: [STUDIO.md](STUDIO.md) (the separate Studio app), [INTELLIGENCE_API.md](INTELLIGENCE_API.md) (versioned API, model health, commands) and [FORECAST_AUDIT.md](FORECAST_AUDIT.md) (leakage audit, per-scenario/junction/severity accuracy, known weaknesses).

## What runs

* **Network and simulation:** the RL 2x2 signalised grid (`flowstate.rl.sim.SignalSim`) with the same seeded demand, the same
  SafeSignal layer (min green 14 s including pedestrian clearance, yellow 4 s, all-red 2 s, max green 60 s) and the same SUMO-side
  measurement. `LabSim` only adds CO2 (SUMO HBEFA3, assumed fleet classes), frames and a TrafficState observation. A test checks
  that its metrics are identical to `SignalSim`'s.
* **Live:** one SUMO worker process streams 1 s frames over `/ws/live`. Start, pause, resume, stop and speed 1-16x are supported. The frame
  queue is bounded, so a slow browser loses frames and never slows SUMO.
* **Comparison:** two independent SUMO processes run the same route file (SHA-256 checked) and SUMO seed. The browser plays them back in sync,
  with a scrubbable timeline and ticks wherever the ML changed a decision. New pairs can be recorded from the UI (about 10 s).
* **Gemini:** `/api/lab/ask` goes through the existing `GuardedGemini` (server-side key, cache, rate limit, timeouts, breakers).
  A Gemini answer is accepted only if every number in it appears in the measured facts. Otherwise, and whenever no key is configured,
  a deterministic answer built from the same facts is returned. Gemini is never in the control loop.

## Forecaster (`artifacts/lab/forecast_eval.json`)

Two HistGradientBoosting regressors predict the halting vehicles on each of the 16 approaches 30 s and 60 s ahead. They use 19
detector-like features: counts, halts, waiting, downstream occupancy, signal state, upstream platoons, and 10/20/30 s history.

* Data: 256 SUMO episodes and 927k approach-samples, split **by seed range**. Train uses seeds 1000-1011, validation 2000-2003, and test
  3000-3007. The rush-hour ramp scenario appears only in test.
* Held-out MAE (vehicles): **30 s: 2.23** (persistence 4.38, ridge 2.94, linear trend 6.50). **60 s: 2.59** (persistence 3.46, ridge 2.80).
  R² is 0.77 at 30 s and 0.71 at 60 s. On the unseen rush-ramp scenario at 30 s, MAE is 2.87 (persistence 5.37).
* Spillback detection (queue at least half the approach): F1 is 0.53 at 30 s (persistence 0.42) and 0.47 at 60 s (persistence 0.47, so no gain).
* Inference: trees are evaluated as flat NumPy arrays and match scikit-learn to 1e-13. Each control decision takes about 8 ms
  (16 approaches x 2 horizons, single thread).

## Ambulances and ML-assisted control (`artifacts/lab/comparison_eval.json`)

**Ambulances.** As on main, the simulator gives emergency vehicles no priority: they stop at red lights and wait in the same queues as other cars, and no driver pulls over.
The lab's controllers are blind to them by default. A controller whose id ends in `_ev` (`queue_heuristic_ev`, `ml_forecast_ev`) turns the lights ahead of an ambulance green
(the heuristic's existing emergency rule, so it is *not* machine learning, and SafeSignal limits still apply). The ambulance still cannot pass queued cars. Ambulance state
(moving / stopped at a red light / stuck behind queued cars) is measured from SUMO's own next-signal and speed values, and is recorded per second in every replay.

`ForecastAware` is the tuned queue heuristic plus two bounded uses of the forecast: each stage's demand is shifted by β x (predicted queue - current queue), capped at ±6 vehicles
(β = 0.1, chosen on tuning seeds 1100-1105 under the previous, ambulance-aware controllers), and a stage whose exit approach is predicted to reach 0.7 x capacity is treated as blocked.

Five arms were each run on 60 paired episodes (5 scenarios x seeds 3100-3111, never used for training or tuning), with identical demand per pair (checked by hash).

**Forecast alone** (`queue_heuristic` -> `ml_forecast`, neither gives ambulances priority):

| Metric | Left | Right | Mean Δ | 95% CI |
|---|---|---|---|---|
| Mean wait, all vehicles except ambulances (s) | 105.3 | 106.6 | +1.3 | [-1.9, 4.6] |
| Mean travel time (s) | 210.1 | 210.3 | +0.2 | [-3.4, 3.7] |
| Throughput (veh/h) | 2783 | 2778 | -5 | [-26, 15] |
| Mean queued vehicles | 69.4 | 71.1 | +1.7 | [-0.4, 4.0] |
| CO2 estimate (kg) | 595.7 | 602.0 | +6.3 | [-4.1, 16.5] |
| Teleports / episode | 0.87 | 1.07 | +0.20 | [-0.07, 0.48] |
| Ambulance travel time (s) | 201.7 | 209.4 | +7.7 | [1.1, 15.3] |
| Ambulance stopped at red lights (s) | 21.7 | 22.4 | +0.6 | [-2.6, 4.2] |
| Ambulance stuck behind queues (s) | 76.6 | 83.7 | +7.2 | [0.8, 14.3] |

The forecast does not reliably improve waiting, travel time, throughput or queues, and it makes ambulances slower when nobody clears their lights.

**Ambulance green rule alone** (`queue_heuristic` -> `queue_heuristic_ev`):

| Metric | Left | Right | Mean Δ | 95% CI |
|---|---|---|---|---|
| Mean wait, all vehicles except ambulances (s) | 105.3 | 106.4 | +1.1 | [-0.9, 3.3] |
| Mean travel time (s) | 210.1 | 209.7 | -0.4 | [-2.8, 1.8] |
| Throughput (veh/h) | 2783 | 2786 | +4 | [-15, 22] |
| Mean queued vehicles | 69.4 | 69.9 | +0.5 | [-1.0, 1.9] |
| CO2 estimate (kg) | 595.7 | 593.7 | -2.0 | [-9.1, 4.6] |
| Teleports / episode | 0.87 | 0.98 | +0.12 | [-0.18, 0.42] |
| Ambulance travel time (s) | 201.7 | 158.0 | -43.7 | [-50.5, -37.0] |
| Ambulance stopped at red lights (s) | 21.7 | 6.7 | -15.0 | [-17.5, -12.5] |
| Ambulance stuck behind queues (s) | 76.6 | 52.4 | -24.2 | [-30.4, -18.2] |

It is a large, consistent win for ambulances (better in 60 of 60 pairs) and costs everyone else nothing measurable. Ambulances still sit in queues for about 52 s on average.

**The demo pair** (`queue_heuristic` -> `ml_forecast_ev`: ML forecast plus ambulance green):

| Metric | Left | Right | Mean Δ | 95% CI |
|---|---|---|---|---|
| Mean wait, all vehicles except ambulances (s) | 105.3 | 108.1 | +2.8 | [0.2, 5.5] |
| Mean travel time (s) | 210.1 | 210.9 | +0.8 | [-2.1, 3.7] |
| Throughput (veh/h) | 2783 | 2787 | +4 | [-18, 25] |
| Mean queued vehicles | 69.4 | 72.0 | +2.6 | [0.5, 4.9] |
| CO2 estimate (kg) | 595.7 | 601.5 | +5.8 | [-4.0, 15.8] |
| Teleports / episode | 0.87 | 1.22 | +0.35 | [0.02, 0.70] |
| Ambulance travel time (s) | 201.7 | 159.1 | -42.6 | [-51.8, -34.0] |
| Ambulance stopped at red lights (s) | 21.7 | 8.3 | -13.5 | [-16.6, -10.5] |
| Ambulance stuck behind queues (s) | 76.6 | 55.0 | -21.5 | [-28.2, -15.1] |

The ambulance gain is the controller rule, not the forecast. Other vehicles are slightly worse off (see the first row; the Results tab in the Studio flags what is clearly worse).

For reference, the heuristic against fixed-time: mean wait -3.52 s, CI [-7.45, 0.74] (not clear).
The forecast changed 2,915 of 38,035 eligible decisions in the forecast-only arm (7.7%).

## Performance (this machine, 8 logical cores, shared)

Each SUMO step (1 s simulated) takes about 1-3 ms. Each TrafficState observation takes about 0.5 ms, and each ML decision about 4-8 ms.
A WebSocket frame send takes about 0.2-0.4 ms. A whole 1,500 s paired episode takes about 10 s of wall time per side.

## Limitations

* The demo uses the small 2x2 grid, not the Toronto district network.
* No Gemini key is configured locally, and the Gemini REST client (`flowstate.experiments.assistant`) is on `task/integrations`, not on this branch.
  The lab therefore answers from measurements until both are present. The Gemini path is tested with a scripted client through the real guard.
* CO2 uses HBEFA3 with an assumed vehicle-class mapping. Use it as a paired difference only, not as an absolute inventory.
