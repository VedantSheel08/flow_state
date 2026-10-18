# FlowState Intelligence Lab

Standalone local demo of queue forecasting and forecast-assisted signal control (a rule-based heuristic that uses a trained queue forecaster) on real SUMO runs. It is separate from the production app.

## What is learned and what is a rule

| Component | What it really is |
|---|---|
| **Queue forecaster** | A **trained model** (gradient-boosted trees) fitted on 256 SUMO episodes and scored on held-out seeds. |
| **Forecast-assisted controller** | A **hand-written queue heuristic** that uses the forecaster's output in two bounded rules. Only the forecaster is learned; the control logic is rules with one parameter tuned by a small grid search. |
| **Ambulance green corridor** | A **hand-written rule** (route-aware preemption through SafeSignal). No model decides it. |
| **PPO agent** | A real **trained RL policy** that failed its validation gate (worse than the heuristic). It is shown as a shadow observer and controls nothing. |
| **Camera detection** | **Classical OpenCV** (background subtraction and contours), not a trained detector, run on images rendered from the simulation. |
| **Gemini** | An external language model that only explains measured results; it is outside the control loop. |
| **Simulation** | Real SUMO traffic physics. |

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
  with a scrubbable timeline and ticks wherever the forecast changed a decision. New pairs can be recorded from the UI (about 10 s).
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

## Ambulances and forecast-assisted control (`artifacts/lab/comparison_eval.json`)

**Ambulances.** As on main, the simulator gives emergency vehicles no priority: they stop at red lights and wait in the same queues as other cars, and no driver pulls over.
The lab's controllers are blind to them by default. A controller whose id ends in `_ev` (`queue_heuristic_ev`, `ml_forecast_ev`) wraps the base controller with a **route-aware green corridor**:
for every ambulance on the road, the next three signalised junctions on its route (within 450 m) are asked to show the stage that serves the approach it will use. Green already: hold it.
Another stage green: ask for the switch, which SafeSignal grants only after the minimum green and then runs the full yellow and all-red. Clearing the junctions *ahead* as well lets the queue in front of
the ambulance discharge through them instead of backing up at a red light one block on. This is a **rule, not machine learning**: no model decides it, and PPO is not involved. The ambulance still cannot
pass queued cars, and every decision is logged (`emergencyLog` in the replay). 3 junctions / 450 m was the best of six settings tried on 15 matched episodes (1/300, 2/180, 1/180, 2/300, 3/450, 4/600).

`ForecastAware` is the tuned queue heuristic plus two bounded uses of the forecast: each stage's demand is shifted by β x (predicted queue - current queue), capped at ±6 vehicles
(β = 0.1, chosen on tuning seeds 1100-1105 under the earlier, ambulance-aware controllers), and a stage whose exit approach is predicted to reach 0.7 x capacity is treated as blocked.

Five arms were each run on 60 paired episodes (5 scenarios x seeds 3100-3111, never used for training or tuning), with identical demand per pair (checked by hash).

**Forecast alone** (`queue_heuristic` -> `ml_forecast` = forecast-assisted; neither gives ambulances priority):

| Metric | Left | Right | Mean Δ | 95% CI |
|---|---|---|---|---|
| Mean wait, all vehicles except ambulances (s) | 105.3 | 106.6 | +1.3 | [-1.9, 4.6] |
| Mean travel time (s) | 210.1 | 210.3 | +0.2 | [-3.4, 3.7] |
| Throughput (veh/h) | 2783 | 2778 | -5 | [-26, 15] |
| Mean queued vehicles | 69.4 | 71.1 | +1.7 | [-0.4, 4.0] |
| Teleports / episode | 0.87 | 1.07 | +0.20 | [-0.07, 0.48] |
| Ambulance travel time (s) | 201.7 | 209.4 | +7.7 | [1.1, 15.3] |
| Ambulance stopped at red lights (s) | 21.7 | 22.4 | +0.6 | [-2.6, 4.2] |
| Ambulance stuck behind queues (s) | 76.6 | 83.7 | +7.2 | [0.8, 14.3] |

The forecast does not reliably improve waiting, travel time, throughput or queues, and it makes ambulances slower when nobody clears their lights.

**Ambulance green corridor alone** (`queue_heuristic` -> `queue_heuristic_ev`):

| Metric | Left | Right | Mean Δ | 95% CI |
|---|---|---|---|---|
| Mean wait, all vehicles except ambulances (s) | 105.3 | 110.8 | +5.5 | [2.1, 9.6] |
| Mean travel time (s) | 210.1 | 213.7 | +3.5 | [-0.1, 7.9] |
| Throughput (veh/h) | 2783 | 2769 | -13 | [-41, 14] |
| Mean queued vehicles | 69.4 | 72.7 | +3.3 | [0.8, 6.1] |
| Teleports / episode | 0.87 | 0.93 | +0.07 | [-0.35, 0.58] |
| Ambulance travel time (s) | 201.7 | 134.2 | -67.5 | [-78.0, -57.7] |
| Ambulance stopped at red lights (s) | 21.7 | 4.8 | -16.9 | [-19.5, -14.5] |
| Ambulance stuck behind queues (s) | 76.6 | 36.0 | -40.5 | [-48.4, -33.2] |

A large, consistent win for ambulances (better in 60 of 60 pairs for travel time and for time in queues), **paid for by everyone else**: their mean wait rises by about
5.5 s and queues grow, because the held greens starve the cross streets. Ambulances still sit in queues for about 36 s on average.

**The demo pair** (`queue_heuristic` -> `ml_forecast_ev`: forecast-assisted plus the corridor):

| Metric | Left | Right | Mean Δ | 95% CI |
|---|---|---|---|---|
| Mean wait, all vehicles except ambulances (s) | 105.3 | 109.5 | +4.2 | [1.0, 7.9] |
| Mean travel time (s) | 210.1 | 211.7 | +1.5 | [-1.8, 5.3] |
| Throughput (veh/h) | 2783 | 2776 | -6 | [-33, 21] |
| Mean queued vehicles | 69.4 | 72.7 | +3.3 | [0.5, 6.3] |
| Teleports / episode | 0.87 | 1.03 | +0.17 | [-0.22, 0.53] |
| Ambulance travel time (s) | 201.7 | 139.5 | -62.2 | [-71.9, -53.0] |
| Ambulance stopped at red lights (s) | 21.7 | 6.4 | -15.3 | [-17.9, -12.9] |
| Ambulance stuck behind queues (s) | 76.6 | 40.0 | -36.6 | [-43.5, -29.8] |

The ambulance gain is the corridor rule, not the forecast. With the corridor on both sides, the forecast changes ambulance travel time by +5.3 s (CI -0.4 to 11.8), i.e. it does not help.

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
