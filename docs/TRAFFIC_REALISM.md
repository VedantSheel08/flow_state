# Traffic realism, safety and congestion engineering

Branch `task/traffic-realism`. Every number below is measured from real SUMO 1.28 (libsumo) runs on this network; the reports are in
`reports/` and every run is reproducible from `condition + seed` (see "Reproduce"). Nothing is calibrated to Toronto counts: the demand is
a modelled district-wide request, and the criteria and results describe this network only.

**Evidence files.** Committed raw reports: `reports/final/` (3 presets x 3 seeds), `reports/recovery/` (relief timing), `reports/perf/` (live cost), `reports/diag/` (per-event
collision and teleport forensics). The 99 exploratory runs behind findings 1-8 and the rejected experiments are summarised row-by-row in `reports/experiments-summary.json`
(`network` = `pre-rebuild` or `post-rebuild`; regenerate raw files with the condition ids). Paths such as `reports/eval/...` below name those sweeps.

## 1. What was wrong, and the evidence

The inherited presets did not meet their own acceptance contract (`docs/CONGESTION_ACCEPTANCE.md`) and every dense run had native
collisions and teleports. A diagnostic harness (`tools/safety_diag.py`) records, for every native collision and every teleport, SUMO's own
collision objects (colliders, speeds, junction lane), a snapshot of each stalled vehicle the step before it teleports (lane, leader, signal,
route), and SUMO's own teleport reason. Findings, in the order they were established:

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 1 | **Teleports are SUMO's 240 s deadlock rescue, firing on real stalls.** All 167 teleports of `exp_pm_10` fired at the 240 s timer; 166 of 167 were not inside a junction; SUMO's reasons: yield 114, jam 44, wrong lane 9. | `reports/diag/exp_pm_10-42.json` | not hidden: counted and reported; see 3-5 |
| 2 | **Teleports are not a keep-the-box-clear artifact.** The box guard *doubled* them (167 -> 379-398) and left collisions at 4-8. | `exp_pm_10_g` x 3 seeds | guard kept (fewer collisions in jams) but not claimed as a teleport fix |
| 3 | **Unbounded over-saturation locks the district into a ring deadlock.** With the rescue off, `exp_pm_10` offered ~12,800 trips/h to a network discharging ~4,000/h: active vehicles grew to 1,381, arrivals fell from 65/min to 0-2/min, 100 % halted; relieving the exit restriction did not help because the blockage is a ring of full lanes inside the district. | `reports/eval/rec_pm_10-*.json` | demand metering: the district is held at a declared accumulation, the surplus waits in the explicit, counted external queue (`ADMISSION="accumulation"`) |
| 4 | **The permissive-left program deadlocked the junction box in every run longer than 600 s.** The protected-left `safe` program was selected only when `demand.dense` (rush hour, <= 600 s). With only 450 held vehicles (density 35) program `0` locked completely (arrivals 0/min, 51 collisions); with `safe` the same run kept 59-64 arrivals/min and 9 collisions. `rec_pm_10_safe` and `gl_750_o_safe` showed the same 3-5x collision reduction. Removing every pedestrian from the deadlocked junction changed nothing, so pedestrians were not the cause. | `reports/eval/gl_450*`, `gl_750_o*`, `rec_pm_10*` | `RunConfig.signal_program` (explicit); the jam presets force `safe` |
| 5 | **Cyclists turned left out of the kerb-side bike lane across live car lanes**, producing repeated collisions on the same internal lanes (`:n1_0_7` accounted for 4 of 9 collisions of one run). | `reports/diag/before-rush-*.json` | `network/city.py::_no_bike_left`: 10 left-turn connections from bicycle-only lanes removed (cyclists turn left in two stages). Same seeds: collisions 9 -> 5, 7 -> 3, 17 -> 4. Signal programs re-audited (below). |
| 6 | **A route SUMO accepted for planning was refused by `vehicle.add`** (lanes closed after it was planned). The exception ended the whole run. | `exp_out_10-*` crashed | `Engine.add_vehicle` returns a bool, counts `rejected_adds`, and the demand ledger records the vehicle as `dropped`. Ledger residual is 0 in every run in `reports/final`. |
| 7 | **Throughput falls as accumulation rises past the critical level** (a macroscopic fundamental diagram): held at ~450 -> 60 arrivals/min; ~900 -> 35; ~1,060 -> 17; ~1,240 -> 0. | `gl_*`, `ga_*` | the three presets sit on different points of this curve |
| 8 | **The district's storage ceiling is ~1,416 motor vehicles (82-86 veh/km/lane)**: held targets of 1,600 and 2,000 gave identical runs because the entry lanes were full. | `ga_1600_g`, `ga_2000_g` | gridlock target = 1,400; criteria gap documented |

Tried and rejected (no benefit measured, not adopted): `jmTimegapMinor` 3 s for cars (16 collisions vs 16 for the control over 4 seeds),
disabling pedestrian squeezing on crossings (identical to the control). Not changed: collision detection, the teleport timer for rush/severe,
speed modes or any vehicle position or speed.

### Signal clearance audit

`tools/signal_audit.py` (re-run after the network rebuild) over all 22 signal programs (`0` and `safe`) on both networks: yellow 3.0-4.0 s,
all-red 2.0-2.5 s, **0 vehicle links change green -> red without a yellow** (the 88 raw hits are pedestrian crossing links, which show WALK
without an amber by design). `tests/test_signals.py` passes. Emergency pre-emption never skips a clearance interval.

## 2. The three presets

Selected through the existing API: `POST /api/runs {"scenario": "rush_hour", "congestion": "<id>"}`; ids and labels are unchanged
(`rush_hour`, `severe`, `gridlock_stress`; `GET /api/congestion-presets` now reports `engine: true` and the warm-up). The options live in
`performance/congestion.py::ENGINE_OPTIONS` (single source for the API and for `evaluation/conditions.py`).

| Preset | Demand | Held at | Other |
|---|---|---|---|
| `rush_hour` | inbound (AM) peak, 3,200 x 1.6 x peak 2.5 | 600 (saturates near 450: insertion-limited) | safe program, box guard |
| `severe` | inbound peak 2,400 x 1.6 x 2.5 + 80 % of district-bound trips circle for parking (2-5 blocks first) | 800 | safe program, box guard |
| `gridlock_stress` | outbound (PM) peak 3,200 x 1.6 x 2.5 | 1,400 (the storage ceiling) | safe program, box guard, downstream restriction (one exit lane, 10 km/h), **SUMO deadlock rescue off and disclosed** |

Warm-ups (the time to reach the held accumulation): 600 s, 600 s, 1,000 s. Start the demo from a **prepared run**
(`performance/prepared.py::PreparedRun(..., engine=engine_options(preset))`): the warmed engine process is paused at t=0 with its vehicles,
routes, signals and RNGs intact, so no state is restored from a snapshot (nothing to verify beyond process identity, which `identity()` hashes
including the engine options).

**Recovery.** `POST /api/runs/current/control {"action": "relieve"}` restores the exit lanes and speed limits and meters the held
accumulation down to 40 %. Vehicles are never moved or removed; the queue discharges under the normal driving model.

## 3. Final results (seeds 7, 42, 99; `reports/final/*.json`)

Window: 300 s after warm-up, interior arterials. Free-flow reference: the 50 km/h posted limit. "Travel p50" is the median per-vehicle average speed through the arterials.

| | rush_hour | severe | gridlock_stress |
|---|---|---|---|
| Density, veh/km/lane (mean) | 37.9 / 39.4 / 40.1 | 46.3 / 47.1 / 43.5 | 57.0 / 60.9 / 52.5 (peak 62-69) |
| Vehicles in district | 445-457 | 583-600 | 785-869 in window, up to 1,074 |
| Halted share | 0.67 / 0.66 / 0.77 | 0.83 / 0.77 / 0.82 | 0.98 / 0.86 / 0.86 |
| Travel p50, km/h (x slower than 50) | 4.5 / 3.4 / 3.8 (11-15x) | 1.7 / 3.3 / 2.5 (15-29x) | 0.3 / 1.0 / 1.0 (50-170x) |
| p90 instantaneous speed, km/h | 15.9 / 12.0 / 11.2 | 3.0 / 7.5 / 5.0 | 0.0 / 1.1 / 1.8 |
| Network throughput, trips/h | 4,152 / 3,696 / 3,732 | 912 / 1,140 / 1,104 | 576 / 4,152 / 2,844 (see recovery) |
| Queued approaches (>= 8 veh, >= 50 m) | 10 / 10 / 10 | 13 / 14 / 13 | 12 / 16 / 15 |
| Stop-and-go vehicles | 249 / 209 / 163 | 146 / 212 / 163 | 29 / 333 / 262 |
| Corridor spillback, s of 300 | 0 / 21 / 78 | 91 / 114 / 121 | 150 / 194 / 179 |
| Native collisions | 5 / 2 / 3 | 1 / 7 / 3 | 19 / 11 / 4 |
| Teleports | 0 / 4 / 18 | 79 / 61 / 98 | 0 / 0 / 0 (rescue off) |
| Ledger: dropped / residual | 0 / 0 | 0 / 0 | 0 / 0 |
| Criteria v1 achieved tier | none | none | none |

Reading: the three are numerically distinct (halted 66-77 % -> 77-83 % -> 86-98 %; throughput ~3,700/h -> ~1,100/h -> collapse; travel speed 11-15x ->
15-29x -> 50-170x slower than free flow), and `severe` throughput is about a third of `rush_hour`'s at the same entry demand. `rush_hour` is bumper-to-bumper
stop-and-go (10 queued approaches, 160-250 stop-and-go vehicles), but it **fails the v1 rush gates in all three seeds**: density 37.9-40.1 is just under the
40 veh/km/lane minimum, storage (0.28-0.30) and body occupancy are just under their minima, and its travel p50 (3.4-4.5 km/h) is *below* the 8-20 km/h rush band, i.e.
it is already slower than the criteria's rush. Failed gates (criteria v1), per seed 7 / 42 / 99:

- rush: density, storage, bodyOccupancy, medianSpeed, collisions, steadyAtStart in all three; teleports additionally for seeds 42 and 99.
- severe: density, storage, bodyOccupancy, collisions, teleports, steadyAtStart in all three; corridorSpillback additionally for seeds 7 and 42 (91 and 114 s, under the 120 s minimum).
- gridlock: density, storage, bodyOccupancy, blockedCycle, legalGreensBlocked, componentOutflow, collisions, steadyAtStart in all three; medianSpeed additionally for seeds 42 and 99.

No preset meets criteria v1 (`docs/CONGESTION_ACCEPTANCE.md` explains the density-ceiling gap and why the gates were not lowered). Requests held outside the district
(counted, never discarded; mean / p90 wait before release): rush 125-136 s / 360-400 s, severe 51-89 s / 155-316 s, gridlock ~1 s.

### Gridlock: plateau, coverage and recovery (`reports/final`, `reports/recovery`)

- Plateau: 17-22 of 48 blocks (35-46 %) were >= 75 % queued for at least 120 s of the 300 s window; the district stayed >= 85 % halted at density >= 50 for
  1,105 / 748 / 682 cumulative seconds (seeds 7 / 42 / 99). No blocked directed cycle through >= 4 junctions was detected (`cyc` 0 s).
- Seed 7 locks permanently (arrivals 0-4/min, 100 % halted, 1,070 vehicles) and **does not recover even when relieved at t=0**; the ring forms during warm-up.
- Seeds 42 and 99 recover when relieved early:

| Relief at | seed 42 arrivals/min after relief | seed 42 halted | seed 99 arrivals/min | seed 99 halted | seed 7 |
|---|---|---|---|---|---|
| t=0 s | 67 -> 62 -> 56 -> 48 -> 51 | 0.78 -> 0.67 | 52 -> 48 -> 56 -> 61 -> 56 | 0.79 -> 0.69 | 15 -> 0.5 -> 4 -> 0 (locked) |
| t=120 s | 60 -> 48 -> 70 -> 57 -> 65 | 0.83 -> 0.64 | 40 -> 48 -> 49 -> 53 | 0.74 -> 0.68 | 8 -> 2.5 -> 0 (locked) |
| t=300 s (shipped) | 27 -> 24 -> 17 -> 19 -> 15 | 0.89 -> 0.90 | 20 -> 15 -> 11 -> 9 | 0.86 -> 0.92 | locked |

  Accumulation falls from ~700-800 to ~548 vehicles within 4-6 minutes in the recovering runs. Relief at 300 s, after the jam has been standing for
  several minutes, drains only slowly (active 1,018 -> 906 in 10 minutes for seed 42): **recovery is reliable only if the ring has not closed, so press
  `relieve` early.** The shipped `gridlock` condition keeps the relief at t=300 s on purpose so the report shows the late-relief behaviour.
- A signal policy cannot do this: the restriction is downstream and the metering is a demand change. Any claim about optimisation must be judged on throughput and delay, not on clearing the jam.

## 4. Performance (one live simulation, idle machine, no evaluation collector; `tools/live_perf.py`, `reports/perf/`)

Production budget: 6 cores / 10 GB for the whole service; one simulation uses one core (libsumo is single-threaded).

| Preset (seed 42) | Engine step mean / p99 / max | Real-time factor | Frame build mean | Frame JSON (gzip-1) at peak vehicles | Cores | Peak RSS |
|---|---|---|---|---|---|---|
| rush_hour | 16.28 / 24.19 / 36.0 ms | 14.84x | 1.96 ms | 20.8 KB (7.7 KB), 475 veh | 1.0 | 92 MB |
| severe | 20.12 / 39.43 / 47.0 ms | 12.02x | 2.43 ms | 26.9 KB (9.6 KB), 660 veh | 1.0 | 103 MB |
| gridlock_stress | 45.21 / 94.36 / 242.8 ms | 5.35x | 5.65 ms | 49.7 KB (16.5 KB), 1281 veh | 1.0 | 120 MB |

The 0.25 s step must finish inside 250 ms at 1x speed: the worst single step in the worst scenario (242.8 ms) is inside it (barely, once in 5200 steps) and the p99 is 94.36 ms, so
the engine stays smooth at maximum gridlock, with headroom for ~5x speed. The evaluation harness adds its collector (mean 34-49 ms per step).
Frame delivery was benchmarked earlier on this lineage (`reports/transport.json`); a 50 KB frame at 4 Hz is 200 KB/s per client uncompressed (65 KB/s gzip-1).

**Frontend** was not profiled or edited (another branch). Interface-level proposals if the browser lags at 1,300 vehicles: send the vehicle list as
typed arrays or deltas instead of JSON rows (50 KB -> ~15 KB), cap the publish rate at 2-4 Hz above ~800 vehicles, and cull off-screen vehicles
client-side by the viewport.

## 5. Known limitations (unresolved)

1. **Collisions are not zero.** Rush 2-5, severe 1-7, gridlock 4-19 per run (900-1,900 simulated s). Remaining causes: right-hook conflicts between a
   right-turning car (crawling, ~1 m/s) and a straight-ahead cyclist at specific junction lanes (`:n1_0_6`), moving lane-change/side collisions on
   arterial blocks, and a car standing on a pedestrian crossing in a jam with a pedestrian walking into it. The two vehicle-model mitigations tried had no effect.
   Every one of these fails the zero-collision gate; none was hidden.
2. **Severe teleports 61-98 per run** (rescue on): genuine 240 s stalls at over-saturated central junctions (n2_2, n2_1), not artifacts. Rush: 0-18.
3. **Criteria v1 are not met by any preset**, and the gridlock density band (>= 110) exceeds the district's measured storage (82-86 veh/km/lane). Decision needed (docs/CONGESTION_ACCEPTANCE.md).
4. **Gridlock recovery depends on the seed and on relieving before the ring closes** (seed 7 never recovers).
5. All three presets are oversaturated by design: the surplus waits in a counted external queue (1,270-1,955 requests held at the end of a run); the mean wait before release is ~125-136 s for `rush_hour`, 51-89 s for `severe` and ~1 s for `gridlock_stress`.
6. Demand is not calibrated to Toronto counts; pedestrians are 900/h baseline and are not part of the held accumulation.
7. The network changed (10 bike-lane left turns removed; 8 of 22 signal programs regenerated). Experiments before the rebuild (`reports/eval`, `reports/diag/before-*`) used the previous network and are kept as evidence for findings 1-4 only.

## Reproduce

```
cd backend
PYTHONPATH=. python -m flowstate.evaluation run --condition rush --seed 42 --out ../reports/mine
PYTHONPATH=. python -m flowstate.evaluation matrix --conditions rush severe gridlock --seeds 7 42 99 --jobs 5 --out ../reports/final
python ../tools/summarize.py ../reports/final/*.json         # one line per run
python ../tools/safety_diag.py severe 42 out.json            # every collision / teleport with SUMO's cause
python ../tools/live_perf.py gridlock_stress 42 out.json     # live step / frame cost
```
Each report embeds its manifest (git commit, SUMO and package versions, full condition, SUMO command line). Experiment conditions
(`baseline_*`, `exp_*`, `gl_*`, `ga_*`, `gr_*`, `rush_*`, `sevam_*`, `gridlock_r*`) are registered in `evaluation/conditions.py`.
