# Optimization v2: forecast-aware signal control, diagnosed and re-tested

Branch `task/optimization-v2`, based on `task/ml-intelligence` (8a8d2a0). Same 2x2 lab grid, demand generator, SafeSignal layer and
trained forecaster (`artifacts/lab/forecaster.pkl`, unchanged). No retraining, and no new networks.

**Outcome: no candidate measurably beats the queue heuristic on the unseen final set. The heuristic stays the default and the
fallback.** The new controllers are exposed as experimental options (`pressure`, `ml_pressure`).

## 1. Why the previous ML controller did not help

Diagnostic: `tools/optimization/diag_holds.py` and `diag_spill.py`, development seeds 4000-4001, 8 episodes.

| Finding | Number |
|---|---|
| Extra holds caused by the demand shift `beta x (p30 - q)` | **100 %** (338 of 338) |
| Extra holds caused by predicted spillback | 0 % |
| Extra holds that kept a green with fewer than 1 vehicle on it, while about 8 waited on red | 46 % |
| Actual downstream occupancy at those holds | median 0.11, never ≥ 0.92 |
| Predicted-spillback trigger: firing rate and precision | 0.4 % of approach-decisions, 85 % precision |

* **Cause: an endogenous objective, not timing or saturation.** The forecaster predicts *halting* vehicles under the controllers that
  generated its training data. For the green stage, it predicts the queue that forms *because the light will turn red* (+). For the
  waiting stage, it predicts the discharge that follows *its upcoming green* (−). Adding both to stage demand pushes every close call
  towards "keep the current green", and it cancels gap-out.
* The previous report described the `heldLonger` count as "extending greens to avoid predicted spillback". In fact `heldLonger` counts every
  hold, and none of them came from the spillback term.
* The previous tuning picked β = 0.1 out of 19 settings using 24 episodes, where the paired noise is about ±4 s. Its −3 s tuning gain was selection noise.

## 2. Candidates (`backend/flowstate/lab/optimize.py`)

All candidates keep the heuristic's structure: `hold_s` = 20, gap-out on *measured* demand, emergency pre-emption, the blocked-exit
test, and the same SafeSignal layer.

* `pressure@margin,gamma`: deterministic, downstream-aware max-pressure. Stage pressure = Σ(queue + 0.25 × moving) − γ × the mean queue
  on the approaches its movements discharge into (straight, left and right; exits count as 0). With γ = 0 it is exactly the heuristic,
  and a test checks this.
* `ml_pressure@margin,gamma,fc_down,beta`: the same controller plus only the forecast terms that do not depend on our own next action:
  * `fc_down`: the downstream queue becomes max(now, p30);
  * `beta`: anticipated arrivals on the **waiting** stage, β × max(0, p30 − q), capped at 4 vehicles.

  The green stage never gets a forecast term, so the forecast can only bring a switch forward. It can never hold an emptying green (tested).

## 3. Protocol

All runs use the same SUMO demand per (scenario, seed): the route-file SHA is checked for every pair.

| Set | Seeds × scenarios | Use |
|---|---|---|
| Development | 4000-4015 × 4 training scenarios (64 pairs) | Search and selection |
| **Final** (new, used once) | 5000-5011 × all 5 scenarios (60 pairs), plus 5100-5103 × 5 scenarios at intensities **0.8 and 1.3**, levels never used anywhere (40 pairs) | Final verdict |
| Previous held-out | 3100-3111 × 5 scenarios | Run once after selection, as a reference only |

* The selection rule (`tools/optimization/select_finalists.py`) was written before the extended development results were seen. It
  requires zero collisions, zero SafeSignal violations, no more than +10 % teleports and no more than −0.5 % throughput, then takes the lowest paired mean wait.
* Cost of the whole study: 1,188 episodes, about 72 minutes of wall time on 6 workers.
* This harness reproduces the previous evaluation's heuristic numbers exactly (60/60 episodes, max |Δ wait| = 0).

## 4. Results

Development search: 14 controllers (13 candidates plus the reference), 32 pairs each (paired Δ mean wait vs heuristic):

* `pressure@3/6/10/14` × γ 0.5/1: from +2.3 s to −2.3 s, all CIs crossing 0.
* The heuristic with margin 10 alone: +0.6 s. So the pressure term, not the margin, carried the gain.
* `fc_down` (forecast downstream): +2.2 s, worse than the same controller without it (−2.3 s), so the downstream forecast was dropped.
* Old `ml_forecast`: +0.7 s.

Selected on 64 development pairs:

| Pick | Controller | Δ wait | 95 % CI |
|---|---|---|---|
| Deterministic | `pressure` = `pressure@10,0.5` | −2.1 s | [−4.8, +0.7] |
| ML | `ml_pressure` = `ml_pressure@10,0.5,0,1` | −2.5 s | [−5.3, +0.3] |

**Final test (unseen), paired vs the queue heuristic** (`artifacts/lab/optimization_v2_report.json`):

| Set (pairs) | Metric | Heuristic | `pressure` | `ml_pressure` | old `ml_forecast` |
|---|---|---|---|---|---|
| **All (100)** | mean wait (s) | 98.8 | +0.30 [−1.9, +2.6] | **−0.10 [−2.3, +2.1]** | +0.49 [−1.6, +2.5] |
| | mean travel (s) | 197.9 | −0.8 [−3.2, +1.7] | −1.3 [−3.8, +1.2] | +0.0 |
| | throughput | 2686 veh/h | −0.3 % | −0.1 % | −0.1 % |
| | mean queued (veh) | 66.9 | +1.4 [−0.3, +3.2] | +1.1 [−0.8, +3.0] | +0.6 |
| | teleports (total) | 141 | 140 | 128 | 146 |
| Standard intensities (60) | mean wait | 95.3 | −1.6 [−4.2, +0.8] | −1.1 [−3.2, +1.1] | +0.4 |
| | mean travel | | **−2.9 [−5.8, −0.1]** | −2.2 [−4.5, +0.3] | −0.1 |
| | p95 travel | | −17 s | −15 s | −8 s |
| Light, 0.8× (20) | mean wait | 44.7 | +1.1 [−0.3, +2.4] | +1.1 [−0.4, +2.4] | +1.5 |
| Extreme, 1.3× (20) | mean wait | 163.3 | +5.4 [−2.3, +13.2] | +1.7 [−6.9, +10.5] | −0.3 |
| Previous held-out (60) | mean wait | 106.1 | −2.4 [−6.0, +1.0] | −1.4 [−4.1, +1.4] | +1.7 (as reported before) |
| | teleports | 59 | 73 | 58 | 73 |

Safety across every run, development, final and reference (1,188 episodes): **0 collisions, and 0 SafeSignal violations** (audited from the
signal history: no green below the minimum or above the maximum, and no short yellow or all-red).

ML influence on the final set:
* `ml_pressure` changed 50 of 61,766 decisions (0.08 %), and all of them were earlier switches.
* The old `ml_forecast` changed 4,266 (6.9 %), and 4,242 of those were holds.

## 5. Interpretation

* Downstream-aware pressure helps in the **standard band** (1.0 / 1.18 base intensity). There it is the only result with a CI that
  excludes zero (travel time −2.9 s), and p95 travel improves by 15-17 s. It **hurts in light traffic and in deeper saturation**, so overall
  it is a wash. One fixed margin does not transfer across demand levels. A demand-dependent margin could fix that, but it would have to
  be designed and selected on new development data and confirmed on a *new* final set. These final seeds are now spent.
* Once the self-referential parts are removed, the forecast has almost nothing left to contribute in this decision structure: anticipated
  arrivals on red approaches rarely tip a decision. The downstream forecast hurts, because it over-penalises stages whose exits are about to
  be served by the neighbour anyway. The forecaster's accuracy (MAE 2.23 vs 4.38) is real, but predicted halting counts are the wrong
  decision variable. A forecast of *arrivals* (exogenous to our signal) would be the right target if this is revisited. That means retraining, which was out of scope.

## 6. Cost (CPU, per 5 s decision, single process on an idle machine; `artifacts/lab/optimization_v2/latency_isolated.json`)

| Controller | Observation | Inference | Decision | Total (mean / p95) |
|---|---|---|---|---|
| queue_heuristic | 0.44 ms | none | 0.10 ms | 0.53 / 0.98 ms |
| pressure | 0.45 ms | none | 0.13 ms | 0.57 / 1.10 ms |
| ml_pressure | 0.43 ms | 4.4 ms | 0.28 ms | 5.2 / 7.6 ms |

* A SUMO step takes about 2.3 ms. No candidate needs a SUMO clone.
* With 6 parallel workers the means rise to about 3 ms for `pressure` and about 28 ms for `ml_pressure`, because of contention for the 8 cores.

## 7. Integration

* The default controller is unchanged: `queue_heuristic` is the verified controller and the fallback.
* The new ids go through the existing API:
  * `flowstate.lab.control.make_controller("pressure" | "ml_pressure", sim)`;
  * `CONTROLLERS` in `/api/lab/status`;
  * `POST /api/lab/replays {left, right}`;
  * the live websocket `start {controller}`.
* Bare ids load the selected parameters from `artifacts/lab/optimization_v2_selection.json`. Explicit ids (`pressure@10,0.5`, `ml_pressure@10,0.5,0,1`) are accepted too.
* `ml_pressure` without a trained forecaster degrades to `queue_heuristic` and reports `fallback: true`.
* The UI (Codex) lists controllers from `lab/src/lib/types.ts` (`CONTROLLER_LABEL`). Add `pressure` and `ml_pressure` there to make them
  selectable, and label them experimental: neither improves on the heuristic overall.

Reproduce:

```
python tools/optimization/bench.py run dev --ids "queue_heuristic;pressure@10,0.5"   # cached, resumable
python tools/optimization/select_finalists.py
python tools/optimization/bench.py run final --ids "queue_heuristic;ml_forecast;pressure;ml_pressure"
python tools/optimization/report.py
```

Use an interpreter with numpy and scikit-learn 1.9 (the ML worktree's `.venv`). The main `.venv` has neither.
