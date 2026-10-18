# Demo scenarios for the Intelligence Lab (2x2 network)

Three reproducible scenarios on the lab's own 2x2 signalised grid (`flowstate.rl.sim.SignalSim`, junctions A0 B0 A1 B1, 200 m two-lane
links, 8 entries / 8 exits). They are **not** Toronto-district scenarios; the district traffic-realism work on `task/traffic-realism`
is untouched. Code: `backend/flowstate/demo/`. Manifest: `artifacts/demo/scenario_manifest.json`. Raw benchmark: `artifacts/demo/benchmark.json`.

```powershell
cd backend
..\.venv\Scripts\python -m flowstate.demo.cli benchmark     # 90 episodes, ~14 min on 6 workers
..\.venv\Scripts\python -m flowstate.demo.cli perf          # CPU / RAM / latency, one episode at a time
..\.venv\Scripts\python -m flowstate.demo.cli manifest      # rebuild the manifest from the two files above
```

## What is real, what is not

* Every vehicle is a SUMO vehicle. Nothing is placed, frozen or removed by the demo code, collision detection is SUMO's (`warn`),
  and SUMO's 240 s deadlock rescue (teleport) stays on. Teleports, collisions and vehicles not finished are reported per run.
* The route file is a pure function of (scenario, seed, intensity). All controllers are run on a byte-identical file
  (SHA-256 compared; the benchmark aborts on a mismatch). Controllers cannot change the demand.
* Incidents are real vehicles with a SUMO `<stop>` inserted into a lane. SUMO inserts a vehicle only when the lane has room, so the
  stop can start late: measured start lag was median 11 s, **maximum 69 s** over 60 incident vehicles (benchmark). It varies slightly with the controller.
* Demand was calibrated on design seeds 9001-9003 (and 9004-9008 for the incident timing). **Illustrative runs and benchmark conclusions are
  separate**: the benchmark uses seeds 4001-4010, disjoint from every seed the forecaster or ML controller was trained, tuned or evaluated on.
  The illustrative seed per scenario is the benchmark seed with the *median vehicle count* (demand only, never outcomes).

### A flaw found and fixed during the work
The first benchmark put the breakdowns at 210 s and 300 s. In 7 of 60 incident vehicles insertion was delayed by traffic, by up to 838 s,
so the incident did not happen when designed. I moved them to 150 s / 240 s (checked on design seeds only), re-ran the entire benchmark,
and the numbers below are from that second run. Rush-hour and spillback results were bit-identical between the two runs.

## Scenarios

Demand windows are 900 s followed by 600 s of drain (no new vehicles), exactly what the lab's recorder and live worker use. Intensity 1.0
is the calibrated level (the lab's slider range is 0.5-1.4; the seed also scales demand by +-15 %, as for the lab's other scenarios).

| id | name | vehicles / run | design |
|---|---|---|---|
| `demo_rush_hour` (default) | Heavy rush hour (inbound to the north-east) | 775-1101 (mean 911) | S and W entries carry ~2x the N and E entries, 65 % to the NE corner; high from t=0, peak 4-9 min, eases after 11.5 min |
| `demo_spillback` | Spillback-prone asymmetric demand | 1218-1582 (mean 1445) | northbound stream turns into the short B0-B1 link (88 %), +60 % surge 5.7-9 min, westbound cross traffic and left turns load B1 |
| `demo_incident` | Serious incident, near-gridlock | 966-1291 (mean 1138) | balanced heavy demand; left lane of B0-B1 stalled 2.7-9.2 min, right lane 4.2-7.2 min (full block ~3 min, under the 240 s rescue limit) |

Flows, turning weights and profiles are in `flowstate/demo/scenarios.py` and are copied into the manifest.

## Measured behaviour (benchmark, 10 seeds, 30 episodes per scenario, means)

| | rush hour | spillback | incident |
|---|---|---|---|
| mean wait, plain heuristic (s) | 154 | 208 | 227 |
| 95th-pct travel time (s) | 696 | 806 | 889 |
| peak halted vehicles (storage = 787) | 196 | 263 | 333 |
| spillback (% link-seconds >= half full) | 6.6 | 13.2 | 17.1 |
| most links spilled at once | 3.0 | 3.5 | 4.7 |
| seconds near standstill (mean speed < 1.5 m/s, >= 50 % halted) | 74 | 125 | 375 |
| teleports / run (heuristic) | 1.2 | 1.8 | 6.8 |
| collisions | 0 | 0 | 0 |
| vehicles still on the road / not inserted at 1500 s | 0.2 | 8.2 | 11.5 |
| queue cleared (below 10 % of storage), mean time (s) | 1058 | 1386 | 1271 |

The severity ladder holds on every measure: delay, peak queue, spillback and standstill time increase from rush hour to incident.
The first approach to fill is `A1:S` (link A0-A1) in 65 % of rush-hour runs; the other two scenarios have no single dominant origin
(40 % and 35 %), because it depends on the controller.

Collisions: 0 in all 90 episodes. Teleports are SUMO deadlock rescues in the saturated junction boxes (the same mechanism as in the district
work); they cluster in the incident scenario and during recovery. Dropped demand: no vehicle is deleted; "not inserted" and "on the road"
are vehicles that had not finished by the end of the run (a recovery that was not complete), counted in `completed_fraction`.

### Controller comparison (paired by seed, bootstrap 95 % CI; honest, not favourable)

| paired difference in mean wait (s) | mean | 95 % CI | seeds better / worse |
|---|---|---|---|
| ML vs heuristic, rush hour | +6.5 | [-2.8, +16.9] | 3 / 7 |
| ML vs heuristic, spillback | +5.7 | [-3.8, +15.2] | 3 / 7 |
| ML vs heuristic, incident | -17.5 | [-46.0, +5.2] | 6 / 4 |
| heuristic vs fixed-time, rush hour | -5.4 | [-19.2, +8.8] | 5 / 5 |
| heuristic vs fixed-time, spillback | **+25.7 (worse)** | [+14.0, +38.2] | 0 / 10 |
| heuristic vs fixed-time, incident | **+33.9 (worse)** | [+20.6, +48.8] | 0 / 10 |

**No scenario shows a clear ML benefit** (all intervals include zero; the ML adds a little more teleports in the incident scenario, 10.0 vs 6.8, also not
clear). The plain queue heuristic is clearly *worse* than the fixed-time plan in the two harder scenarios. These scenarios therefore expose a real
control problem (spillback and blockage) rather than showing the existing ML winning; any single replay that shows ML ahead is one draw and the UI should show
this table next to it. Pooled over all 30 pairs, ML vs heuristic is -1.8 s [-13.0, +7.6] (12 better, 18 worse).

### Forecast skill in these scenarios
Shadow forecasts (displayed in the non-ML runs, never used there) of "an approach reaches half its storage within 60 s": precision 0.92-0.97, but recall only
0.10 / 0.28 / 0.17 (rush / spillback / incident), median lead 15-20 s. The forecast is reliable when it fires and fires late: the
"predicted spillback" marker is trustworthy but not an early warning of most queues. Recall counts every forecast sample, so one event is counted several times.

## Illustrative demo seeds (watch, do not conclude)

| scenario | seed | wait fixed / heuristic / ML (s) | heuristic wait percentile among the 10 seeds |
|---|---|---|---|
| rush hour | 4003 | 161 / 179 / 160 | 70th |
| spillback | 4003 | 175 / 224 / 238 | 60th |
| incident | 4006 | 199 / 214 / 244 | 30th |

On the spillback and incident seeds ML is *worse* than the heuristic; on rush hour it is better. That mix is what the benchmark says, and is why no seed was picked for a favourable look.

## Replay markers

Rules are fixed and copied into the manifest (`markers.rules`); times for the illustrative seeds are in `markers.illustrative.{queue_heuristic,ml_forecast}`.
Kinds: `queue_buildup` (5 % of approach storage halted, then 15 % "congested"), `spillback` (first internal link half full, with its id),
`predicted_spillback` (ML forecast, with whether it was verified within 60 s and the lead time), `controller_intervention` (30 s clusters where the
ML decision differed from the heuristic; only the 6 busiest are in the manifest), `recovery` (80 % of peak, 50 %, cleared) and `incident` (measured stop and clear).
Rush hour, seed 4003 (heuristic run): buildup 180 s, forecast 265 s, spillback 350 s, congested 445 s, recovery 635 s, half 940 s, cleared 1170 s.

## Resources (`artifacts/demo/performance.json`, 8-core 32 GB Windows machine, one episode per process, frames recorded)

* CPU: 0.91-0.99 cores average (SUMO is single-threaded). Peak RSS 222-271 MB per simulation process.
* One simulated second (SUMO step + measurement + frame capture): mean 9.6 / 17.0 / 15.2 ms (rush / spillback / incident, heuristic), p99 20-31 ms, worst single second 170 ms.
  Under 250 ms at p99 everywhere; episodes ran 42-79 times faster than real time.
* One control decision (observation + forecast + controller): mean 0.3-0.6 ms, p99 under 1.9 ms (the observation is timed separately in the file).
* A live-WebSocket check through the real lab API (`tools/verify_lab_integration.py`): all three scenarios listed by `/api/lab/status`, manifest served by
  `/api/lab/demo-scenarios`, an ML-vs-heuristic pair recorded for each (same demand hash as the manifest), and a live session streamed with no errors.
  The test client itself limited frames/s at 8x speed, so that number is a lower bound.

## Default demo scenario

`demo_rush_hour`. The choice is a pre-declared checklist computed on the non-ML controllers only (visible buildup >= 120 s between 5 % and peak; one
approach is first to fill in >= 60 % of runs; clean run: <= 1 teleport and no collisions; recovery inside the run; heavy enough). Rush hour passes 4 of 5,
spillback and incident 2 of 5 (no recovery check passes for any: some seeds do not finish clearing by 1500 s). Ties would have gone to the middle tier.
It is also the scenario with the fewest teleports. ML results played no part. Suggested sequence: rush hour (queue forms at one link, spreads), then spillback
(a downstream link overflows into the junction behind it), then incident (worst case), always showing the multi-seed table with the replay.

## Known limitations

* Teleports (SUMO deadlock rescues) are not zero: 1.2 / 1.8 / 6.8 per run for the heuristic, up to 14 in some incident seeds. Not hidden, not suppressed.
* Incident start can lag its schedule by up to ~70 s (insertion needs room). Both lanes are blocked for a fixed 3 minutes only to stay under SUMO's 240 s rule.
* The demand is calibrated near the network's capacity: a +-15 % seed change flips some runs between "recovers" and "still queued at 25 min".
* Only fixed-time, the queue heuristic and the ML-forecast heuristic were run; the RL agent from `task/rl-signal-control` was not.
* Scenarios live in `rl.demand.SCENARIOS` (so the lab API accepts them) but never in `TRAIN_SCENARIOS`/`HELD_OUT_SCENARIOS`; `lab.evaluate` now iterates
  `BASE_SCENARIOS` (the original five) so the lab's published evaluation is unchanged. The five original scenarios' demand hashes were verified identical.
* The lab frontend is untouched; it can read the new scenarios from `/api/lab/status` today and the manifest from `/api/lab/demo-scenarios`.
