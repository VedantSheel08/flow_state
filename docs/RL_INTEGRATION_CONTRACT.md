# RL signal-control backend contract

Everything is **additive**: a new router under `/api/rl`. No existing FlowState route, payload or WebSocket message
changes. Mounted from `backend/flowstate/api/app.py` by a guarded three-line include (see "Mounting").

All experiments run on the small RL network (`rl_grid`: a 2 x 2 grid of signalised junctions, see
`GET /api/rl/info`). They do **not** run on the district network; the dashboard should label results accordingly
(`caveat` is returned in every comparison result).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/rl/info` | network, scenarios, controllers (with availability), safety limits, decision interval |
| GET | `/api/rl/controllers` | just the controller list |
| GET | `/api/rl/model` | model card: training record, publication gate, stored evaluation of the trained policy |
| POST | `/api/rl/compare` | start a controlled comparison; returns `202` and a job |
| GET | `/api/rl/compare/{job_id}` | poll the job; `result` appears when `status == "done"` |

Errors: `422` with a message for an invalid request (unknown controller or scenario, too many episodes, baseline not
in the list), `404` for an unknown job. Jobs are in memory and are lost on a server restart.

## Controllers

| id | what it is | learned | `status` |
|---|---|---|---|
| `fixed_time` | conventional pre-timed plan, 30 s per stage | no | `baseline` |
| `queue_heuristic` | queue-responsive actuated-style rule (parameters tuned on training seeds) | no | `verified` |
| `rl_ppo` | the trained PPO policy | yes | `verified` only if it passed the gate, otherwise `experimental` |

`fixed_time_tuned` appears only if tuning found a green length other than 30 s.

**Current state of this branch: the production adaptive controller is `queue_heuristic`; `rl_ppo` is `experimental` and
unavailable.** Four PPO runs were made and the two candidates that finished both failed the publication gate (details and
held-out numbers in `docs/RL_REPORT.md`). `GET /api/rl/info` returns `"default_controller": "queue_heuristic"`; a UI should
preselect that id, and must read `default_controller` rather than hard-coding it, because it changes to `rl_ppo` only when a
policy has passed the gate.

`rl_ppo` is **offered only if all three hold**: `artifacts/rl/ppo_policy.zip` exists, `artifacts/rl/validation.json` says
`passed: true`, and `validation.json`'s `checkpoint_sha256` equals the SHA-256 of that very `ppo_policy.zip` (so a file that
was swapped, edited or placed by hand is refused). Otherwise `available` is `false` with a `note`, and **requesting it still
works**: the run uses the verified heuristic instead and says so in `fallbacks` (see below). The UI must show that, not
pretend the RL policy ran. `validate-policy` writes `ppo_policy.zip` and `validation.json` **only when the gate passes**;
every verdict, pass or fail, is kept in `artifacts/rl/validation_records/`.

Only catalog ids are accepted by the API (`422` otherwise). The explicit offline ids `rl_ppo@<path>`,
`queue_heuristic@m,h,w` and `fixed_time@<s>` used by the CLI for tuning and candidate evaluation are rejected by
`POST /api/rl/compare`, so the API can never load an arbitrary checkpoint (loading a policy unpickles it).

All strategies act through the same safety layer: minimum green (14 s, includes pedestrian walk and clearance),
yellow 4 s, all-red 2 s, maximum green 60 s, decisions every 5 s. No controller can bypass these.

## `GET /api/rl/info`

```jsonc
{
  "network": {"id": "rl_grid", "junctions": ["A0","A1","B0","B1"], "description": "..."},
  "scenarios": [{"id": "balanced", "title": "Balanced", "description": "...", "held_out": false, "trained_on": true}, ...],
  "intensity": {"standard": 1.0, "stress": 1.18, "range": [0.3, 1.6]},
  "controllers": [{"id": "queue_heuristic", "title": "...", "description": "...", "learned": false, "available": true, "status": "verified"},
                  {"id": "rl_ppo", "title": "...", "description": "...", "learned": true, "available": false, "status": "experimental",
                   "note": "validation failed (...); falling back to the queue-responsive heuristic"}, ...],
  "default_controller": "queue_heuristic",   // preselect this; "rl_ppo" only after a policy passed the gate
  "safety": {"yellow_s": 4.0, "all_red_s": 2.0, "ped_clearance_s": 7, "min_green_s": 14.0, "max_green_s": 60.0, "decision_s": 5.0},
  "decision": {"interval_s": 5.0, "actions": ["hold (extend green)", "request next stage"], "note": "..."}
}
```

## `POST /api/rl/compare`

Request (every field optional; defaults shown):

```jsonc
{
  "controllers": ["fixed_time", "queue_heuristic", "rl_ppo"],  // 2-5 ids from /api/rl/controllers
  "scenarios": ["balanced"],                                    // 1-5 ids from /api/rl/info
  "seeds": [3000, 3001, 3002],                                  // 1-10 demand seeds; same seed => same vehicles for every controller
  "intensity": 1.0,                                             // 0.3-1.6 demand multiplier
  "horizon_s": 900,                                             // 300-1800 s of demand; runs then continue until every vehicle has arrived
  "baseline": "fixed_time",                                     // paired statistics are computed against this controller
  "include_series": true                                        // time series for the first seed of each scenario
}
```

Limit: `len(controllers) * len(scenarios) * len(seeds) <= 60` episodes. A 3-controller, 1-scenario, 3-seed comparison
takes roughly half a minute on a laptop.

Response `202`:

```jsonc
{"id": "9f2c1a7b3d", "status": "queued", "progress": {"done": 0, "total": 9}, "request": {...},
 "created": 1760678400.1, "result": null, "error": null}
```

## `GET /api/rl/compare/{job_id}`

`status` is `queued`, `running`, `done` or `failed`. `progress.done / progress.total` counts finished episodes.
On `failed`, `error` holds the last lines of the worker output.

When `status == "done"`, `result` is:

```jsonc
{
  "request": {...},                       // as validated
  "titles": {"fixed_time": "Fixed-time (30 s greens)", ...},
  "fallbacks": [                          // EMPTY when everything ran as requested
    {"requested": "rl_ppo", "used": "queue_heuristic", "fallback": true, "reason": "..."}
  ],
  "demand": {"identical_across_controllers": true, "hashes": {"balanced/3000": "9e734107eb780b39", ...}},
  "summary": {
    "baseline": "fixed_time", "episodes": 9, "scenarios": ["balanced"],
    "overall": {                          // also "per_scenario": {"balanced": {...}}, same shape
      "rl_ppo": {"n": 3, "metrics": {
        "mean_wait_s": {"mean": 61.2, "std": 4.1, "min": 56.0, "max": 66.3,
                        "vs_baseline": {"mean_diff": -8.4, "ci95": [-12.0, -4.6], "pct_change": -12.1,
                                        "wilcoxon_p": null, "better_in": 3, "worse_in": 0, "paired": 3}},
        "mean_travel_s": {...}, "p95_travel_s": {...}, "throughput_vph": {...}, "completed_fraction": {...},
        "queue_mean_veh": {...}, "queue_max_veh": {...}, "spillback_pct": {...},
        "teleports": {...}, "collisions": {...}, "emergency_mean_stopped_s": {...}}},
      "fixed_time": {"n": 3, "metrics": {...}},   // no vs_baseline for the baseline itself
      "queue_heuristic": {...}
    }
  },
  "episodes": [                           // one entry per (controller, scenario, seed)
    {"requested_controller": "rl_ppo", "controller_used": "rl_ppo", "fallback": false, "scenario": "balanced", "seed": 3000,
     "mean_wait_s": 61.2, "mean_travel_s": 160.4, "...": "all summary metrics plus vehicles_scheduled/completed/unfinished, signal_switches, requests_refused, forced_changes, demand_sha, wall_s",
     "series": {"columns": ["t_s","queued_vehicles","mean_wait_s","vehicles_in_network"], "rows": [[0,0,0,0], [10,3,1.2,18], ...]}}  // series only for the first seed per scenario
  ],
  "wall_seconds": 31.4,
  "caveat": "Results come from SUMO runs of the 2 x 2 RL grid, not from the FlowState district network."
}
```

Metric meanings (all from SUMO's own per-vehicle bookkeeping, independent of the reward):

| key | meaning |
|---|---|
| `mean_wait_s` | seconds a vehicle spent standing still, plus the delay before it could enter the network |
| `mean_travel_s`, `p95_travel_s` | arrival time minus the *scheduled* departure time |
| `throughput_vph` | vehicles arrived within the 900 s demand window, per hour |
| `completed_fraction` | completed / scheduled after the drain period (unfinished vehicles are counted, not dropped) |
| `queue_mean_veh` | mean stopped vehicles per approach |  
| `queue_max_veh` | peak stopped vehicles on all approaches together |
| `spillback_pct` | share of internal-street seconds in which a queue covered at least half the block |
| `teleports`, `collisions` | SUMO teleports; distinct collision pairs |
| `emergency_mean_stopped_s` | seconds an emergency vehicle stood still (0 if none in the demand) |

Lower is better for everything except `throughput_vph` and `completed_fraction`. `vs_baseline.better_in` / `worse_in`
already account for direction. `ci95` is a bootstrap interval of the paired mean difference (RL minus baseline); a
dashboard should show "no clear difference" when it spans 0.

## Suggested UI flow

1. `GET /api/rl/info` once: build the controller and scenario pickers; grey out controllers with `available: false`
   and show `note`.
2. `POST /api/rl/compare`, then poll `GET /api/rl/compare/{id}` every 1-2 s, showing `progress`.
3. Render `summary.overall` as the headline table; `episodes[].series` as small line charts (queued vehicles over time)
   for each controller; `fallbacks` as a visible banner.
4. `GET /api/rl/model` for a "how was this trained and tested" panel (stored numbers, no recomputation).

## Mounting and dependencies

`app.py` mounts the router inside `try/except ImportError`, so FlowState still starts without the RL packages. The
router itself needs only FastAPI and pydantic; the experiments need `numpy`, `gymnasium`, `stable-baselines3`, `torch`
and `scipy` (`backend/requirements-rl.txt`) in the interpreter that runs the worker. Set `FLOWSTATE_RL_PYTHON` to that
interpreter if the API server uses a different one. Experiments run in a child process (libsumo allows one simulation
per process), so a running FlowState simulation is never disturbed.

## Observation, action and policy loading (for anyone embedding a controller)

A controller is anything with `decide(sim) -> [0|1] * 4` (hold / request the next stage, one entry per junction in the
order `A0, A1, B0, B1`), called once per 5 s decision interval. Requests go to `SignalSim.request(jid)`, which forwards them
to that junction's `SafeSignal`; the controller never writes signal states. Refused requests are counted
(`requests_refused` in every episode result), never executed.

Two observation layouts exist, selected automatically by `PolicyController` from the loaded model's observation space:

| version | shape | used by | built by |
|---|---|---|---|
| v1 | `(76,)` joint vector, 19 values per junction, range 0..1 | `run1`-`run3` (joint action `MultiDiscrete([2]*4)`) | `flowstate.rl.env.build_observation` |
| v2 | `(33,)` **per junction**, range -1..1; the same policy is called on a `(4, 33)` batch, action `Discrete(2)` | `run4` (per-junction agents) | `flowstate.rl.env.build_agent_observations` |

v2 per-junction block: 4 approaches x (queue/capacity, mean wait/60 s, downstream occupancy, emergency present) = 16; signal = stage
one-hot (2), in-transition, green elapsed / 60 s, transition time left, *can-switch* (minimum green reached) = 6; demand = demand of the
current stage, demand of the next stage, their difference, exit blocked (current, next), emergency (current, next) = 7; context = the two
neighbouring junctions' queue ratios, network queue ratio, network waiting ratio = 4. "Demand" is queued plus 0.25 x approaching vehicles
on the approaches a stage serves, the same quantity the heuristic uses.

Loading: `PolicyController("artifacts/rl/ppo_policy.zip")` calls `stable_baselines3.PPO.load(..., device="cpu")`; the file is a
pickle-based archive, so load only files that `rl_status()` accepted. Observations are raw (no normalisation wrapper), so nothing
besides the zip is needed. The shared-network class `flowstate.rl.policy.SharedJunctionPolicy` must be importable to load `run3`
checkpoints.

`SafeSignal` guarantees, for every controller: minimum green 14 s (pedestrian walk + clearance), full 4 s yellow, full 2 s all-red,
forced change at 60 s. The environment only ever calls it through `request_switch` and `tick`.

## Promoting a policy (the only way `rl_ppo` becomes available)

1. Train: `python -m flowstate.rl.cli train ...` (configurations in `artifacts/rl/RUNS.md`); select the checkpoint on selection seeds
   2000-2003 only (`validation.jsonl`).
2. Gate: `python -m flowstate.rl.cli validate-policy <checkpoint.zip>` evaluates it against fixed-time and the heuristic on gate seeds
   2004-2015. It passes only with mean waiting time not worse than the better baseline and no more teleports or collisions. On a pass it
   publishes `ppo_policy.zip` and `validation.json` (with the checkpoint's SHA-256); on a fail it publishes nothing.
3. Held-out test, once, on seeds 3000+: `python -m flowstate.rl.cli evaluate ...`, then `python -m flowstate.rl.report`. If the test
   result contradicts the gate, move `ppo_policy.zip` and `validation.json` out of `artifacts/rl/` (do not leave them in place).
4. Copy the run's `training_run.json` to `artifacts/rl/training_run.json` so `GET /api/rl/model` can show how it was trained.

## Artifacts and storage

`python -m flowstate.rl.manifest` writes `artifacts/rl/MANIFEST.json` (path, size, SHA-256, whether git carries it) and
`--verify` checks the files on disk against it. Committed: best-validation checkpoints (about 0.7 MB each), training and validation
logs, tuning results, gate records, evaluation JSON and diagnostics. **Not committed** (`.gitignore`): `artifacts/rl/run*/checkpoints/`
(intermediate checkpoints, about 16 MB in total) and raw trainer stdout. Keep those in external storage (a release asset or a shared
drive), restore them to the same relative paths, and check them with `--verify`. `artifacts/rl/rejected/run3/` holds a checkpoint
that was copied to the publication path by an earlier version of `validate-policy` (a bug, now fixed); it is kept for the record, was
never offered by the API, and must not be copied back.

## Command line (same code, no API)

Run from the repository root with `PYTHONPATH=backend` and the RL environment (`backend/requirements-rl.txt`, plus the SUMO python packages the
backend already uses).

```
python -m flowstate.rl.cli tune-fixed                          # best uniform green on training seeds
python -m flowstate.rl.cli tune-heuristic                      # heuristic margin / hold on training seeds
python -m flowstate.rl.cli train --seed 4 --steps 1000000 --envs 6 --validate-every 100000 --val-seeds 4        --agent --blend 0.5 --norm-reward --gamma 0.97 --n-steps 180 --batch-size 540 --ent-coef 0.005 --out artifacts/rl/run4
python -m flowstate.rl.cli validate-policy artifacts/rl/run4/best_validation.zip     # gate; publishes only on a pass
python -m flowstate.rl.cli evaluate --controllers fixed_time queue_heuristic rl_ppo@artifacts/rl/run3/best_validation.zip        --seeds 10 --seed-start 3000 --intensity 1.0 --out artifacts/rl/evaluation_standard.json     # held-out seeds: once, after selection
python -m flowstate.rl.report                                  # regenerate docs/RL_REPORT.md from the artifacts
python -m flowstate.rl.manifest                                # regenerate / --verify artifacts/rl/MANIFEST.json
```
