# FlowState Studio

A separate (earlier, dark-themed) app for looking at the forecast-assisted comparison properly and for seeing what the AI components actually receive. It does not change the lab app (`lab/`, port 5176).

```powershell
.\run-studio.ps1        # Studio UI http://localhost:5178, its own API http://127.0.0.1:8017
```

The Studio talks to its own backend instance, so its live session never replaces the one the lab UI uses on :8015. (Ports 5177 and 8016 were already in use by another process when this was built.) Both backends are the same code: `python -m flowstate.lab.cli serve --port 8017`.

## Ambulances

As on main, the simulator gives an ambulance **no priority**: it stops at red lights and queues behind other cars, and no driver pulls over. In the Compare tab:

* **Left** (no ML) has no ambulance priority.
* **Right** (forecast-assisted + ambulance corridor) has a controller that holds the lights green along the ambulance's route (next three junctions, 450 m) as SafeSignal allows. The ambulance still waits in any queue in front of it, and everyone else pays for the held greens (see the Results tab).

Each ambulance is outlined and labelled with its measured state: **moving**, **stopped at a red light** (halted within 12 m of a red or amber stop line), **stuck behind queued cars** (halted anywhere else) or **moving, green light ahead**. The state comes from SUMO's own speed and next-signal values, recorded every simulated second. Per-ambulance timelines and totals sit under the maps.

The green corridor is a rule (route-aware preemption), not machine learning, and no learned model decides it, which is why the results report it separately from the forecast.

## Tabs

* **Results**: the multi-seed held-out evaluation (60 paired runs): forest plots of the paired difference (95% bootstrap CI) and tables for the demo pair, the forecast alone, the ambulance rule alone, and adaptive vs fixed-time.
* **Compare**: recorded pairs played back in sync, with a polished renderer (road surface, lane markings, stop lines, crosswalks, signal lamps, brake lights, flashing ambulance lightbar), measured queues, dashed forecast queues, ML-intervention rings, a timeline you can scrub, ambulance timelines, and the measured differences. The banner shows this single run next to the 60-run result, so one lucky seed cannot mislead.
* **What the AI sees**: a live SUMO run with read-only observers attached. Nothing here can change a signal.

## What the AI sees

| Panel | What it shows |
|---|---|
| **Computer vision** | A **synthetic** overhead camera: the SUMO state is rendered (4.5 px per metre, in the same palette as the lab's map, with noise, shadows and illumination drift) and analysed with classical OpenCV: background subtraction, connected components, then **one oriented box per car**. A lone car is its blob's rectangle; cars that still touch are cut along the lane into the number their area implies. Stopped vs moving comes from the overlap with the previous second. Every box is scored against SUMO's own vehicle list. Measured on stress traffic at two intersections over 620 simulated seconds (52 frames, about 42 cars in view): **per-car precision 98.9%, recall 99.3%**, count error +0.21 cars (mean absolute 0.48), queue-count error -0.1 vehicles (mean absolute 1.1; 1.6 on queues of 10+). 3 px/m was tried first and merged queued cars (79% precision); 6 px/m was no better and slower. Compute time was 190-320 ms per frame on a busy machine. It is an *upper bound* for a real camera (no weather, glare or occlusion), and a trained detector would be the next step. The road-reconstruction prototype on `task/vision` is a separate tool and is not used here. |
| **Queue forecaster** | The exact 16 x 19 feature matrix it is given, its 30 s and 60 s predictions with the 90% calibrated intervals, and its global feature importance. |
| **PPO agent** | The 33-number observation each junction's policy receives, its action probabilities, and whether it agrees with the active controller. The checkpoint (`run4/best_validation.zip`) **failed its publication gate**, so it is a shadow only and never controls anything. It loads only if `torch` and `stable-baselines3` are installed. |
| **Gemini** | The exact system prompt and measured-facts JSON it is sent (size in tokens), its draft, the number-grounding verdict (every number must appear in the facts), and the answer shown. With no key, or on any error, the answer is built from the same facts and says so. Gemini never sees raw simulation state or any control API. |

## Optional dependencies

`pip install -r backend/requirements-studio.txt` (OpenCV, gymnasium, stable-baselines3, CPU torch). Each panel degrades with an explanation when its dependency is missing.

## API (additive; backend instance of the lab app)

`GET /api/studio/meta`, `POST /api/studio/gemini`, `GET /api/studio/replays/{id}/story`, plus everything already in `/api/lab/*` and `/api/intelligence/v1/*`.
The live WebSocket accepts `{"cmd":"start", ..., "perception": true, "camera": "A0"}` and `{"cmd":"camera","junction":"B1"}`; each 5 s `obs` message then carries `perception`.
The v1 live report now includes `observed.emergencyVehicles` (measured state per ambulance) and `interventions.controller.emergencyGreen`.

## Limitations

* The grid is the 2x2 synthetic network, not the Toronto district.
* The camera is synthetic and the CV is classical; it demonstrates the pipeline and its error against ground truth, not real-world accuracy.
* PPO is shown for inspection only: it underperforms the heuristic and is not offered for control.
* The forecast does not reliably improve traffic; the ambulance rule helps ambulances a lot and leaves others unchanged. Both statements are in the Results tab with their intervals.

## Map rendering in the light lab

The light-mode lab (`intelligence-ui-v2/lab`) draws its maps with the **same renderer as the real FlowState app**: `lab/src/mainmap/` is vendored from main's `frontend/src/map` (unchanged apart from import paths), and `MainMap.tsx` drives it. Roads, curbs, sidewalks, lane markings, crosswalks, signal masts, buildings, trees and the vehicle sprites (including the ambulance light bar) are therefore main's own drawing code.

* **Map description:** `python -m flowstate.lab.cli mapgeom` writes `artifacts/lab/grid-network.json` using main's `network_geometry` on a display-only copy of the grid (sidewalks and crossings added by `netconvert`; the simulation network is untouched, and signal links are mapped back to the simulation's numbering). Copy it to `lab/public/grid-network.json`.
* **Decoration:** the grid is synthetic, so its blocks, buildings, trees and park are **generated** (fixed seed), not OpenStreetMap data. The real app's buildings come from OSM for Toronto.
* **Cars** keep a fixed colour in the comparison and live views. In "What the AI is looking at", the same map is tinted by perceived state (stopped, slow) with the camera's detection boxes, its per-approach counts against SUMO's, and a tag on each emergency vehicle. That map is held at the instant of the camera frame, so each box sits on the car it describes.
