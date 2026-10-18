# FlowState Intelligence Lab

Standalone local demo of queue forecasting and ML-assisted signal control on real SUMO runs. It is separate from the production app.

```powershell
.\run-lab.ps1        # API http://127.0.0.1:8015, UI http://localhost:5176
```

Manual start: `cd backend; ..\.venv\Scripts\python -m flowstate.lab.cli serve --port 8015` and `cd lab; npm run dev`.
Paired replays live in `data/lab/replays/` (git-ignored, about 10 MB). `run-lab.ps1` records them if they are missing
(`python -m flowstate.lab.cli record`, about 2 minutes).

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

## ML-assisted control (`artifacts/lab/comparison_eval.json`)

`ForecastAware` is the tuned queue heuristic plus two bounded uses of the forecast:

* each stage's demand is shifted by β x (predicted queue - current queue), capped at ±6 vehicles;
* a stage whose exit approach is predicted to reach 0.7 x capacity is treated as blocked.

β = 0.1 was chosen on tuning seeds 1100-1105. On those seeds it scored 100.0 s against the heuristic's 103.2 s.

**Held-out result (60 paired episodes, 5 scenarios x seeds 3100-3111): no measurable improvement.**

| Metric | Heuristic (no ML) | Heuristic + ML | Mean Δ | 95% CI |
|---|---|---|---|---|
| Mean waiting time (s) | 106.1 | 107.8 | +1.68 | [-0.79, 4.24] |
| Mean travel time (s) | 209.7 | 210.9 | +1.18 | [-1.56, 4.25] |
| Throughput (veh/h) | 2786 | 2787 | +0.3 | [-17.7, 16.9] |
| Mean queued vehicles | 69.9 | 72.0 | +2.13 | [0.20, 4.20] (worse) |
| CO2 estimate (kg) | 593.7 | 601.5 | +7.9 | [-0.9, 16.8] |
| Teleports / episode | 0.98 | 1.22 | +0.23 | [-0.05, 0.57] |
| Collisions | 0 | 0 | 0 | |

The forecast changed 2,597 of 38,377 eligible decisions (6.8%), and 2,576 of those were longer holds for predicted spillback.
Individual seeds go either way. For example, rush-ramp seed 3100 is 17 s better with ML. The UI always shows the multi-seed result next to a single replay.

For comparison, the heuristic against fixed-time on the same episodes: mean wait -2.66 s, CI [-6.76, 1.74], which is not clear.
The 95th-percentile travel time is -27.0 s, CI [-45.5, -8.8], and emergency-vehicle stopped time is -43.8 s, CI [-52.4, -35.8].

## Performance (this machine, 8 logical cores, shared)

Each SUMO step (1 s simulated) takes about 1-3 ms. Each TrafficState observation takes about 0.5 ms, and each ML decision about 4-8 ms.
A WebSocket frame send takes about 0.2-0.4 ms. A whole 1,500 s paired episode takes about 10 s of wall time per side.

## Limitations

* The demo uses the small 2x2 grid, not the Toronto district network.
* No Gemini key is configured locally, and the Gemini REST client (`flowstate.experiments.assistant`) is on `task/integrations`, not on this branch.
  The lab therefore answers from measurements until both are present. The Gemini path is tested with a scripted client through the real guard.
* CO2 uses HBEFA3 with an assumed vehicle-class mapping. Use it as a paired difference only, not as an absolute inventory.
