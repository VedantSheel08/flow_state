# Integration contract and local handoff

Branch: `task/backend-performance`. Worktree: `D:\SUMA test-wt\backend-performance`. Base: `0de23f6`. The original integration checkout and the frontend, signals and scenarios worktrees were not edited. No push or merge was performed.

## API changes, all additive except documented defaults

- `GET /api/scenarios`: existing schema, public duration defaults now **5 minutes**. Explicit 10/15/30/60-minute requests remain supported. The scenario owner's source catalog is unchanged; normalization happens at the API/CLI boundary.
- `GET /api/congestion-presets`: five versioned records, `id`, `label`, `params`, `warmup` (seconds), `rushHourPeak`, `stressTest`, `calibrated:false`, `safetyValidated:false`. These are provisional modeled workloads, not guarantees of achieved traffic density.
- `POST /api/runs`: existing `scenario`, `params`, `seed`, `speed`; optional **`congestion`** string. Valid IDs: `light`, `normal`, `rush_hour`, `severe`, `gridlock_stress`. Invalid IDs return 400. Explicit parameters override preset defaults, so return values, not the preset name alone, describe the actual run. A rush-hour preset sets the existing `peak`; other scenarios retain their own demand curves and event behavior.
- Default observation is 5 minutes when `duration` is omitted. Preset warm-up is 180 s; existing requests without a preset keep 90 s warm-up. Neither warm-up is part of the displayed 300 s observation clock. Fixed signals remain the existing default. No optimizer is installed.
- Run metadata adds `warmup`, `congestion`, `congestionPreset`, and `reproducibility` (key + content/version manifest). Existing `seed`, full resolved `params`, network and briefing remain. Run history files retain the added metadata.
- Restart reuses resolved parameters, seed and preset/warm-up. Reproducing conditions also requires matching network/backend versions and configuration; the manifest exposes those differences. A chosen SUMO backend can fall back from libsumo to TraCI when libsumo is occupied; check deployment isolation when exact replay is required.
- Existing WebSocket `frame`, `stats`, `event`, `status`, `results` messages remain. Frames retain native SUMO positions. Current results are also available via `GET /api/runs/current`; completed run files are in `data/runs`. `GET /api/runs` returns summaries, not complete saved results.
- Results add `safety` and `demandAccounting`. `backgroundSubmitted` is successful background vehicle-add calls, **not actual departures**. `backgroundRouteFailures` is exhausted background OD/route attempts; it does not count every failed retry. `pendingNow` is SUMO's pending vehicle count. Existing `metrics.departed/completed` include warm-up; the benchmark partitions actual native notifications by time to avoid that reporting trap. Do not divide legacy totals by 300 s and call them observation throughput.
- `collisions` is preserved as the legacy observation counter. Prefer `safety.collisionRecordObservations`, `collisionEpisodes`, `uniqueCollisionPairs`, and native `collisions.xml`; read the definitions in the audit. New fields expose existing problems; they are not safety certifications.

Example:

```json
{"scenario":"rush_hour","params":{"duration":5,"period":"am","signals":"fixed"},"seed":42,"speed":4,"congestion":"rush_hour"}
```

The API chooses a seed if omitted and returns it. Save the returned full `params`, `seed`, `warmup`, `congestion`, and reproducibility manifest. Use resolved `params` as the settings values rather than rebuilding them from the source recipe defaults in `run.spec`. Record live parameter/controller decisions as well if the entire interactive trajectory must later be reproduced. Event markers represent planned inputs; event messages/results report what actually happened. Raising traffic does not guarantee proportional insertions or fewer collisions. Display pending/achieved speed/throughput alongside population; do not decorate frames with extra vehicles.

## Prepared live workers: implemented library, REST wiring pending

```python
from pathlib import Path
from flowstate.performance.prepared import PreparedRun

# Run this in a normal Python entry point guarded by if __name__ == '__main__'
# on Windows, or from a server method after import (not at module top level).
with PreparedRun('rush_hour', {'duration': 5, 'demand': 2800}, 42,
                 Path('data/networks'), warmup=180) as prepared:
    # Preparation executes SUMO now; the clock then freezes at scenario t=0.
    prepared.claim()  # content/version/TTL check; consumes this instance
    frame = prepared.call('frame')
    update = prepared.call('advance', 4)  # 1 actual simulated second
    prepared.call('params', {'traffic': 1.1})
    results = prepared.call('results')
```

Do not use the context manager at module import time. Serialize calls on an owning service thread. `advance` accepts 1..256 steps, stops at scenario completion, and returns a native frame/KPIs/finished flag. This is a low-level worker, not a playback scheduler. The service must pace steps, publish at its existing capped rate, handle stop/disconnect, and close the worker. Pipe timeouts close the worker to prevent response misassociation. No arbitrary code-execution operation is exposed.

For existing Start-button integration, keep at most one or a few prepared entries indexed by the returned identity key. Prepare likely configurations before a click, claim only the exact matching request, remove consumed entries, and prepare replacements only within a CPU/memory budget. A miss should use normal live initialization. Do not prepare every possible seed. This feature is intentionally not silently attached to the existing service; its lifecycle and status messages need deliberate integration with the frontend owner.

SUMO snapshots are not an exact FlowState cache: native RNG saving is opt-in (`--save-state.rng`), transportables require the corresponding option, RNG states are platform dependent, lane-change/car-following model state is not fully saved, and undeparted demand needs input handling. Python RNG states, counters, route caches, reservations, incident timers, tracking and signal-manager state live outside SUMO. Keeping the same paused process avoids restoring those components. A persistent snapshot feature would need a separate equivalence test across future control decisions; no such guarantee is claimed here.

## Future controller boundary

Keep all SUMO access on its owner thread/process. The existing `SignalPolicy.step(manager,t)` hook is the integration point owned by the signals agent. A future service command should enqueue a decision for that policy to validate against minimum greens, pedestrian clearance, amber/all-red and conflict constraints, and apply it **before** the next SUMO step. Return accepted/rejected decisions with simulation time and policy version. Expose native approach queues, phase, elapsed phase time, pending departures and measurement timestamp as observations. Do not expose unrestricted raw green strings through the WebSocket. No AI algorithm, signal-timing rewrite or additional signal-control endpoint is implemented in this branch.

## Merge instructions

From the intended integration branch, review the commit first and cherry-pick this branch's commit (or use `git cherry-pick task/backend-performance` after confirming it points at the delivered commit). Do not merge the old experimental `codex/five-minute-scenarios` branch: it failed its safety gates and has overlapping signal/network/demand changes.

Only `api/app.py`, `api/service.py`, `cli.py`, `sim/engine.py` are modified existing source files. Added files are `sim/safety.py`, `performance/*.py`, `tests/test_performance.py`, docs and reports. No frontend, scenario, signal, demand-generation, network-generator or generated-network file is changed. If another agent has since touched these four existing files, resolve the small API/default/cache/safety hunks individually. Retest after combining their changes; this branch's evidence is for the recorded baseline network, not their newer work.

Required checks after integration: run `pytest tests -q`, rerun the five benchmark cases with junction checking, inspect native collision/discard/teleport outputs, confirm the 300 s default and current frontend compatibility. Dense workloads remain unsuitable for a realism/safety claim until the owner-led collision investigation is resolved.
