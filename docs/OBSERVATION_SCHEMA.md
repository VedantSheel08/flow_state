# Observation and signal-control contract (schema 1.0)

For controllers (the RL agent, SafeSignal comparisons) and for the evaluation harness. Code: `backend/flowstate/evaluation/schema.py`
(observation), `backend/flowstate/sim/signals.py` (control), `backend/flowstate/sim/engine.py` (stepping). Everything is read from SUMO
through the engine's own per-second state. Nothing is a configured target; a quantity SUMO cannot report is `None`, never a guess.

## Stepping

One simulation per process (libsumo). A controller runs inside the stepping loop:

```python
from flowstate.evaluation.runner import run_condition
from flowstate.evaluation import conditions as C
from flowstate.evaluation.schema import observation

def policy(eng, collector):                       # called after every 0.25 s step, after the collector has measured that step
    obs = observation(eng, collector)             # cheap: no stepping, no modification
    ...                                           # act through eng.signals (below)

run_condition(C.get("severe"), seed=42, out_dir=Path("reports/mine"), on_step=policy)
```

`run_condition` is the same code path the baseline uses, so a policy and the fixed-time baseline see identical demand, seeds, vehicles,
warm-up and measurements. Live (API) runs expose the same `Engine`; `observation(eng)` works without a collector.

## `observation(eng, collector=None)`

| Field | Meaning |
|---|---|
| `schemaVersion` | `"1.0"`; additions bump the minor version, renames the major |
| `time.scenario` / `time.sumo` / `time.step` / `time.warmup` / `time.inObservation` | scenario seconds (negative during warm-up), SUMO clock, step length, warm-up length |
| `network.activeVehicles`, `persons`, `pendingInsertion` | vehicles in the network, pedestrians, vehicles SUMO has loaded but not inserted (native) |
| `network.heldExternally` | background requests waiting in the demand generator's counted external queue (accumulation presets) |
| `network.cumulative` | `added`, `teleports`, `collisionRecords`, `collisionEpisodes` since start |
| `kpi` | the engine's per-second KPIs (speed, waiting, queues...) |
| `junctions.<id>.approaches[]` | per approach block: `vehicles`, `halting` (speed < 0.1 m/s), `meanSpeedKmh`, `laneKm`, `heading`, `street`, `class` |
| `junctions.<id>.signal` | `phase`, `phaseCount`, `elapsedInPhase`, `secondsToNextSwitch`, `state` (SUMO state string), `program`, `heldBy` |
| `scope.*` (with a collector) | last completed second over the declared scope: `density` (veh/km/lane), `body`, `storage`, `p10/p50/p90` speed, `halted` |
| `blocks.<street:dirSeg>` | persisted queue extent in metres and vehicles per block |
| `blockedMovements` | `(from block, to block)` movements whose head vehicle is stopped on a green with no room in the receiving lane |

Definitions of density, body occupancy, storage, queue extent and blocked movements are in `docs/CONGESTION_ACCEPTANCE.md`.

## Control: what a policy may do

A policy acts through `eng.signals` (a `SignalManager`). It can only *shorten or lengthen a green*; it can never skip, shorten or stretch
an amber, an all-red or a pedestrian-clearance interval, so every signal sequence stays legal.

```python
eng.signals.end_phase(tid, eng.t)                 # -> bool: ended the current green early (False: not in a green, < 5 s run,
                                                  #    pedestrian clearance, or an emergency vehicle holds the signal)
eng.signals.extend_green(tid, 4.0, eng.t)         # -> seconds granted (0.0 in a clearance interval; capped at 15 s per phase)
eng.signals.tls[tid].held_by                      # emergency vehicle id when pre-emption owns the signal
```

Two fixed-time programs exist per signal: `"0"` (everyday) and `"safe"` (protected arterial lefts, split minor streets). The jam presets
force `"safe"`; `RunConfig.signal_program` selects either explicitly. Emergency pre-emption and transit priority run underneath a policy
and win over it (`held_by`). Safety statistics (collisions, teleports, illegal moves) are read from the engine, never from the policy.

Both primitives are covered by `tests/test_traffic_safety.py::test_signal_primitives_never_shorten_or_stretch_a_clearance_interval`.

## What an optimiser can and cannot change

The jam presets (`docs/TRAFFIC_REALISM.md`) include a downstream restriction (one exit lane, 10 km/h). A signal policy changes how the
district's own capacity is shared between approaches; it **cannot** raise the discharge of that restriction, and the evaluation reports
throughput so this is visible. The `relieve` control (restriction lifted, accumulation metered down) is the only thing that recovers the
gridlock preset, and it is a capacity/demand change, not a signal change.

## Porting a controller from another engine (notes from the RL agent's review)

- **Minimum green.** `end_phase` only guarantees 5 s. A controller that needs walk + pedestrian clearance as its minimum (SafeSignal: 14 s on a
  50 km/h two-lane crossing) must keep that hold itself, on top of this primitive.
- **Holding.** "Hold" on this engine means `extend_green`, capped at 15 s per phase; doing nothing lets the fixed-time program end the phase.
  A policy that wants longer holds must call `extend_green` again in the next phase of the same signal, or the cap has to be raised
  deliberately (`limit=`).
- **Phase structure.** District junctions have multi-phase programs (protected lefts, split minor streets) and a variable `approaches[]` list; a
  controller written for two stages and a fixed N/E/S/W layout needs an adapter. Only demand-responsive logic that needs per-approach demand and
  per-phase served approaches ports directly.
- **Stable symbols.** `sim/signals.py`: `yellow_time`, `all_red_time`, `flashing_dont_walk`, `WALK_MIN` are used by the RL agent; their
  signatures and values will not change without notice.

### Requested additions (schema 1.1, not implemented)

| Field | Meaning |
|---|---|
| `junctions.<id>.signal.servedApproaches` | per phase index, the approach indices that have a green in that phase |
| `junctions.<id>.approaches[].exitOccupancy` | 0..1 occupancy of the receiving block the approach discharges into |
| `junctions.<id>.approaches[].emergency` | bool: an emergency vehicle is on the approach (`signal.heldBy` only covers the pre-empted case) |
