# FlowState Studio

A separate app for looking at the ML comparison properly and for seeing what the AI components actually receive. It does not change the lab app (`lab/`, port 5176).

```powershell
.\run-studio.ps1        # Studio UI http://localhost:5178, its own API http://127.0.0.1:8017
```

The Studio talks to its own backend instance, so its live session never replaces the one the lab UI uses on :8015. (Ports 5177 and 8016 were already in use by another process when this was built.) Both backends are the same code: `python -m flowstate.lab.cli serve --port 8017`.

## Ambulances

As on main, the simulator gives an ambulance **no priority**: it stops at red lights and queues behind other cars, and no driver pulls over. In the Compare tab:

* **Left** (no ML) has no ambulance priority.
* **Right** (ML forecast + ambulance green) has a controller that turns the lights *ahead of* an ambulance green once SafeSignal allows it. The ambulance still waits in any queue in front of it.

Each ambulance is outlined and labelled with its measured state: **moving**, **stopped at a red light** (halted within 12 m of a red or amber stop line), **stuck behind queued cars** (halted anywhere else) or **moving, green light ahead**. The state comes from SUMO's own speed and next-signal values, recorded every simulated second. Per-ambulance timelines and totals sit under the maps.

The green rule is the heuristic's existing emergency rule, not machine learning, which is why the Results tab reports it separately from the forecast.

## Tabs

* **Results**: the multi-seed held-out evaluation (60 paired runs): forest plots of the paired difference (95% bootstrap CI) and tables for the demo pair, the forecast alone, the ambulance rule alone, and adaptive vs fixed-time.
* **Compare**: recorded pairs played back in sync, with a polished renderer (road surface, lane markings, stop lines, crosswalks, signal lamps, brake lights, flashing ambulance lightbar), measured queues, dashed forecast queues, ML-intervention rings, a timeline you can scrub, ambulance timelines, and the measured differences. The banner shows this single run next to the 60-run result, so one lucky seed cannot mislead.
* **What the AI sees**: a live SUMO run with read-only observers attached. Nothing here can change a signal.

## What the AI sees

| Panel | What it shows |
|---|---|
| **Computer vision** | A **synthetic** overhead camera: the SUMO state is rendered, in the same palette as the lab's map (light land, curbs, grey asphalt, lane markings), to a noisy image (shadows, blur, illumination drift) and analysed with classical OpenCV: background subtraction, contours, and frame-to-frame overlap to tell stopped from moving. Camera-derived queue counts are scored against SUMO's own count (measured on stress traffic at two intersections: mean queue error +0.35 vehicles, mean absolute error 0.88, blob precision 99.8%). Touching cars merge into one blob, so counts use blob area divided by one car's calibrated area. It is an *upper bound* for a real camera (no weather, glare or occlusion). The road-reconstruction prototype on `task/vision` is a separate tool and is not used here. |
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
