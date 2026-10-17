# Backend performance and congestion audit

This branch is based on `0de23f6`, not the other agents' uncommitted work. All measurements use the authored **synthetic Riverside District**, SUMO 1.28.0, Python 3.12.3, Windows, seed 42, fixed signals, 0.25 s steps, 180 s warm-up and **300 s observation**. There are no videos or fake vehicle positions. Concurrent development was active on this computer; timings are local observations, not hardware-independent guarantees.

## Measured load and safety

Raw, machine-readable evidence: `reports/baseline-libsumo.json`. Counts below come from SUMO, with warm-up and observation partitioned in the harness. Maximum populations/queues/pending values are sampled every 10 simulated seconds and may miss shorter peaks. All five runs reached the observation endpoint. Route validity was sampled for every active vehicle every 10 s; no invalid routes were observed. That is not an exhaustive route-validity proof.

| Modeled workload | Base trips/h × traffic | Rush peak multiplier | Warm-up wall s | Observation wall s | Observation steps/s | Max active | Max halted | Max pending | Departures during observation | Arrivals during observation | Native collision records | Teleports, whole run | Discarded, whole run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Light | 600 × 1 | 1.2 | 1.64 | 5.92 | 202.6 | 32 | 17 | 0 | 50 | 49 | 0 | 0 | 0 |
| Normal | 1600 × 1 | 1.2 | 3.58 | 12.14 | 98.9 | 89 | 42 | 0 | 148 | 136 | 1 | 0 | 0 |
| Rush hour | 2800 × 1 | 2.0 | 4.06 | 33.92 | 35.4 | 262 | 184 | 2 | 341 | 211 | 6 | 1 | 0 |
| Severe stress | 3200 × 1.6 | 2.5 | 12.98 | 73.23 | 16.4 | 564 | 369 | 59 | 665 | 277 | 9 | 1 | 12 |
| Gridlock stress | 3200 × 1.6 | 3.0 | 13.27 | 45.38 | 26.4 | 593 | 415 | 75 | 690 | 280 | 3 | 0 | 10 |

Rush hour started with **112 active / 54 halted vehicles** at t=0. Normal started with 59 / 26; severe and gridlock with 176 / 89. The existing rush recipe still compresses a rise/peak/fall into the observation interval; its warm-up has background demand, not the eventual peak. Correcting that time profile belongs to the scenario owner. Do not describe this as a stationary, calibrated rush hour.

Mean/max departure delay, whole run, seconds: light 0.032/1.50, normal 0.080/3.25, rush 0.118/4.00, severe 2.225/175.75, gridlock 3.211/179.25. These summarize vehicles which actually departed; the pending tail is censored. SUMO's 180 s maximum departure delay discards demand in the last two workloads. Effective observed arrival throughput is respectively **588, 1632, 2532, 3324, 3360 trips/h**, counting arrivals in the five-minute window, including trips that started during warm-up. Those are district trip completions, not intersection turning counts or unconstrained capacity.

Process CPU time was 7.50 / 15.05 / 31.73 / 67.00 / 53.12 s; sampled peak resident memory approximately 77 / 87 / 89 / 100 / 105 MiB. Later cases share the interpreter and allocator, so RSS is not an isolated incremental memory requirement.

## Where time goes

The light run spent 5.68 s in native SUMO stepping, 1.40 s reading state, 0.19 s generating demand, and 0.15 s polling edges. Cold Python network parsing was 0.20 s; recipe instantiation 0.00023 s; SUMO initialization 0.46 s. Normal: 12.56 s native stepping, 2.47 s state reading, 0.31 s demand, 0.22 s edge polling; cold parse 0.16 s, initialization 0.34 s. Timers are inclusive: edge polling is contained in state reading and must not be added twice.

Native vehicle/pedestrian simulation dominates libsumo. More vehicles and blocked intersections make it slower. At 16.4 steps/s and 0.25 s/step, severe congestion achieved about **4.1 simulated seconds per real second**, so asking for 16× playback cannot deliver 16× computation. Warm-up already ran unpaced in the original live service; removing wall-clock sleeps does not solve it.

The socket-based normal run took 35.40 s stepping, including 10.74 s edge polling. A measurement-only batch-subscription experiment reduced polling to 0.065 s but raised total stepping to 46.96 s, with added per-step subscription work. Both runs had identical sampled trajectory hashes and departure/completion counts. **The experiment was rejected for production**; it remains only in the harness for reproducibility. Evidence: `reports/traci-poll.json`, `reports/traci-subscribe.json`. More repetitions on a controlled host would be needed to quantify the regression precisely.

Frame construction and JSON serialization are timed separately in those reports; no clients or socket sends run inside engine step timers. `reports/transport.json` measures 200 actual loopback WebSocket echo round trips with a 5,969-byte native SUMO frame: mean 0.681 ms, p95 0.828 ms, compression off, one client. This is an isolated transport measurement, not a measurement of browser rendering, WAN latency, the live broadcaster's fan-out, or slow-client backpressure. It does not duplicate the frontend owner's profiling.

## Implemented startup work

`performance/network_cache.py` reuses parsed network metadata and keys it by **network and sidecar contents**, fixing stale reads when a file at the same path changes. The earlier cache was keyed only by path. This is principally a correctness improvement; parsing was not the major bottleneck.

`performance/prepared.py` implements an opt-in **live prepared worker**. Each worker owns an isolated SUMO process, warms it to t=0 and blocks on its command pipe. Claiming does not replay frames or restore an incomplete snapshot. It consumes that prepared instance; subsequent `advance` calls execute genuine SUMO steps, and live parameter changes run in the owning process.

In the final three measurements, preparation took 8.50, 7.75 and 8.82 s; claims took 1.151, 0.544 and 0.566 s (median **0.566 s versus 8.50 s**). An earlier lighter-load pass measured 0.281 s versus 2.72 s medians; concurrent system load varied, so use the saved final measurements rather than assuming fixed latency. All had 112 active vehicles, identical t=0 and t=10 trajectory hashes, and frozen clocks before advancement. This moves the setup cost before a click; it does **not** eliminate total computation. Evidence: `reports/prepared.json`. The existing REST Start path is not wired to this optional worker yet. No background process is automatically created on app startup.

Keys include content hashes of the network and additional network files, all backend Python sources and calibration/configuration resources, resolved scenario parameters, scenario, seed, SUMO/Python/platform/dependency versions, backend, step size and warm-up. Claim rechecks the key and TTL. Expired, changed, consumed or closed entries cannot be reused. Call `close()` to reclaim resources. Prepare only a bounded number of likely next selections; unlimited prepared workers would consume CPU/memory and slow the current run.

No persisted demand-file cache is enabled: OD generation depends on live scenario state, closure permissions, seeded profile choices and dynamic route invalidation. Reusing cached routes across incidents would be unsafe. Recipe instantiation is too cheap to justify a validated-config cache. Networks are reused instead of regenerated per run.

## Safety findings and ownership handoff

Junction checking was disabled in the baseline engine. This branch enables it and writes `collisions.xml`, retaining SUMO's warning action and all default checks. The legacy `collisions` field is retained for API compatibility: it sums **colliding-vehicle observations**, not physical crashes. For these runs it was 0/2/12/18/6, versus 0/1/6/9/3 native collision records. New `safety` fields distinguish record observations, contiguous pair/type episodes and unique pairs. A pair can have more than one episode; neither episodes nor pairs alone is a complete crash-severity model.

`reports/collision-analysis.json` preserves native records, involved types, speeds, connections and reconstructed rectangle overlap. Of 19 native records, 17 have reconstructed body overlap greater than 3 cm; two are near contact where rounded coordinates are inconclusive. This supporting reconstruction uses the actual generated vehicle widths and SUMO's rounded front/back coordinates. It must not override native detection. Bicycles count as vehicles here. No pedestrian conflict records occurred in this five-workload seed-42 sample; that does not clear the pedestrian conflicts seen in the earlier separate audit.

Concrete repeatable sites:

- Rush: five distinct colliders contact the same stopped hatch `v144` at `:n2_2_33_0` (left-turn continuation `:n2_2_21` → `church_n3`), SUMO t=302.25–395.75 s. These are different pairs, not repeated counting of one pair. Another record involves bicycle `bike@3` on a straight Church movement.
- Severe: two SUVs contact stopped van `v281` on `:n1_3_17_0`; three vehicles contact stopped truck `v351` on `:n2_2_27_0`. Other records include a midblock/bay transition and a car/bicycle conflict.
- Normal: sedan/truck near contact at `queen_e1b_2`, SUMO t=467 s, stopped victim; reconstructed overlap approximately 2 cm, within the audit's rounded-coordinate uncertainty margin.

The evidence concentrates the investigation on turning-path geometry, stopped downstream vehicles, permissive movement conflict handling, sublane behavior and driver gap settings. It does **not** prove that a specific signal plan or parameter is the sole cause. The next intervention should be run by the owners of `network/city.py`, `sim/signals.py`, `sim/demand.py` and `scenarios/*`, using the same seed and outputs, one change at a time. No such files or generated networks were modified here.

Teleports are distinct from collisions and do not by themselves prove gridlock. This branch retains native warnings and changes the generic event wording to avoid that unsupported attribution. A final rush recheck (`reports/rush-safety-recheck.json`, `reports/rush-native.log`) reproduced the exact baseline trajectory hash. Its teleport was a **yield timeout** for v95 at :n2_2_14_0, SUMO t=477 s. Two pedestrians (p93/p88) were reported jammed at :n2_2_c0; those warnings are separate from vehicle collision records. 5 failed connectivity queries were retried successfully: backgroundSubmitted=484, backgroundRouteFailures=0, pendingNow=0 at the end. This does not imply every randomly requested OD pair is routable. Do not disable checks, suppress native warnings, or label severe/gridlock presets calibrated. Normal and rush settings are also **not safety-certified**.

## Reproduce

Use the existing environment without installing packages or modifying the integration checkout:

```powershell
$env:PYTHONPATH='D:\SUMA test-wt\backend-performance\backend'
Set-Location 'D:\SUMA test-wt\backend-performance'
& 'D:\SUMA test\.venv\Scripts\python.exe' -m pytest tests -q --junitxml=reports/tests.xml
& 'D:\SUMA test\.venv\Scripts\python.exe' -m flowstate.performance.benchmark --output reports/recheck.json
& 'D:\SUMA test\.venv\Scripts\python.exe' -m flowstate.performance.benchmark --backend traci --cases normal --edge-mode poll --output reports/recheck-traci-poll.json
& 'D:\SUMA test\.venv\Scripts\python.exe' -m flowstate.performance.benchmark --backend traci --cases normal --edge-mode subscribe --output reports/recheck-traci-subscribe.json
& 'D:\SUMA test\.venv\Scripts\python.exe' -m flowstate.performance.prepared_benchmark --output reports/prepared-recheck.json
& 'D:\SUMA test\.venv\Scripts\python.exe' -m flowstate.performance.transport --frame reports/transport-frame.json --output reports/transport-recheck.json
```

Native summary, collision and tripinfo XML are generated under ignored `data/work/performance`. Each benchmark JSON embeds final native summary and collision records, real departure delays, resource samples, trajectory digest and phase timing. Baseline reports predate the new safety/accounting fields; their audit launcher already enabled junction checks. New benchmark runs also exercise the content-versioned cache. No checked-in trajectory is used to drive the simulator.

Sources: [SUMO state saving](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html), [SUMO Python/TraCI and subscriptions](https://sumo.dlr.de/docs/TraCI/Interfacing_TraCI_from_Python.html), [SUMO safety definitions](https://sumo.dlr.de/docs/Simulation/Safety.html), [native simulation counters](https://sumo.dlr.de/docs/TraCI/Simulation_Value_Retrieval.html).
