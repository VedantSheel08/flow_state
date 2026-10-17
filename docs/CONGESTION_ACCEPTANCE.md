# Congestion acceptance criteria (version 1)

Code: `backend/flowstate/evaluation/classify.py` (`CRITERIA_VERSION = 1`). The classifier **never reads the requested preset to decide
what was achieved**: it evaluates the measured per-second series of one run against the gates below and returns the highest tier whose
traffic-state gates all pass, plus the safety/accounting gates every tier needs. A slider position, a refused insertion or a screenshot
never qualifies a tier. These criteria were fixed before the final measurements and **were not relaxed** to make any condition pass
(see "Known gap" below).

## Measurement window and scope

- One classified window: the 300 s after the warm-up. A run's warm-up is long enough for the district to reach its declared accumulation.
- Scope: interior arterials (the blocks between the district's signalised junctions). Gridlock is additionally evaluated on a *declared zone*
  (the four central junctions `n1_1 n2_1 n1_2 n2_2`, fixed before the run).
- `density` = motor vehicles per km of motor lane over the scope, empty lanes included (veh/km/lane).
- `body` = spatial occupancy by vehicle length. `storage` = (vehicle length + native standstill gap) summed / lane metres.
- Speeds: `p10/p50/p90` are instantaneous per-second percentiles. On a signalised grid roughly half of all vehicles are stopped at a red at
  any instant, so the instantaneous median is ~0 even at light load. The median-speed gate therefore uses each vehicle's **average speed
  through the scope** (trips of at least 20 s); the instantaneous values are reported next to it.
- Queue extent: persisted (>= 10 s) bumper-to-bumper run (<= 10 m gaps, speed < 1 m/s) from the stop line.
- Blocked movement: the head vehicle is stopped while its signal is green and the receiving lane has no room.

## Tier gates

| Gate | rush | severe | gridlock |
|---|---|---|---|
| density (veh/km/lane) for >= 240 of 300 s | 40-75 | 85-115 | >= 110 (declared zone) |
| storage use for >= 240 s | >= 30 % | >= 65 % | >= 85 % |
| body occupancy for >= 240 s | >= 20 % | >= 40 % | >= 50 % |
| median per-vehicle travel speed | 8-20 km/h | <= 8 km/h | <= 1 km/h |
| p90 instantaneous speed | <= 35 km/h | <= 20 km/h | <= 5 km/h |
| halted share | - | >= 55 % | >= 85 % |
| queued approaches | >= 2 with >= 8 vehicles over >= 50 m | | |
| stop-and-go | >= 20 vehicles with >= 2 stops and starts | >= 20 | |
| corridor spillback | | >= 120 s: two consecutive blocks each >= 75 % queued and a green-blocked movement between them | |
| sustained | | last 60 s density >= 80 % of first 60 s | |
| blocked directed cycle | | | through >= 4 junctions over >= 2 corridors for >= 120 s |
| legal greens blocked | | | every cycle junction shows green-blocked movements while signals keep cycling |
| component outflow | | | across the declared component <= 10 % of a matched non-locked reference |
| rescue disabled | | | SUMO deadlock rescue (teleport) off and disclosed |

## Safety and accounting gates (every tier)

- **collisions**: zero native SUMO vehicle/vehicle and vehicle/person collision records, warm-up included (junction collision checking is on).
- **routes**: zero invalid active routes (sampled every 10 s).
- **hiddenLoss**: the integer demand ledger reconciles. `requests = submitted + dropped + cancelled + held`, with no unexplained vehicles.
  Refused insertions (`vehicle.add` rejected for a stale route) are counted as `dropped`, never lost.
- **teleports**: zero in rush and severe (a teleport is a vehicle that stood still for 240 s). Gridlock runs with the rescue disabled, so there are none by construction.
- **steadyAtStart**: density at least the tier minimum at t=0 and through the last two signal cycles of the warm-up.

Qualifications: `qualified` (achieved = requested and safe), `qualified_other_tier`, `unsafe` (tier achieved but a safety gate failed),
`target_unmet` (no tier). The report lists every failed gate with the measured number.

## Known gap: the density bands exceed the district's storage

The v1 bands for severe (85-115) and gridlock (>= 110 veh/km/lane) were written as targets. Measurement shows the district physically
saturates at about **1,400-1,420 motor vehicles, 82-86 veh/km/lane on the interior arterials**: raising the accumulation target from 1,600
to 2,000 produced identical runs because the gateway lanes were full and nothing more could be inserted. The gridlock density gate
(110) therefore cannot be met by any state of this network, and severe (85+) is reached only at that saturation, where the network
is already in a ring deadlock. We did **not** lower the gates. Consequences:

- no shipped condition currently classifies as severe or gridlock under criteria v1;
- every report states the measured density, storage, halted share, spillback, throughput and blocked-cycle numbers so the regime can be judged directly;
- the decision to define a version-2 criteria set from measured storage (and what to call a state at 82-86 veh/km/lane) is the project owner's, and
  must be made before the numbers are used to claim a tier.
