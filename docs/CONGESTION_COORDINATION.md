# FlowState congestion integration decision

**Hold integration.** There is enough geometric storage for much denser arterials, but the current labels do not describe achieved conditions. Keep `D:\SUMA test` unchanged. No branch has been merged, pushed or taken over.

## Physical capacity first

Geometry, permissions and signal link indices match between baseline and signals. Storage: **17.245 motor lane-km overall; 10.527 interior; 6.273 interior arterials**. Fringe stubs account for 39%; assess corridors separately.

For a 4.75 m car and 2/3/5 m standstill gaps, per-lane packing gives:

| Scope | Gap 2 m | Gap 3 m | Gap 5 m |
|---|---:|---:|---:|
| Whole noninternal motor network | 2,506 | 2,208 | 1,766 |
| Interior streets | 1,522 | 1,339 | 1,079 |
| Interior arterials | 896 | 801 | 643 |

These are car-packing upper bounds; fleet mix, turn pockets, permissions and closures reduce usable capacity. At 3 m gaps, full storage means about 61% lane-length body coverage, not 100%. **Do not widen first:** roughly 530–720 arterial vehicles fit the nominal severe-target envelope; verify movement capacity before promising it.

## Actual validation matrix

Frozen **0b49c08**, fixed AM plans, SUMO 1.28.0, dt=.25 s, 300 s warm-up + 300 s observation. Three requested loads × seeds 7/42/99. Each diagnostic holds its seeded peak demand/OD; this is **not a shipped profile fix**. Density is motor vehicles across all interior arterial directions. All ten runs completed; 10 s route-validity samples found no invalid active routes.

| Requested probe | Seed | Mean arterial veh/lane-km | Mean standstill storage use | Max connected-lane standing queue m / vehicles | Collision records / teleports / discards* | Decision |
|---|---:|---:|---:|---:|---:|---|
| gridlock | 7 | 29.0 | 22.3% | 171 / 23 | 8 / 0 / 1 | Reject |
| gridlock | 42 | 18.5 | 13.6% | 124 / 17 | 4 / 0 / 0 | Reject |
| gridlock | 99 | 33.6 | 25.9% | 166 / 22 | 5 / 1 / 5 | Reject |
| rush | 7 | 18.8 | 14.3% | 151 / 19 | 3 / 0 / 0 | Reject |
| rush | 42 | 24.5 | 17.9% | 156 / 22 | 2 / 0 / 0 | Reject |
| rush | 99 | 22.1 | 16.9% | 124 / 17 | 2 / 0 / 0 | Reject |
| severe | 7 | 31.9 | 24.2% | 171 / 23 | 6 / 2 / 2 | Reject |
| severe | 42 | 27.8 | 20.5% | 152 / 20 | 3 / 1 / 1 | Reject |
| severe | 99 | 34.5 | 26.4% | 164 / 23 | 9 / 1 / 11 | Reject |

*Safety/loss counts include warm-up; density is observation-only. Across nine held-peak runs: **42 native collision records, 40 per-run unique pairs, five teleports, 20 discards**. Six records involved pedestrians, including two bicycle/pedestrian contacts. Native records are not automatically unique physical crashes. The unchanged-profile seed-42 reference was collision-free but still sparse: **21.2 veh/lane-km / 15.6% storage**.

Stop-sample gap medians were 2.2–2.8 m; tails below 2 m need review, with proper filtering of bicycle leaders/sublane maneuvers. Excessive spacing is not established as the main sparsity cause. Queues reached roughly one block; adjacent-block proxies lasted at most 105 s, without causal blocked-cycle proof. Slow sparse traffic is not gridlock.

## Outcome targets — engineering requirements, not Toronto observations

| Achieved class | Arterial density | Standstill storage use | Required sustained evidence |
|---|---|---|---|
| Regular rush | 40–75 veh/lane-km | about 30–60% | moving/discharging stop-and-go queues; minimum targets ≥240/300 s |
| Severe | 85–115 | ≥65% | spillback through ≥2 consecutive physical blocks for ≥120 s; minimum targets ≥240/300 s |
| Actual gridlock in a declared zone | ≥110, below physical jam limit | ≥85% | ≥4-junction blocked cycle across ≥2 corridors, legal greens unable to discharge, component outflow ≤10% of matched nonlocked reference for ≥120 s |

All tiers require native safety, honest demand accounting and appropriate t=0 conditions. Gridlock cannot be inferred from a slider or a handful of stationary cars. Exact speed, gap, occupancy, queue, warm-up, seed and safety assertions are in [the acceptance contract](CONGESTION_ACCEPTANCE.md).

## Bottlenecks and assignments

1. **Signals/demand, P0:** replace the global pending>10 stop with reviewed per-origin admission and visible external holding; preserve OD and insertion safety. Gates begin at 55% queued storage and rate factors fall to .45. Seed 42 became less dense from Severe (27.8) to Gridlock request (18.5), while exhausted requests doubled to 1,500. Separate requests, attempt counts, expected deferred rates, route failures and cancellations; `.failed` currently mixes causes.
2. **Scenarios/drivers, P0:** decouple profile time from the 5-minute display; provide a ≥30-minute plateau and prewarmed observation slice. Calibrate through-arterial OD/residence time and critical movement v/c. Diagnose insertion/turning/sublane/pedestrian contacts jointly with signals. Measure 2–5 m stable following gaps without forcing positions or weakening safety. Return a frozen commit.
3. **Frontend, P0:** keep the renderer; use backend-owned presets and separate requested/achieved conditions. Frontend Severe=3200×1; backend Severe=3200×1.6. Backend Severe/Gridlock are identical outside rush_hour. Remove “calibrated”/unverified gridlock claims; show density, storage, queues, held/pending demand, losses and safety. Reprofile at 800/1200/1500 cars.
4. **Backend coordinator:** reconcile counters, publish live safety/accounting, preserve frame/API contracts and log control changes. Native stepping dominated the earlier profile; cache only prepared workers that pass warm-up density/safety gates. Full [owner instructions](CONGESTION_AGENT_ASSIGNMENTS.md) identify the exact interfaces.

## Staged integration gate

Frozen owner commits and acknowledged definitions → **after approval only**, disposable combined candidate → existing tests plus 3 tiers × AM/PM × 7/42/99 and held-out 314/2026 → conservation, t=0 queues, 300-second persistence, causal spillback, safety and closure/emergency/bicycle tests → separate native/WebSocket/rendering benchmarks → review before changing integration.

**Zero of ten probes qualifies.** Four new contract tests pass and prevent false gridlock certification; causal/conservation gates remain pending. Evidence: `reports/frozen-matrix/index.json`, `acceptance.json`, raw run reports and `congestion-audit-manifest.json`. This is not combined-branch or PM qualification. Assignments are documented, not injected into another agent's session. Integration remains clean at `0de23f6`.
