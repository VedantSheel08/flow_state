# Project changes

- moved configurations to a popup
- created reasonable preset configurations for each senario
- made the overview map draggable/movable to anywhere on the map
    - when an event happens at an intersection map dot will be pinned and start pulsing (not tested yet)
- created a devpost join using the link in insta
- senarious will show on map ex rain will cover map and car crashs will show (claude tests pasted and partially tested)
- render infrastructure set up but website not deployed
    - i think vedant set up rate limits and stuff on another branch so will hold off making public for now
- made touchpad zoom more responsive (about 3x stronger than before its nice now)
- emergency service vehicles can bypass lights and citizen vehicles will slow down around them when their lights are on (claude tests pass and partially tested)

# Issues
- police waits for cars + lights (fixed: drivers clear the lane / slow / hold at junctions for units with lights on, pre-emption looks further ahead and steps through to the serving phase; ambulance response 2.08x -> ~1.5x free-flow)
- senarios dont really show ex car crash will work but wont show on map, severe weather wont show like rain on the map (fixed) (partially tested)
  - car crash just shows 4 cars together looks a bit weird (fixed on krishna-updates: wrecks spun askew + skid marks, fluid stain, debris)
- the map is good however the intersections are a repetitive structure, we should try to add even diagonals curves roundabounts etc.
- ui improvements needed
- dragging map pretty unresponsive
- issue with emergency vehicles overtaking system is that they cant actually overtake cuz the roads are all one lanes but its implemented if we make roads 2 lanes in places

# Krishna (krishna-updates branch)

- new **Results** tab: FlowState vs fixed-time using Vedant's real recorded SUMO evaluation (held-out seeds 3000-3009, 5 traffic patterns, standard + over-saturated demand)
    - headline tiles, per-pattern chart with 95% intervals, every-run scatter, full table, controllers + safety limits
    - only says "clear improvement" when the interval doesn't cross zero; shows losses too (rush-hour ramp, more teleports under stress)
    - data is copied from `task/rl-signal-control` (commit 109108e) by `frontend/scripts/extract_controller_results.py` - rerun it if Vedant updates the evaluation
    - **live mode ready**: if the server has Vedant's `/api/rl` router, a "Run a fresh comparison" panel appears (fixed-time vs FlowState on new random seeds, live progress). Without it, the tab just shows the recorded results, so nothing breaks before the merge
- **Claude Design redesign applied** (canvas "FlowState Dashboard Redesign"): new tokens (caption grey now passes contrast, verdict red no longer looks like fixed-time orange), underline header tabs, calmer map floats, and the Results tab rebuilt to the design (hero, 10-second version, live states, scatter). Tab order follows Colton's change (Results last)
    - not built from the design: per-pattern progress chips and a Cancel button while a live run is going (the /api/rl API has neither); the Simulation rail/run bar got the new tokens only, not a restructure
- merge check: `krishna-updates` and `task/rl-signal-control` both merge into `main` with no conflicts
- `demo-script.md`: shot list for the backup demo recording
    - tested against Vedant's branch running locally (not merged): all 5 patterns x 3 fresh seeds ran in ~15 s, 8% less waiting / 14% smaller peak queues, rush-hour ramp still worse
    - needs `numpy scipy gymnasium` on the server for comparisons (torch only if the RL policy ever passes its gate)
- crash scenes look like crashes now (frontend renderer only, backend untouched)
- UI/design polish: Krishna doing this in Claude Design
