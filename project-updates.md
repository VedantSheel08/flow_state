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
police waits for cars + lights (fixed: drivers clear the lane / slow / hold at junctions for units with lights on, pre-emption looks further ahead and steps through to the serving phase; ambulance response 2.08x -> ~1.5x free-flow)
senarios dont really show ex car crash will work but wont show on map, severe weather wont show like rain on the map (fixed)
  - car crash just shows 4 cars together looks a bit weird
the map is good however the intersections are a repetitive structure, we should try to add even diagonals curves roundabounts etc.
ui not fully good
