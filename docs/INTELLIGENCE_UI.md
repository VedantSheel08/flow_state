# FlowState Intelligence Lab — verified UI v2

Primary branch: `task/intelligence-ui-v2`. Worktree: `D:/flowstate-migration/intelligence-ui-v2`.
Created from the latest committed `task/ml-intelligence` at the start of this pass: `8a8d2a07b7aeb54fa0ad56bda3b07a583fdd8140`. The previously completed UI work was reused, then the remaining requirements from the attached brief were implemented. Claude's active, uncommitted ML changes were inspected for coordination and were not copied or edited.

## Preview and integration

- Preview: http://localhost:5177
- Isolated API: http://127.0.0.1:8016, now served from the v2 worktree.
- Original ML preview/API on 5176/8015 remain separate.
- No production frontend, Vultr backend, simulation physics, controller logic, model, or experiment-evaluation result was changed. No model was trained or retrained. No merge or deployment was performed.
- Existing ignored replay files were copied into this worktree. A new UI-requested comparison, balanced scenario / seed 3420 / 0.7× demand, completed in 58.4 seconds on the shared machine and was automatically selected.

The existing lab API contracts are unchanged: status, geometry, replay index, replay/job recording, comparison/forecast evaluation, runtime telemetry, grounded explanations, and the live WebSocket. No backend migration is required. Future uncommitted intelligence-contract work is deliberately not a dependency of this UI.

Run the unchanged lab API from this worktree's backend directory with a Python environment containing the existing backend dependencies:

```powershell
python -m flowstate.lab.cli serve --port 8016
```

Run the UI:

```powershell
cd D:/flowstate-migration/intelligence-ui-v2/lab
$env:FLOWSTATE_LAB_API = 'http://127.0.0.1:8016'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5177 --strictPort
```

Replay data remains git-ignored, as in the original lab. The tracked trained forecaster is used as supplied. No new credentials or infrastructure are required. Gemini is unavailable locally; its measured-answer fallback was verified, and no external Gemini-provider success is claimed.

## Requirements completed

- Supplied FlowState logo; light neutral SaaS workspace; deliberate typography/spacing; gray baseline and restrained blue ML visualization; no added gradients, glows, emoji, or decorative icon cards.
- Two dominant synchronized SUMO maps, actual vehicle positions and signal states, measured queue overlays, total queued counts, and the same timestamp on both sides, including mobile.
- Exactly four primary KPIs: average waiting time, average queued vehicles, throughput, and delayed vehicles. Whole-run and playhead scopes are labeled. Deltas are measured right-minus-left values with restrained red/green treatment.
- One overlaid chart with total queue, average delay, and throughput options. Intervention ticks come only from recorded ML decision logs, including either controller side. Graph seeking and timeline seeking update both maps.
- Contextual prediction/action/outcome panel backed by recorded observations, forecasts, decision logs, and measured full-run differences. It explicitly avoids attributing the whole-run result to one intervention.
- Visible held-out evidence and confidence interval. The underlying conclusion remains **no reliable improvement in average wait**. Individual runs can improve or regress.
- One collapsed **Advanced** view contains full run/provenance/safety data, all 16 approach observations, intersection filtering and map highlighting, forecasting diagnostics, runtime diagnostics, and grounded Gemini explanations.
- Scenario, controller, seed, demand, playback, speed, new paired-run recording, and live simulation controls remain functional.
- Replay timing follows actual SUMO frame timestamps, beginning at 1 second. Earlier-finishing runs hold their last observed frame and are labeled complete. Hidden comparison playback pauses while using the live view.
- Missing measurements remain unavailable instead of becoming zero improvements. Small reported differences retain sufficient precision rather than being relabeled as equality.

## Measurement definitions

Average waiting time is the backend completed-trip mean. Average queued vehicles is the backend full-run mean of stopped vehicles across approaches. Throughput is the backend arrival rate over the 900-second demand window. **Delayed vehicles** counts the recorded per-vehicle flag where SUMO current waiting time is positive; it is an instantaneous count of vehicles with current waiting time, not a count of passengers or unique vehicles delayed at any earlier point.

The chart's average-delay option explicitly means mean current waiting time across vehicles in the network. It does not claim to measure travel-time loss. The throughput chart uses cumulative measured arrivals divided by elapsed demand time × 3,600, freezes at 900 seconds, and excludes drain-period arrivals. Predictions remain labeled as future estimates; observed queues remain separate.

Source-data/browser cross-check at 751 seconds in rush-ramp seed 3100:

| Measurement | Without ML | With ML |
|---|---:|---:|
| Total queued at playhead | 385 | 269 |
| Positive-current-wait vehicles | 386 | 270 |
| Full-run average queued | 187.43 | 172.96 |
| Demand-window throughput (veh/h) | 2656 | 2808 |
| Full-run average waiting time (s) | 217.85 | 200.86 |

The right-side peak 30-second forecast was 31.5 vehicles at A1:E, based on 28 observed vehicles at 750 seconds. The decision log contains 26 interventions through 751 seconds. These values were read from the actual replay and checked in the browser; they are not UI constants.

## Verification results

- TypeScript noEmit and Vite production build: passed.
- Frontend tests: **18 passed in 3 files**. Coverage includes timestamp alignment, interpolation, drain-window throughput, delayed-vehicle measurement, missing data, precision, regressions, exactly four primary KPIs, Advanced-only intersection evidence, selection, and unavailable replay handling.
- Real-browser verification: replay pause/play/restart; keyboard and pointer seeking; graph synchronization; all chart options; scenario/controller/seed/demand selection; asynchronous SUMO recording; intersection filtering and highlighting; Advanced forecast/runtime/Gemini panels; live start/pause/resume/speed change/stop and final measurements.
- Live ML simulation was paused and resumed from 16× to 4×, then stopped at 480 simulated seconds. The finished result and worker telemetry remained available in Advanced.
- Desktop layouts checked at 1200 pixels and mobile at 390 pixels. Mobile body/scroll width and visible timestamps are checked separately from screenshot appearance.
- Actual hundreds-of-vehicle replay rendering and controls were checked. No fixed FPS claim is made for this shared machine.
- Final diff excludes backend code, production frontend code, trained artifacts, temporary viewport harness, dependencies, and build output.

Screenshots are direct browser captures of actual SUMO data:

- [Finished desktop comparison](../artifacts/intelligence-ui/desktop-comparison.png)
- [Measured outcomes and evidence](../artifacts/intelligence-ui/measured-evidence.png)
- [Mobile layout](../artifacts/intelligence-ui/mobile.png)

Reproduce automated checks with `npm run build` and `npm test` from `lab/`.
