# Verification and scope

Final result: **61 passed, 0 failed, 0 skipped in 114.88 seconds**, with one existing test-client deprecation warning. The final pytest report is `reports/tests.xml`. The suite exercises the existing API/WebSocket and SUMO scenario tests plus the new prepared-worker/cache/preset/safety tests. Native SUMO executes in these tests; this is not a mocked traffic suite. The only warning is the pre-existing Starlette test-client deprecation concerning its httpx integration.

New verification covers content-key invalidation for seeds, configurations, same-path network metadata and calibration resources; actual process resource measurements; rejection of invalid seeds; frozen prepared clocks; live continuation; equality between a prepared worker and a freshly warmed ordinary engine; repeat-seed equality; consumed/expired worker cleanup; valid congestion parameter mappings; public five-minute defaults; safety episode definitions; geometric overlap classification; and exact trajectory/KPI equivalence for the rejected socket batching experiment.

Passing tests establish software behavior, **not that every traffic configuration is collision-free**. The separate five-workload audit deliberately records the known collisions and discarded demand instead of hiding them or turning them into an expected-zero assertion. Those safety issues remain unresolved and block claims of calibrated, stable dense traffic.

Existing source changes:

| File | Change |
|---|---|
| `backend/flowstate/api/app.py` | Public five-minute defaults, additive preset list, optional congestion request field |
| `backend/flowstate/api/service.py` | Resolve preset/default parameters, retain warm-up/preset/reproduction metadata, preserve restart configuration |
| `backend/flowstate/cli.py` | Five-minute headless default while preserving explicit duration |
| `backend/flowstate/sim/engine.py` | Content-versioned metadata cache, enabled junction checks, native collision output, explicit safety and demand-accounting results |

Added source: `performance/benchmark.py`, `collision_audit.py`, `congestion.py`, `identity.py`, `network_cache.py`, `prepared.py`, `prepared_benchmark.py`, `resources.py`, `transport.py`, package initializer; `sim/safety.py`; `tests/test_performance.py`.

Added documentation: backend performance/safety report, API/prepared-worker integration contract, Toronto calibration strategy and this verification summary. Added reports: five baseline loads, both socket experiments, collision analysis, prepared-worker measurements, transport measurement/native payload and final pytest XML.

Not changed: frontend/rendering/artwork/CSS/camera, scenario definitions and components, driving profiles, demand generator, signal policy/timings, network builder, generated road networks, the original integration source files, or another agent's worktree. The prepared worker is a tested optional backend interface; Start-button lifecycle wiring is pending integration. No snapshot restoration, prerecorded driving, AI model, push or automatic merge is included.
