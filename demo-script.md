# Backup demo: shot list (about 2 min 30 s of screen)

Record at 1440×900 or larger, browser zoom 100%, one take per shot so you can cut. Light theme. Close other tabs.

## Before you record

```bash
./run.sh                                                  # API on :8000, UI on :5173
.venv/bin/pip install numpy scipy gymnasium               # only for the live comparison (needs /api/rl merged)
```

- [ ] Header pill says **SUMO 1.28.0** (green dot)
- [ ] Results tab shows **Run a fresh comparison** (if it doesn't, the server has no `/api/rl` yet: skip shot 5, the recorded results still work)
- [ ] Do one throwaway live run first so SUMO is warm
- [ ] Hide the scenario rail and inspector with `\` when you want the map alone

## Shots

| # | Time | Screen | Do | Say (roughly) |
|---|------|--------|----|---------------|
| 1 | 0:00–0:15 | Simulation, Rush Hour, seed 42 | Generate & Run, 4×. Let traffic build. | "This is a real SUMO traffic simulation: every car, light and pedestrian comes from the simulator." |
| 2 | 0:15–0:45 | Road Accident, severity Severe | Generate & Run, 4×. When the pin appears, click it. Zoom in. | "A crash closes Queen Street. Traffic backs up, police and paramedics are dispatched, and the queue spreads to nearby intersections." |
| 3 | 0:45–1:10 | Ambulance Dispatch | Generate & Run, 4×. Follow an ambulance (click it, Follow). | "Emergency vehicles get signal pre-emption, and drivers pull over. Response time drops toward free-flow." |
| 4 | 1:10–1:50 | Results tab, Standard peak | Pause on the hero. Scroll slowly: tiles, then *Where it works, and where it doesn't*. Hover one dot in the scatter. | "We ran fixed-time lights and FlowState on identical traffic, 50 paired runs. Peak queues are 11% shorter, emergency vehicles stand still half as long. And we show where it loses: the rush-hour ramp we never tuned on." |
| 5 | 1:50–2:15 | Results → Run in SUMO (All patterns, 6 seeds) | Click Run. Show progress. When it finishes, show *Does it match the recording?* | "You don't have to trust our numbers: this reruns the whole comparison on fresh seeds in about 15 seconds." |
| 6 | 2:15–2:30 | Results, Over-saturated | Toggle demand. Show *The trade-off*. | "When the grid is past capacity the gains shrink and gridlock goes up. That's the honest boundary of the approach." |

## If something breaks mid-record

- Map black or blank: switch to Results and back, or reload.
- Live run fails: click **Back to recorded** and use the recorded numbers (shot 4 covers the claim).
- Server offline (red pill): restart `./run.sh`; the browser reconnects by itself.
