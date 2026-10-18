# FlowState — Urban Traffic Simulation

FlowState is an interactive microscopic traffic simulator built on **Eclipse SUMO**. A FastAPI backend drives
SUMO in-process (libsumo), generates seeded stochastic scenarios, and streams authoritative state over a
WebSocket. A React + TypeScript frontend renders the real SUMO lane geometry, vehicles, pedestrians and signals
on a Canvas 2D map, with controls, an inspector and analytics.

Everything on the map comes from SUMO. The frontend only interpolates between real simulation steps.

## Quick start

Requires Python 3.10+ and Node 18+. SUMO itself is installed from PyPI (`eclipse-sumo`, `libsumo`).

```powershell
.\run.ps1          # Windows
./run.sh           # macOS / Linux
```

Then open <http://127.0.0.1:5173>. The script creates `.venv`, installs dependencies, builds the networks if
they are missing, and starts the API on port 8000 and the UI on port 5173.

To launch manually:

```bash
python -m venv .venv && .venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && python -m flowstate.cli build-network      # once
python -m flowstate.cli serve --port 8000                # terminal 1
cd frontend && npm install && npm run dev                # terminal 2
```

**Intelligence Lab** (queue forecasting and a forecast-assisted signal heuristic, compared with the plain heuristic on real SUMO runs; standalone): `.\run-lab.ps1`, then open <http://localhost:5176>. A light-mode redesign of the lab lives on the `task/intelligence-ui-v2` branch. Only the queue forecaster is a trained model: the controllers and the ambulance green corridor are hand-written rules, and the PPO agent failed validation and controls nothing. See [docs/INTELLIGENCE_LAB.md](docs/INTELLIGENCE_LAB.md) (results and what is learned vs a rule) and [docs/STUDIO.md](docs/STUDIO.md) (an earlier dark prototype: `.\run-studio.ps1`, <http://localhost:5178>).

## Using it

1. Choose a scenario in the left rail. Adjust its parameters; *Simulation settings* holds run length, demand,
   cyclists, pedestrians and signal control.
2. Leave **Seed** empty for a new random variation, or enter one to reproduce a run exactly. Press
   **Generate & Run**.
3. The backend draws a concrete instance from the seed (for example *"Severe collision, all lanes closed on
   Church St southbound between Queen and King, 03:40 → 21:29"*), runs a 90 s SUMO warm-up, and starts streaming.
   The right panel shows exactly what was drawn.
4. Use the transport bar for pause/resume, **restart with the same seed**, **new variation**, stop, and speed
   (1–16×). The clock shows simulated time and real elapsed time separately. If the machine can't keep up,
   the achieved speed is shown.
5. Click a vehicle to inspect its SUMO state and driver profile, and to follow it. Click an intersection for
   live phases, a countdown and approach queues. Events are clickable.
6. **Analytics** shows time series from live measurements, per-mode trip statistics and a **controlled
   comparison**: two headless SUMO runs with the same seed that differ in exactly one setting (signal control,
   pre-emption, transit priority, King Street design, or weather vs. clear).
7. **Run history** lists every generated run with its seed. **Reproduce** regenerates it exactly.

Keyboard: `Space` play/pause · `R` restart same seed · `N` new variation · `H` default view · `F` fit network ·
`+`/`-` zoom · `Esc` clear selection.

## The simulation

**Network.** *Riverside District* is a synthetic, reproducible downtown network generated with `netconvert`
(`backend/flowstate/network/city.py`):

- 4 × 4 street grid. Arterials (Main, King, Queen) have 2 lanes per direction, a planted median and 38 m
  left-turn bays with protected-left phasing. Collectors (Front, Church) have painted bike lanes. Local streets
  are controlled by all-way stops.
- Sidewalks, crosswalks and walking areas everywhere. Pedestrians use SUMO's striping model.
- 11 signalised intersections with 4 s amber, 2 s all-red and pedestrian walk / flashing-don't-walk intervals.
- Points of interest: hospital, police division, school, stadium and park, plus bus lines 501 Queen and 6 Main
  with far-side stops.
- A variant network, `district_bike`, reallocates King Street's median and turn bays to protected bike lanes.

**Driver behaviour.** Car following uses IDM; lane changing uses SL2015 with the sublane model (0.8 m lateral
resolution). Each run draws 8 driver profiles per vehicle class from bounded normal distributions (headway,
acceleration, deceleration, gap, lane-change eagerness) using the run seed. SUMO adds per-vehicle desired-speed
factors. Vehicle dimensions follow North-American averages. Emergency vehicles use SUMO's bluelight device.

**Demand.** Trips are Poisson arrivals from a time-varying origin–destination model: network fringe by compass
side, or city-block frontages (driveways and parking). Scenarios can add venue attractors and generators.
Routes are SUMO shortest-time routes, and 35% of vehicles carry the periodic rerouting device.

**Signals.** There are two base modes, both executed by SUMO: *fixed-time* (the netconvert plans) and
*actuated* (SUMO's gap-based actuated control with automatic detectors). Emergency pre-emption and transit
signal priority only hold or end the current green early, and never skip amber, all-red or pedestrian clearance.
New strategies can subclass `SignalPolicy` in `sim/signals.py`.

**Scenarios** (`scenarios/catalog.py` composes reusable components from `scenarios/components.py`):

| Scenario | What the seed draws | What is measured |
| --- | --- | --- |
| Rush Hour | peak intensity, timing, OD imbalance | delay, queues, throughput |
| Road Accident | location (any valid mid-block segment), severity, lanes, time, clearance, responder delay | queue behind the scene, detours, responder arrival |
| Ambulance Dispatch | call times and scenes | response time vs. free-flow, pre-emptions |
| Police Pursuit | origin, evasive re-targeting, unit dispatch | time to stop, closest approach |
| Bike Lane Conversion | demand (network chosen by parameter) | cyclist speed, car travel time, corridor speeds |
| Construction | location, lane or full closure | merge queue, detour |
| Stadium Event | attendance, surge timing | venue queues, network delay |
| School Dismissal | bell time, pick-up arrivals | frontage queues, pedestrian volumes |
| Transit Priority | departures with headway jitter, boardings | bus travel time, timetable delay, green extensions |
| Severe Weather | condition and severity | speed and delay vs. clear (via comparison) |
| Random Urban Disruption | 2–4 non-overlapping disruptions | network delay, response |

Same seed + same parameters → identical scenario instance and identical SUMO trajectories (tested).

## Architecture

```
backend/flowstate/
  network/   city.py (network generator + city layout), geometry.py (render geometry from .net.xml), info.py
  sim/       engine.py (libsumo/TraCI loop, frames, incidents), demand.py, signals.py, metrics.py, vtypes.py, events.py
  scenarios/ base.py (ParamSpec, Scenario, Component), components.py, catalog.py
  api/       app.py (REST + /ws), service.py (live run thread, pacing, history, comparison jobs)
  cli.py     build-network | serve | scenarios | run
frontend/src/
  map/       scene.ts (Path2D layers from SUMO geometry), renderer.ts, vehicles.ts (procedural sprites),
             camera.ts, geom.ts, MapView.tsx, MapControls.tsx
  lib/       frames.ts (playout buffer + Hermite interpolation), ws.ts, api.ts, store.ts, actions.ts
  components/ Header, ScenarioRail, ParamField, RunBar, RightPanel, ui/
  views/     AnalyticsView, RunsView
tests/       pytest suite (network, scenarios, engine runs, API/WebSocket)
data/        networks/, runs/ (run records), work/ (SUMO scratch)
```

**Rendering.** Canvas 2D with cached `Path2D` layers in world metres: asphalt from SUMO lane polygons, junction
shapes, walking areas, continental crosswalks, stop bars, turn arrows derived from SUMO connections,
dashed/solid/yellow markings, a planted median, bike-lane paint with delineators, bus stop boxes and shelters,
overhead signal heads per lane, and stop signs. Vehicles are procedural top-down vector sprites at true
dimensions, cached per zoom bucket: sedan, hatchback, SUVs with roof rails, pickups with open beds, vans, box
trucks, city buses, ambulances, police cruisers and cyclists. Brake lights, indicators and beacons come from
SUMO's signal bits. Positions use cubic Hermite interpolation with SUMO heading and speed as tangents, so
vehicles pass exactly through every SUMO position and follow turning arcs. Nothing is extrapolated.

## Tests

```bash
.venv/Scripts/python -m pytest tests -q        # 46 tests, ~25 s
cd frontend && npm test && npm run build
```

## Known limitations

- The network is synthetic. An OpenStreetMap import was not added in this iteration. SUMO's `netconvert --osm`
  plus `geometry.py` would support one, but city layout data (buildings, POIs) would need its own importer.
- The weather scenario is a sensitivity model: factors fall within published ranges but are not calibrated.
- The pursuit model is simplified: re-targeting suspect, units chasing its current or next street, contact rule.
- School pick-up stops are curb-lane stops. Parking manoeuvres are not modelled.
- Bus boardings are drawn per stop. Waiting passengers are not simulated as persons.
- At very high speeds (16×) with dense traffic, a laptop may not sustain real-time ratio. The UI shows the
  achieved rate.
- `libsumo` allows one simulation per process, so comparison jobs run in subprocesses. Set
  `FLOWSTATE_BACKEND=traci` to use TraCI instead (about 8× slower).
- The previous implementation (3×3 grid, custom adaptive controller, recorded replays) is preserved in
  `archive/legacy_v1/`.
