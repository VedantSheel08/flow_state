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
- senarios dont really show ex car crash will work but wont show on map, severe weather wont show like rain on the map (fixed) (partially tested)
  - car crash just shows 4 cars together looks a bit weird
- the map is good however the intersections are a repetitive structure, we should try to add even diagonals curves roundabounts etc.
- ui improvements needed
- dragging map pretty unresponsive
- issue with emergency vehicles overtaking system is that they cant actually overtake cuz the roads are all one lanes but its implemented if we make roads 2 lanes in places
