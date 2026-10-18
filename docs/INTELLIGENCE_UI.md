# Intelligence Lab UI handoff

UI branch: `task/intelligence-ui`. Worktree: `D:/flowstate-migration/intelligence-ui`.
Base: `8a8d2a07b7aeb54fa0ad56bda3b07a583fdd8140` from `task/ml-intelligence`.

The direct task requested this branch name. The later attached design brief was used as a visual reference; its alternate `task/intelligence-ui-v2` branch instruction was not substituted for the direct request.

## Preview and integration

- UI: http://localhost:5177
- Isolated local API: http://127.0.0.1:8016
- Claude's original UI/API on 5176/8015 were left running.
- No backend implementation, trained model, physics, controller, or recorded evaluation artifact was edited. No production website or Vultr operation was performed.
- Existing ignored replay files were copied into this worktree for verification. A new balanced/light-demand pair, seed 3401, was recorded through the UI against this worktree's API, taking 13.6 seconds. Original replay files were not overwritten.

No API migration is required. The UI consumes the existing `/api/lab/{status,geometry,replays,comparison,forecast,perf,ask}`, replay-job endpoints, and `/ws/live` contracts. Additional uncommitted contract/benchmark work observed in the original ML worktree was inspected but not imported. Incorporate future ML API changes only from its reviewed committed version.

To run against a different local lab API:

```powershell
cd D:/flowstate-migration/intelligence-ui/lab
$env:FLOWSTATE_LAB_API = 'http://127.0.0.1:8016'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5177 --strictPort
```

The API is the unchanged `python -m flowstate.lab.cli serve --port 8016`, run from this worktree's `backend` directory using the existing ML Python environment. The existing launcher remains unchanged.

## Product changes

- Light neutral workspace with the supplied FlowState wordmark, restrained color, and two dominant linked traffic canvases.
- Default pairing is the queue-responsive heuristic without ML versus the forecast-assisted controller. Alternate pairings receive accurate controller labels.
- Four outcomes: completed-trip average wait, demand-window throughput, total stopped vehicles at the playhead, and blocked road time (spillback). Every scope is labeled. Deltas are right minus left; improvements and regressions are treated equally.
- One overlaid chart switches between queue, current wait, and cumulative demand-window throughput. Intervention ticks come from `mlLog` on either side. No intervention is inferred from a forecast alone.
- A compact explanation identifies a real 30-second prediction, the latest recorded decision change, and the full-run measured result. It explicitly avoids attributing the full-run difference to one decision.
- The multi-seed confidence interval remains visible outside the technical disclosure. This controller's held-out result remains **no reliable improvement in average wait**.
- Scenario recording, live controls, Gemini explanations, full metrics, safety events, model evaluation, and runtime diagnostics remain available. Advanced information is collapsed initially.

## Measurement details

Replay frames begin at SUMO time 1. The previous view treated array indices as timestamps; the UI now selects/interpolates by recorded timestamp. Both views, the playhead, queue values, forecast lookup, and intervention lookup use the same time. A run that finishes earlier holds its last measured frame and is labeled complete.

The throughput chart computes cumulative arrivals divided by elapsed demand time, multiplied by 3,600. At 900 seconds it freezes the numerator and denominator, excluding drain-period arrivals. Current wait is named separately from completed-trip average wait. Queue overlays use measured approach observations; forecasts are labeled separately in the explanation/live diagnostics.

Small nonzero reported changes retain extra decimal precision, so rounding cannot turn them into a claim of equality. No passenger or causal-benefit estimates were added.

## Verification

Real-browser checks against the unchanged local SUMO backend:

- Replay pause/play, restart, Home/End/PageUp keyboard seeking, pointer seeking, and graph-to-map synchronization.
- Linked canvas zoom and double-click fit; actual vehicle positions and live signal colors.
- Queue/current-wait/throughput chart switching and logged intervention markers.
- Scenario/replay selection, controller selection, seed/demand controls, same-controller validation, asynchronous paired-run recording and result selection.
- Live ML simulation start, pause, resume, speed change from 16× to 4×, and stop; streamed vehicles, queues, forecasts, model-error checks, and final metrics.
- The balanced seed-3102 replay correctly reports **more waiting** with ML (+1.67 seconds).
- Gemini entry and the real measured-answer fallback; Gemini is unavailable locally because no key/client is configured. No live Gemini-provider call was claimed.
- Full comparison/provenance and forecast diagnostics expand correctly. Runtime diagnostics displayed measured worker timings, WebSocket samples, and the stopped live session.
- Desktop comparison layouts at 1440 and 1200 pixels, the direct medium-width preview, and a 390-pixel mobile viewport. Mobile body width and scroll width both measured 390 pixels; the paired maps measured 362 pixels with no horizontal overflow.

Numerical cross-check from the source replay at 751 seconds: left/right queues 385/269, in-network vehicles 450/453, forecast peak 31.5 vehicles (28 observed at that approach at 750 seconds), and 26 ML changes through the playhead. These were checked against the rendered UI and the stored replay, not entered as constants.

Frontend verification commands:

```powershell
cd lab
npm run build # TypeScript noEmit check plus Vite production bundle
npm test      # timing, interpolation, measurement scopes, and honest result formatting
```

Final results: TypeScript/Vite build passed; **13 tests passed in 2 files**; `git diff --check` passed. Frontend dependency installation reported zero vulnerabilities. Browser interaction checks above are manual verification, separate from the automated tests.

Screenshots are direct browser captures of actual SUMO replay data:

- [Desktop traffic comparison](../artifacts/intelligence-ui/desktop-comparison.png)
- [Measured outcomes and multi-seed evidence](../artifacts/intelligence-ui/measured-evidence.png)
- [390-pixel mobile view](../artifacts/intelligence-ui/mobile.png)

The screenshot viewport harness was temporary and is excluded from the shipped application. No merge or deployment is authorized by this UI handoff.
