# Project changes

- moving configurations to a popup
- creating reasonable preset configurations for each senario
- made the overview map draggable to anywhere
    - when an event happens at an intersection map dot will be pinned and start pulsing (not tested yet)
- created a devpost
- senarious will show on map ex rain will cover map and car crashs will show
- render infrastructure set up but website not deployed
    - i think vedant set up rate limits and stuff on another branch so will hold off making public for now

# Issues
police waits for cars + lights (working on this rn)
senarios dont really show ex car crash will work but wont show on map, severe weather wont show like rain on the map (fixed)
  - car crash just shows 4 cars together looks a bit weird (fixed on krishna-updates: wrecks spun askew + skid marks, fluid stain, debris)
the map is good however the intersections are a repetitive structure, we should try to add even diagonals curves roundabounts etc.
ui not fully good

# Krishna (krishna-updates branch)

- new **Results** tab: FlowState vs fixed-time using Vedant's real recorded SUMO evaluation (held-out seeds 3000-3009, 5 traffic patterns, standard + over-saturated demand)
    - headline tiles, per-pattern chart with 95% intervals, every-run scatter, full table, controllers + safety limits
    - only says "clear improvement" when the interval doesn't cross zero; shows losses too (rush-hour ramp, more teleports under stress)
    - data is copied from `task/rl-signal-control` (commit 109108e) by `frontend/scripts/extract_controller_results.py` - rerun it if Vedant updates the evaluation
    - not live yet: once `/api/rl` is merged, the tab can also run fresh comparisons
- crash scenes look like crashes now (frontend renderer only, backend untouched)
- UI/design polish: Krishna doing this in Claude Design
