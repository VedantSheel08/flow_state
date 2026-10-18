# Results page: side-by-side replays on the downtown network

The first section of the Results tab replays recorded SUMO runs on the `district` network (the same map and renderer as the Simulation page): the left
map is main's **fixed-time plans**, the right map is the **same plans with FlowState's rules**. Both sides use the same seed, demand and events; only the
signal control differs. Nothing is simulated in the browser; the page plays back recorded positions and signal states.

## What the right side is

Two hand-written rules in `backend/flowstate/replay/`, both acting only on green *durations* (amber, all-red and pedestrian clearance are never shortened
or skipped), applied through main's `SignalManager` like transit signal priority:

* `heuristic.py` – **queue-responsive control**: the Intelligence Lab's queue heuristic ported to any network. Gap-out of empty greens, early switch when
  the red approaches hold clearly more queued vehicles, short extensions while the green side still holds more queue (min green 10 s, max 60 s).
* `priority.py` – **ambulance green priority**: extends a green an emergency unit is approaching and ends a conflicting green once it has served its
  minimum. It cannot turn a red green directly.

**No learned model runs on this network.** The lab's queue forecaster (gradient-boosted trees) was trained on the synthetic 2×2 grid and is not used
here; the PPO agent is not used anywhere in production. The page's "How to read this" says the same.

## How the numbers are made

`python -m flowstate.replay.record --scenarios ambulance,rush_hour,accident,construction --seeds 3100-3107 --out frontend/public/flowstate-demo`

For every scenario and seed it runs both arms headless (5 minutes each, main's engine, libsumo), records half-second frames for the display seed, and
computes paired statistics over all seeds: mean per arm, mean paired difference, a 95 % bootstrap interval of the difference, and better/worse run
counts. Everything shown on the page, including the worse-run counts, comes from `artifacts/replay/district-eval.json`.

The emergency unit shown first is chosen by a stated rule (queued ≥ 10 s on the fixed-time side and held ≤ 3 s on the right; otherwise the unit that
gained most) and is therefore a best case; every unit in the run is listed with its own numbers.

## Limits

* 8 paired seeds per scenario is a small sample; read the intervals before the percentages.
* Unit trip time is the call-to-scene leg only.
* The two arms differ only in the two rules above, so the comparison isolates them; it does not include SUMO's built-in actuated programs.
