# FlowState frontend: performance, traffic intensity, and backend contracts

Measured on 2026-10-16. Hardware: Windows 11 laptop, Intel UHD 620 (Chrome uses it through ANGLE/D3D11), headless Chrome
1600×900, DPR 1. Backend: FlowState API with libsumo (SUMO 1.28.0), `rush_hour`, seed 4242. "Light" runs use
`demand 800`; "heavy" runs use `demand 3200, traffic 1.6` and 200+ s of simulated build-up.

## How it was measured

* `src/lib/perf.ts` records frame intervals, canvas draw time, interpolation time, WebSocket messages
  (count, bytes, handling time), React commits (via `<Profiler>`, dev builds only), long tasks
  (`PerformanceObserver`) and JS heap. Dev builds expose it as `window.__fs.perf`, production builds as
  `window.__flowstatePerf`. Production builds made with `VITE_FS_DEBUG=1` also expose `window.__fs`, for scripted profiling.
* The profiles were scripted with Playwright: a Generate & Run timeline, then 10 s windows of steady playback at 1×,
  in intersection and network views, under light and heavy demand. We also took a V8 CPU profile and a Chrome timeline trace.
* The "before" production build is commit `4f37e6b` plus only the measurement hooks.

## Root cause

The canvas re-rasterised the whole static city every frame: thousands of SUMO lane, junction, crosswalk and building polygons,
dashed markings, tree circles and an asphalt pattern. Chrome's accelerated Canvas 2D replays those commands
on the GPU process, so the JS-side draw time looked cheap (1–3 ms). The trace showed the GPU main thread saturated:
`RasterDecoderImpl::DoRasterCHROMIUM` took 4.98 s of every 5 s. Frames therefore took 50–100 ms
**regardless of traffic**. The canvas, not SUMO data volume, was the bottleneck.

Secondary costs:

* RunBar re-rendered all its Radix tooltips at 10 Hz.
* MapView re-rendered on every status change.
* `shadowBlur` was applied per braking car.
* Per-vehicle paint/sprite lookups and fresh interpolation objects were created every frame.
* Hit-testing re-interpolated all vehicles on every mouse move.

## What changed (renderer kept, not rewritten)

* **Cached static layers.**
  * The ground layer (land use, streets, markings, roofs) and the canopy layer (trees, signal masts, stop signs, labels)
    render once into off-screen bitmaps with a 20% margin, and are blitted each frame.
  * They are rebuilt only when the view settles after a zoom, when panning leaves the margin, or when layers or the
    viewport change. Mid-zoom, the last bitmap is stretched; if it no longer covers the view, the frame is drawn directly.
* **Only moving things are drawn per frame:** vehicles, pedestrians, signal lamps, incidents, the selected route and tags.
  * Pedestrians and signal heads are batched into one path per colour instead of 2–3 fills each.
  * Overview vehicle markers are batched per colour.
  * Brake lights use a halo instead of `shadowBlur`.
* **Frames are skipped when nothing changed**: paused, idle, camera still, nothing animating.
  Playback is still interpolated per animation frame between SUMO frames, so the visual frame rate is independent of
  how often frames arrive (4–20 Hz).
* **Per-vehicle memoisation:**
  * Vehicle looks (paint, livery) are memoised per SUMO id.
  * Interpolated `VehicleView` objects are reused.
  * Hit-testing uses the vehicles drawn in the last frame.
* **React:**
  * `MapView` is memoised.
  * The run clock and timeline tick in their own small components; marker tooltips re-render only when a marker is passed.

Nothing is hidden or invented. Every vehicle SUMO reports is drawn at its interpolated SUMO position and heading,
in its lane. The cull margin is unchanged.

## Results: production builds, same machine, same seed

Steady playback at 1× (10 s windows). FPS is the browser's achieved frame rate on a 60 Hz display.
Jank counts frames longer than 50 ms per 10 s.

| Traffic | View | Before: FPS / p95 frame / jank | After: FPS / p95 frame / jank | JS draw time (before → after) |
|---|---|---|---|---|
| Light (~60 vehicles) | Intersection | 15.2 / 66.8 ms / 142 | **59.9 / 16.8 ms / 0** | 2.34 → 1.46 ms |
| Light | Network | 12.5 / 83.4 ms / 127 | **60.0 / 16.8 ms / 0** | 1.43 → 0.40 ms |
| Heavy (~370–380 vehicles) | Intersection | 15.4 / 83.3 ms / 130 | **59.9 / 16.8 ms / 0** | 2.57 → 1.68 ms |
| Heavy | Network | 10.6 / 116.6 ms / 106 | **60.0 / 16.8 ms / 0** | 3.16 → 2.61 ms |

* In an extra after-run with ~460 vehicles and frames arriving at 13/s (240 KB/s), it still held 60.0 FPS with 0 jank.
* Interpolation costs 0.16–0.72 ms per frame. WebSocket handling costs 0.2–0.7 ms per message (4 frames/s plus 1 stats/s at 1×).
* JS heap is 10–33 MB after, versus 13–36 MB before.
* Production builds had zero long tasks in steady playback, both before and after.

Development builds (React dev mode, for React attribution):

* React work dropped from 230–430 ms/s to 64–75 ms/s.
* Jank frames dropped from 60–122 to 4–8 per 10 s.
* FPS went from 14–20 to 37–58. After batching the overview markers, the heavy network view also reached 60 in production.

## Dense traffic: hundreds of visible vehicles (real SUMO gridlock frames)

Measured 2026-10-17 against Codex's backend (`task/backend-performance`, run unmodified from a scratch copy).

* **Workload.** `POST /api/runs` with `{scenario: "rush_hour", seed: 4242, congestion: "gridlock_stress", params: {duration: 15}}`,
  which resolves to `demand 3200 × traffic 1.6`, `peak 3`, warm-up 180 s.
* **Build-up.** The run builds to t ≥ 300 s; then playback runs at 1×, with 730–770 vehicles active, 490–560 queued and mean speed about 4 km/h.
* **View.** Production builds at 1920×1080, panels hidden, so 600+ vehicles can be on screen at sprite zoom.
* **Visible counts.** For each target, the harness chose the camera scale by counting vehicles of the current SUMO frame
  inside the view, then measured 10 s. "Visible" is the mean on-screen count during that window. Nothing is synthetic:
  every vehicle is a SUMO vehicle at its interpolated SUMO position.

| Visible vehicles (scale) | Before (HEAD `68a9dba`): FPS · p50 / p95 / p99 / max | After: FPS · p50 / p95 / p99 / max | JS draw (before → after) |
|---|---|---|---|
| 104 → 101 (6.3 px/m) | 31.9 · 33.3 / 33.4 / 50.0 / 50.1 ms | **59.8 · 16.7 / 16.8 / 16.8 / 33.4 ms** | 3.53 → 1.75 ms |
| 344 → 338 (3.0 px/m) | 24.8 · 33.4 / 50.1 / 50.1 / 50.2 ms | **60.0 · 16.7 / 16.8 / 16.8 / 16.9 ms** | 6.32 → 4.46 ms |
| 622 → 634 (2.1 px/m) | 20.2 · 50.0 / 66.7 / 83.2 / 133.3 ms | **60.0 · 16.7 / 16.8 / 16.8 / 16.8 ms** | 10.42 → 7.34 ms |

Zooming and navigation with that traffic on screen:

| Motion | Before: FPS · p95 / max | After: FPS · p95 / max |
|---|---|---|
| Glide intersection → network | 19.8 · 116.7 / 216.6 ms | 56.7 · 16.8 / 66.6 ms |
| Glide network → intersection | 22.7 · 83.3 / 383.4 ms | 54.0 · 16.8 / 166.7 ms |
| Glide intersection → intersection | 21.9 · 66.7 / 66.8 ms | 53.4 · 16.8 / 183.3 ms |
| Wheel zoom out, 12 notches | 24.5 · 100.0 / 133.4 ms | 59.2 · 16.8 / 50.0 ms |
| Wheel zoom in, 12 notches | 18.9 · 100.0 / 466.6 ms | 54.2 · 16.8 / 199.9 ms |

The remaining single long frame (~170–200 ms) per glide is the sharp bitmap being rebuilt once, after the camera stops.
WebSocket frames carried 114–119 KB/s and took 0.71–0.74 ms to parse and handle; interpolation took 0.9 ms per frame.

### Second root cause (found with these measurements)

At 1920×1080 the GPU process was saturated again, about 1000 ms/s busy on Canvas raster replay. Live layer toggles and a
micro-benchmark on the same GPU attributed it to two things:

* **Signal heads.** They were rebuilt as fresh vector paths every frame for every head in the city, so the GPU
  re-tessellated circles each frame. Turning just this layer off took FPS from 36 to 60.
* **Vehicle sprites.** Each vehicle was drawn from its own small canvas, with brake-light rectangles interleaved between
  sprites. Chrome only batches consecutive `drawImage` calls from the same source. Measured on this GPU, 800 rotated
  sprites ran at 31.9 FPS from 13 source canvases, 58.7 FPS from one atlas, and 24.8 FPS with two rectangles
  interleaved per sprite.

Fixes, all in `src/map`:

* **`atlas.ts`.** Every sprite (vehicle bodies, network-view markers, signal lamps, pedestrians) is shelf-packed into
  shared 2048² atlas pages, so consecutive draws batch.
* **Brake lamps are baked into sprite variants.** Indicators, emergency beacons, hazard flashers and the selection
  outline are drawn in a second pass after all bodies.
* **Signal housings are static geometry in the cached canopy layer.** Only the lamps are drawn live, as atlas discs,
  culled to the view.
* **Whole-city overview bitmaps.** Built once in idle time; a zoom or glide that leaves the detailed bitmap blits these
  instead of re-rasterising the city.

Visual checks at these densities, with screenshots taken during the runs:

* Lane alignment and spacing in bumper-to-bumper queues.
* Brake lamps.
* Signal housings and lamps.
* Bus liveries and route numbers.
* Ambulance and police light bars.
* Network-view markers.

## Generate & Run: where the time goes

Worst case: replacing an already busy heavy run (`demand 3200, traffic 1.6`) with another. Production build, clicked in-page:

| Milestone | Run 1 | Run 2 | Owner |
|---|---|---|---|
| Click handled, state `starting` | 1.9 ms | 2.5 ms | frontend |
| Progress card painted | 30 ms | 21.5 ms | frontend |
| `warming` status received (WebSocket) | 551 ms | 377 ms | backend: stop old run, draw scenario, start SUMO |
| `POST /api/runs` returned | 619 ms | 391 ms | backend |
| `running` and first frame of the new run | 3088 ms | 3432 ms | backend: 90 s warm-up at ~30–40× real time under heavy demand |
| Main-thread long tasks during the whole sequence | 0 | 0 | |

A first run from idle is ready in about 1.4 s; a light run replacing another in about 0.9 s.

The perceived freeze was two things:

* **Backend time.** The scenario build plus warm-up has to finish before any frame exists.
* **Frontend stalls on top.** The old UI showed only a small banner while the canvas ran at 10–15 FPS. In development
  builds, 100–160 ms React commits followed each status message.

The frontend now responds within one frame and shows each backend phase with live timing.

`LaunchProgress` replaces the old banner. It shows the three real phases with live timings:

1. Building scenario (POST pending).
2. SUMO warm-up (`status.t` / `status.warmup`).
3. Streaming live traffic (first frame of the new run).

Its indeterminate bar is a transform-only CSS animation, so it keeps moving even when the main thread is busy.

## Traffic intensity presets

The selector writes the backend's existing scenario parameters into the draft for the next run. No client-side traffic is created.

| Preset | `demand` (trips/h) | `traffic` (live multiplier) |
|---|---|---|
| Light | 0.5 × scenario default | 1.0 |
| Normal | scenario default | 1.0 |
| Rush Hour | 1.5 × scenario default | 1.0 |
| Severe | `demand.max` (3200) | 1.0 |
| Gridlock | `demand.max` (3200) | `traffic.max` (1.6) |

Values are clamped and rounded to each parameter's `min`/`max`/`step` from `/api/scenarios`. Anything else shows as
"Custom". Scenario components (commuter peak, event surges, weather) still shape demand on top. While a run is
active, only `traffic` can change live (Live control → Traffic level); `demand` applies to the next run.

What SUMO actually produced. Rush Hour scenario, seed 4242, 10 min after the 90 s warm-up, run headless through the API at 16×:

| Preset | demand × traffic | POST | Warm-up done | Vehicles @5 min | Vehicles @10 min | Queued @10 min | Mean speed | Time loss / trip | Waiting to enter | Teleports | Achieved sim speed (asked 16×) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Light | 800 × 1.0 | 0.62 s | 1.40 s | 76 | 49 | 13 | 20.5 km/h | 85 s | 0 | 0 | 16.0× |
| Normal | 1600 × 1.0 | 0.30 s | 1.15 s | 153 | 125 | 35 | 15.7 km/h | 98 s | 0 | 0 | 16.0× |
| Rush Hour | 2400 × 1.0 | 0.32 s | 1.94 s | 264 | 258 | 106 | 8.4 km/h | 121 s | 5 | 0 | 11.0× |
| Severe | 3200 × 1.0 | 0.29 s | 1.82 s | 342 | 383 | 194 | 7.0 km/h | 126 s | 16 | 3 | 9.6× |
| Gridlock | 3200 × 1.6 | 0.03 s | 2.68 s | 561 | 726 | 494 | 3.5 km/h | 143 s | 66 | 5 | 5.3× |

The "empty" look of the default rush hour is a demand-level fact: at `demand 1600` SUMO holds about 125 vehicles across
16 intersections, around 8 per intersection.

### Backend congestion presets (Codex contract, `task/backend-performance`)

When the server answers `GET /api/congestion-presets`, the selector shows those presets instead of the client mapping
above. It uses the backend's ids (`light`, `normal`, `rush_hour`, `severe`, `gridlock_stress`) and shows the full
labels in tooltips. The backend's honesty flags are shown on the badges: version, warm-up, stress test, not calibrated,
safety not validated. The backend's own description is shown too.

* **Request.** `POST /api/runs` is sent with `congestion: <id>`. Because explicit `params` override the preset
  (`resolve()` merges `{...presetDefaults, ...params}`), the frontend leaves out every preset-owned key the user has not
  edited: `duration`, `demand`, `traffic`, and `peak` for `rush_hour`. Otherwise the scenario defaults would silently
  replace the preset. Values the user does edit by hand are sent, and the UI says they override the preset.
* **Display.** The run panel shows the resolved values the server returns (`run.params`, `run.warmup`,
  `run.congestionPreset`), not the preset name alone.
* **Restart and new variation.** These resend the resolved params together with the `congestion` id, so warm-up and
  metadata match.
* **Fallback.** Servers without the endpoint (404, e.g. the current `main` backend) fall back to the client mapping that
  writes `demand`/`traffic` directly.
* **Verified end to end against Codex's backend.** The UI sent
  `{"scenario":"rush_hour","params":{"bike_share":5,"pedestrians":true,"signals":"fixed","period":"am","variability":0.5},"seed":4242,"speed":1,"congestion":"gridlock_stress"}`,
  and the server resolved `demand 3200, traffic 1.6, peak 3, duration 5, warmup 180`.

## Backend coordination (signals and scenarios agents)

No backend change is needed for anything in this branch. Items worth coordinating:

1. **Calibrated defaults (scenarios).** If "Rush Hour" should look busy by default, raise the default `demand`
   (e.g. 2400) or the commuter `peak`, and/or raise `traffic.max` above 1.6. The presets follow the spec's
   `default`/`max` automatically.
2. **Optional first-class intensity (scenarios).** A `ParamSpec` such as
   `{ key: "intensity", type: "select", options: [...], live: false }`, or a `presets` list on `ScenarioSpec`, would
   let the backend own the calibration. The UI would render it in place of the client-side mapping. Contract:
   each preset must resolve to concrete `demand`/`traffic` values echoed back in `RunInfo.params`.
3. **Non-blocking run start (API).** `POST /api/runs` currently stops the previous run (joining its thread, up to 10 s)
   and draws the scenario before responding: 90–880 ms measured. Returning immediately with
   `{ id, state: "starting" }` and publishing `status` updates for "stopping previous" and "building" would let the UI
   show those sub-phases. Warm-up then runs at full speed and reports progress at 5 Hz, which the UI already shows.
4. **Frame payload (API).** Heavy traffic is about 60–240 KB/s of JSON frames (`[id, type, x, y, angle, speed, signals]`
   per vehicle). Parsing costs 0.6–0.7 ms per frame here, which is fine. If vehicle counts grow 10×, a binary or
   delta encoding with an id table would keep it flat.
5. **Signal control for AI (signals).** The renderer draws signal heads from each frame's `tls` state strings,
   indexed by SUMO link (`LaneGeom.links`). A controller that changes phases through TraCI/libsumo is reflected
   automatically, with no frontend change. Useful additions: a per-junction `program`/`controller` field in `stats`
   (fixed, actuated, or the name of an AI policy), and a `signal` event when a controller overrides a phase, so the
   inspector and event list can attribute changes.
6. **Sim throughput (engine).** Achieved simulation speed drops to about 5× at gridlock demand (SUMO step cost). Not a
   frontend issue, but it bounds fast-forward for AI training or evaluation in the live server.

7. **Gridlock simulation speed on `task/backend-performance` (Codex).** With junction collision checking and warnings
   enabled, a `gridlock_stress` run achieved **0.82× real time** at 1× requested (`stepMs` ≈ 295 ms), so the UI receives
   about 3 frames/s. Playback stays smooth because the frontend interpolates between frames, but a gridlock demo cannot
   fast-forward.
8. **Preset/scenario defaults (Codex + scenarios).** `severe` and `gridlock_stress` resolve to the same `demand`/`traffic`
   and differ only in `rushHourPeak` (2.5 vs 3.0), so outside `rush_hour` they are identical. The UI shows each preset's
   resolved values in its tooltip and labels both as stress tests.

## MapLibre evaluation (not migrated)

* **Fit.** MapLibre GL JS 6.x (BSD-3, ~28 MB unpacked, ~800 KB min+gz runtime) renders vector-tile basemaps on WebGL with
  smooth zoom/rotate. It would replace the synthetic background (blocks, buildings, trees, labels), not the traffic layer.
* **Blocker: no geographic reference.** The SUMO network is synthetic and Cartesian
  (`<location projParameter="!"/>`, `convBoundary -160,-160,760,640`). A basemap needs real coordinates. That means
  either importing a real district with `netconvert --osm` (which supplies `projParameter`, and SUMO can then report
  lon/lat), or inventing an anchor point, which would show a real city under a fictional grid.
* **Traffic on top.** Vehicles would become a custom WebGL layer (MapLibre `CustomLayerInterface`) or a deck.gl
  overlay, with sprites in a texture atlas. That is a rewrite of `renderer.ts`'s dynamic half.
* **Cost/benefit now.** With cached static layers the current Canvas 2D path holds 60 FPS at about 460 vehicles on
  integrated graphics, so the motivation would be real-world maps and tiles, not speed.
* **Recommendation.** Revisit when a real OSM-derived network is adopted. Then move static context to MapLibre and keep
  the traffic layer in WebGL (deck.gl `IconLayer` or a custom layer) driven by the same `FrameBuffer`.
