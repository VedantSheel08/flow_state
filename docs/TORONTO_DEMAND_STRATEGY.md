# Toronto observations and modeled district demand

The earlier audit pinned official City of Toronto turning-movement count **112619**, Danforth Avenue / Main Street, **2025-06-24**, with 56 fifteen-minute rows. This is one observed intersection on one date, not the synthetic Riverside District's OD matrix.

Observed motor-vehicle counts from that pinned extract:

| Window | North approach | East | South | West | Total |
|---|---:|---:|---:|---:|---:|
| 08:00–09:00 | 230 | 778 | 446 | 460 | 1914 |
| 11:00–12:00 | 159 | 634 | 370 | 463 | 1626 |
| 17:15–18:15 | 271 | 644 | 517 | 545 | 1977 |

AM fifteen-minute motor totals: 447, 492, 510, 465. PM: 482, 490, 502, 503. AM bicycle count 80; PM 174. A five-minute draw based on these bins is **modeled**, not an observed five-minute count. Bus/truck/bicycle/pedestrian approach columns must retain their own definitions; do not lump crossing pedestrians into vehicle OD demand. Source columns, count ID, date, bin boundaries, resource ID and licence should accompany any calibration output.

Reproducible source retrieval: the [Toronto dataset](https://open.toronto.ca/dataset/traffic-volumes-at-intersections-for-all-modes/) is published through CKAN. Resolve package `traffic-volumes-at-intersections-for-all-modes` using `/api/3/action/package_show`; the raw resource used by the audit was `262469c2-abfe-4756-9068-4ea5c7ba1af7`. Fetch `/api/3/action/datastore_search` with that `resource_id`, filters `{"count_id":112619}`, `limit=100`, `sort=start_time asc` (URL-encode query values). Archive the response and its SHA-256, validate record count/date/15-minute intervals, then independently sum each approach's cars+trucks+buses across right/through/left columns. Resource IDs can change; resolve the package again if the pinned resource is retired. [Toronto licence](https://open.toronto.ca/open-data-licence/).

The current presets are modeled **district trip requests per hour**, with the existing seeded OD distribution and fleet mix. Their rates are not copies of these intersection totals. Rush components further vary demand and trip-end direction over time. Road storage and approach service capacity depend on actual lane lengths/counts, green share, turning conflicts, pedestrian demand, yielding, saturation discharge and spillback; there is no defensible universal “cars per road” multiplier.

The signals/demand-calibration owner should implement this staged calibration:

1. Keep the current synthetic district label. Select representative arterial/collector movements and explain the analogy; a real Toronto claim would require a matched real network and controller plans.
2. Pin several official nearby count locations/time windows, not just Danforth/Main. Extract approach turning fractions, heavy-vehicle and bicycle shares, pedestrian crossings, and their uncertainty/date. Separate boundary through trips from internal trip ends so vehicles are not counted as new demand each time they cross another junction.
3. Build a movement incidence matrix from **valid SUMO routes**: each OD path contributes to movements it actually traverses. Fit nonnegative OD rates to target movement totals with regularization toward plausible residential/commercial trip ends. Keep structurally impossible routes zero. A constrained fit to one representative intersection can anchor scale/direction; it cannot identify a whole district's OD matrix uniquely.
4. Use observed fifteen-minute bins as piecewise intensity inputs with seeded stochastic arrivals. Simulate warm-up outside the 300 s observation window using the preceding comparable peak conditions. Do not compress an entire rush hour into five minutes or promise observed five-minute outcomes. Use fixed conventional signals supplied by the signals owner.
5. Fit against one set of counts/seeds and validate against held-out movements/times/seeds. Report movement error, absolute count differences, queue distributions, effective throughput, insertion delay, pending/discarded demand, teleports and collisions. Do not increase offered demand to conceal an insertion bottleneck.
6. Treat light/normal/rush as modeled exploratory settings until that validation passes. Severe and gridlock remain explicitly labeled stress tests. Increasing offered demand need not increase completed trips, and a short sample need not produce literal gridlock.

No calibration or signal-owner files were modified by this branch. The immediate safe interface is the documented preset → existing scenario parameters mapping, plus measured native outcomes. Applying this calibration strategy and fixing the compressed peak profile are pending owner integration.
