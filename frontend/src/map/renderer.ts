// Canvas 2D renderer. World geometry is drawn as cached Path2D objects under a
// camera transform; text and badges are drawn in screen space for crispness.

import type { PersonView, VehicleView } from '../lib/frames'
import type { Layers } from '../lib/store'
import type { Incident, Tracked } from '../lib/types'
import { Camera } from './camera'
import { drawOverlayPins, drawOverlayZones, type OverlayHit, type PlacedOverlay, type Rect } from './overlays'
import { along, hash, offset, polyLength, slice, strip, type Pt } from './geom'
import type { Scene } from './scene'
import { CODE_TO_TYPE, DIMS, LIGHTBAR, busLineFor, cargoFor, dotSprite, markerSprite, paintFor, personSprite, spriteBucket, spriteFor, type VType } from './vehicles'

export const C = {
  land: '#E8EAE6',
  residential: '#ECEDE8',
  commercial: '#E9EAEC',
  park: '#D5E5C8',
  school: '#ECEAE3',
  hospital: '#EBEBEE',
  police: '#E8E9EC',
  stadium: '#E3E5E8',
  sidewalk: '#DCDEE1',
  curb: '#B7BCC3',
  asphalt: '#5E636B',
  asphaltJ: '#646971',
  bike: '#6F9B7C',
  white: 'rgba(246,246,242,0.92)',
  yellow: '#E2B93B',
  shadow: 'rgba(31,41,55,0.16)',
  roofLine: '#B9BFC7',
  tree: '#A8C79A',
  treeLight: '#BCD7AE',
  treeShadow: 'rgba(35,55,35,0.13)',
  water: '#C3D9EA',
  label: '#4B5563',
}

const SIG: Record<string, string> = { G: '#22C55E', g: '#4ADE80', y: '#F5B700', Y: '#F5B700', r: '#EF4444', R: '#EF4444', u: '#F5B700', o: '#9CA3AF', O: '#9CA3AF', s: '#EF4444' }
const RANK: Record<string, number> = { G: 4, g: 3, y: 2, Y: 2, u: 2, r: 1, R: 1, s: 1, o: 0, O: 0 }

// Aggregate grain for asphalt: a deterministic speckle tile, repeated every few metres.
let grain: CanvasPattern | null | undefined
function asphaltGrain(ctx: CanvasRenderingContext2D): CanvasPattern | null {
  if (grain !== undefined) return grain
  const N = 128
  const tile = document.createElement('canvas')
  tile.width = tile.height = N
  const t = tile.getContext('2d')
  if (!t) return (grain = null)
  let seed = 1234567
  const rnd = () => ((seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296)
  for (let i = 0; i < 2600; i++) {
    const light = rnd() < 0.45
    t.fillStyle = light ? `rgba(255,255,255,${0.05 + rnd() * 0.07})` : `rgba(0,0,0,${0.05 + rnd() * 0.08})`
    const r = 0.4 + rnd() * 1.1
    t.fillRect(rnd() * N, rnd() * N, r, r)
  }
  grain = ctx.createPattern(tile, 'repeat')
  grain?.setTransform(new DOMMatrix().scale(5 / N))
  return grain
}

export interface RenderState {
  vehicles: VehicleView[]
  persons: PersonView[]
  tls: Record<string, string>
  layers: Layers
  selected: string | null
  selectedJunction: string | null
  hoverEdge: string | null
  pickedEdge: string | null
  tracked: Map<string, Tracked>
  incidents: Incident[]
  edges: Record<string, [number, number, number]>
  route: string[] | null
  busLines: { id: string; color: string }[]
  now: number
  pick: boolean
  /** scenario footprints for this run, and the sim time that decides which are live */
  overlays: PlacedOverlay[]
  t: number
  /** screen area clear of floating panels, where off-screen indicators may sit */
  safe: Rect
}

/** Click targets of the scenario pins drawn in the latest frame. */
export let overlayHits: OverlayHit[] = []

/** Draws one frame; returns how many vehicles were drawn (inside the view after culling). */
export function render(ctx: CanvasRenderingContext2D, scene: Scene, cam: Camera, st: RenderState, dpr: number): number {
  const s = cam.scale
  const W = cam.width
  const H = cam.height
  const k = s * dpr
  const world = () => ctx.setTransform(k, 0, 0, -k, (W / 2 - cam.x * s) * dpr, (H / 2 + cam.y * s) * dpr)
  const px = 1 / s // one CSS pixel in metres

  // ---- static ground (cached bitmap; redrawn only when the view changes) --
  drawLayer(ctx, groundCache, scene, cam, st, dpr, drawGround)

  // ---- dynamic, under traffic ---------------------------------------------
  world()
  // ---- traffic flow overlay -------------------------------------------
  if (st.layers.flow) {
    ctx.lineCap = 'round'
    for (const [eid, [n, , v]] of Object.entries(st.edges)) {
      const c = scene.edgeCenter.get(eid)
      if (!c || !n) continue
      const ratio = v / 13.89
      ctx.strokeStyle = ratio > 0.6 ? 'rgba(34,197,94,0.55)' : ratio > 0.3 ? 'rgba(245,158,11,0.65)' : 'rgba(239,68,68,0.7)'
      ctx.lineWidth = Math.min(c.w * 0.6, Math.max(1.2, px * 5))
      ctx.beginPath()
      ctx.moveTo(c.pts[0][0], c.pts[0][1])
      for (let i = 1; i < c.pts.length; i++) ctx.lineTo(c.pts[i][0], c.pts[i][1])
      ctx.stroke()
    }
    ctx.lineCap = 'butt'
  }

  // ---- incidents ------------------------------------------------------
  drawOverlayZones(ctx, st.overlays, st.t, px, st.now)
  for (const inc of st.incidents) if (!inc.cleared) drawIncident(ctx, scene, inc, s, st.now)

  // ---- selected route -------------------------------------------------
  if (st.route && st.route.length) {
    ctx.strokeStyle = 'rgba(37,99,235,0.7)'
    ctx.lineWidth = Math.max(0.35, px * 2.5)
    ctx.lineCap = 'round'
    ctx.setLineDash([Math.max(1.5, px * 6), Math.max(1.5, px * 6)])
    for (const eid of st.route) {
      const c = scene.edgeCenter.get(eid)
      if (!c) continue
      ctx.beginPath()
      ctx.moveTo(c.pts[0][0], c.pts[0][1])
      for (let i = 1; i < c.pts.length; i++) ctx.lineTo(c.pts[i][0], c.pts[i][1])
      ctx.stroke()
    }
    ctx.setLineDash([])
    ctx.lineCap = 'butt'
  }

  // picked / hovered street (location picking)
  for (const [eid, col] of [
    [st.hoverEdge, 'rgba(37,99,235,0.35)'],
    [st.pickedEdge, 'rgba(37,99,235,0.6)'],
  ] as const) {
    if (!eid) continue
    const c = scene.edgeCenter.get(eid)
    if (!c) continue
    ctx.fillStyle = col
    ctx.beginPath()
    const poly = strip(c.pts, c.w)
    ctx.moveTo(poly[0][0], poly[0][1])
    for (const p of poly) ctx.lineTo(p[0], p[1])
    ctx.closePath()
    ctx.fill()
  }

  // ---- pedestrians ----------------------------------------------------
  if (st.layers.pedestrians && s > 1.6) drawPedestrians(ctx, st.persons, cam, dpr)

  // ---- vehicles -------------------------------------------------------
  const visible = drawVehicles(ctx, cam, st, dpr)

  // ---- static canopy: trees, signal masts, stop signs, labels (cached) ----
  drawLayer(ctx, canopyCache, scene, cam, st, dpr, drawCanopy)

  // ---- live signal heads (overhead, above traffic) ----------------------
  world()
  if (st.layers.signals && s > 1.4) drawSignalHeads(ctx, scene, st.tls, cam, px)

  // junction highlight
  if (st.selectedJunction) {
    const j = scene.junctions.find((q) => q.id === st.selectedJunction)
    if (j) {
      ctx.strokeStyle = 'rgba(37,99,235,0.9)'
      ctx.lineWidth = Math.max(0.3, px * 2)
      ctx.setLineDash([px * 6, px * 4])
      ctx.beginPath()
      ctx.arc(j.x, j.y, 26, 0, Math.PI * 2)
      ctx.stroke()
      ctx.setLineDash([])
    }
  }

  // ---- screen-space overlays -------------------------------------------
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.drawImage(vignette(W, H), 0, 0, W, H)
  drawTrackedTags(ctx, cam, st)
  overlayHits = drawOverlayPins(ctx, cam, st.overlays, st.t, st.now, st.safe)
  return visible
}

/** Static ground: land use, streets, markings, roofs. World geometry from SUMO lanes, junctions and crossings. */
function drawGround(ctx: CanvasRenderingContext2D, scene: Scene, cam: Camera, layers: Layers, dpr: number) {
  const s = cam.scale
  const W = cam.width
  const H = cam.height
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = C.land
  ctx.fillRect(0, 0, W, H)
  // world transform: x right, y up
  const k = s * dpr
  ctx.setTransform(k, 0, 0, -k, (W / 2 - cam.x * s) * dpr, (H / 2 + cam.y * s) * dpr)
  const px = 1 / s // one CSS pixel in metres

  // ---- ground --------------------------------------------------------
  for (const [use, path] of scene.blocks) {
    ctx.fillStyle = (C as Record<string, string>)[use] ?? C.residential
    ctx.fill(path)
  }
  ctx.fillStyle = '#D6D9DD'
  ctx.fill(scene.parking)
  if (s > 2.5) {
    ctx.strokeStyle = 'rgba(255,255,255,0.75)'
    ctx.lineWidth = 0.12
    ctx.stroke(scene.parkingLines)
  }
  ctx.strokeStyle = '#E9E3D3'
  ctx.lineWidth = 2.2
  ctx.lineCap = 'round'
  ctx.stroke(scene.parkPaths)
  ctx.lineCap = 'butt'
  ctx.fillStyle = C.water
  ctx.fill(scene.water)
  ctx.fillStyle = '#CF967A'
  ctx.fill(scene.track)
  ctx.fillStyle = '#9CC38A'
  ctx.fill(scene.fields)
  ctx.fillStyle = '#DCDDDF'
  ctx.fill(scene.playground)
  ctx.fillStyle = '#D9DCE0'
  ctx.fill(scene.helipad)
  // stadium bowl
  ctx.fillStyle = '#E3E6EA'
  ctx.fill(scene.stadiumRing)
  ctx.strokeStyle = '#C5CAD1'
  ctx.lineWidth = 0.6
  ctx.stroke(scene.stadiumRing)
  ctx.fillStyle = '#86B874'
  ctx.fill(scene.stadiumField)
  ctx.strokeStyle = 'rgba(255,255,255,0.8)'
  ctx.lineWidth = 0.25
  ctx.stroke(scene.pitchLines)

  if (layers.buildings) {
    ctx.fillStyle = C.shadow
    ctx.fill(scene.buildingShadows)
  }

  // ---- streets -------------------------------------------------------
  ctx.lineJoin = 'round'
  ctx.strokeStyle = C.curb
  ctx.lineWidth = Math.max(0.3, px * 1.2)
  ctx.stroke(scene.sidewalks)
  ctx.fillStyle = C.sidewalk
  ctx.fill(scene.sidewalks)
  ctx.fillStyle = C.asphalt
  ctx.fill(scene.asphalt)
  ctx.fillStyle = C.asphaltJ
  ctx.fill(scene.junctionAsphalt)
  if (s > 2.5) {
    const g = asphaltGrain(ctx)
    if (g) {
      ctx.fillStyle = g
      ctx.fill(scene.asphalt)
      ctx.fill(scene.junctionAsphalt)
    }
  }
  ctx.strokeStyle = C.curb
  ctx.stroke(scene.walkAreas)
  ctx.fillStyle = C.sidewalk
  ctx.fill(scene.walkAreas)
  if (s > 1.4) {
    ctx.fillStyle = 'rgba(38,42,48,0.2)'
    ctx.fill(scene.gutters)
    ctx.strokeStyle = 'rgba(28,31,36,0.09)'
    ctx.lineWidth = 0.6
    ctx.lineCap = 'round'
    ctx.stroke(scene.wear)
    ctx.lineCap = 'butt'
  }
  if (s > 4.5) {
    ctx.strokeStyle = 'rgba(150,156,165,0.35)'
    ctx.lineWidth = Math.max(0.04, px * 0.6)
    ctx.stroke(scene.pavingJoints)
    ctx.fillStyle = '#3F444B'
    ctx.fill(scene.drains)
  }
  ctx.strokeStyle = '#C3C8CF'
  ctx.lineWidth = Math.max(0.24, px * 1.2)
  ctx.stroke(scene.curbs)
  ctx.fillStyle = C.bike
  ctx.fill(scene.bikePaint)
  ctx.fillStyle = 'rgba(97,102,110,1)'
  ctx.fill(scene.bikeBuffer)
  ctx.fillStyle = '#D3D6DA'
  ctx.fill(scene.medianCurb)
  ctx.fillStyle = '#B9D1A8'
  ctx.fill(scene.medianGrass)

  if (s > 0.9) {
    const mw = Math.max(0.13, px * 0.8)
    ctx.strokeStyle = C.yellow
    ctx.lineWidth = mw
    ctx.stroke(scene.yellow)
    ctx.lineWidth = Math.max(0.1, px * 0.6)
    ctx.stroke(scene.medianHatch)
    ctx.strokeStyle = C.white
    ctx.lineWidth = mw
    ctx.stroke(scene.whiteSolid)
    ctx.setLineDash([3, 9])
    ctx.stroke(scene.whiteDash)
    ctx.setLineDash([])
    ctx.fillStyle = C.white
    ctx.fill(scene.stopBars)
    ctx.fill(scene.zebra)
    if (s > 2.2) {
      ctx.lineWidth = 0.2
      ctx.stroke(scene.arrowStroke)
      ctx.fill(scene.arrowFill)
      ctx.strokeStyle = 'rgba(255,255,255,0.85)'
      ctx.setLineDash([1.2, 0.8])
      ctx.lineWidth = 0.12
      ctx.stroke(scene.busBox)
      ctx.setLineDash([])
    }
    if (s > 5) {
      ctx.strokeStyle = 'rgba(255,255,255,0.9)'
      ctx.lineWidth = 0.08
      ctx.stroke(scene.bikeSymbols)
      ctx.fillStyle = '#F8FAFC'
      for (const [x, y] of scene.bikePosts) {
        ctx.beginPath()
        ctx.arc(x, y, 0.09, 0, Math.PI * 2)
        ctx.fill()
      }
    }
  }

  // ---- roofs (traffic drives below the tree canopy, beside the buildings) --
  if (layers.buildings) {
  if (layers.buildings) {
    for (const r of scene.roofs) {
      ctx.fillStyle = r.fill
      ctx.fill(r.path)
      ctx.strokeStyle = C.roofLine
      ctx.lineWidth = Math.max(0.15, px * 0.8)
      ctx.stroke(r.path)
    }
    if (s > 1.5) {
      ctx.strokeStyle = 'rgba(160,168,178,0.55)'
      ctx.lineWidth = Math.max(0.1, px * 0.6)
      ctx.stroke(scene.roofLines)
      ctx.fillStyle = 'rgba(205,210,216,0.7)'
      ctx.fill(scene.roofUnits)
    }
    ctx.fillStyle = 'rgba(201,214,226,0.9)'
    ctx.fill(scene.shelters)
    ctx.fillStyle = '#6B7280'
    ctx.fill(scene.shelterRoof)
  }
  }
}

/** Static layer drawn above traffic: tree canopy, signal masts, stop signs, street labels. */
function drawCanopy(ctx: CanvasRenderingContext2D, scene: Scene, cam: Camera, layers: Layers, dpr: number) {
  const s = cam.scale
  const W = cam.width
  const H = cam.height
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, W, H)
  const k = s * dpr
  ctx.setTransform(k, 0, 0, -k, (W / 2 - cam.x * s) * dpr, (H / 2 + cam.y * s) * dpr)
  if (layers.trees) {
    if (s > 1.2) {
      ctx.fillStyle = C.treeShadow
      ctx.fill(scene.treeShadows)
    }
    ctx.globalAlpha = s > 1.2 ? 1 : 0.55
    ctx.fillStyle = C.tree
    ctx.fill(scene.treeCanopy)
    ctx.globalAlpha = 1
    if (s > 1.2) {
      ctx.fillStyle = C.treeLight
      ctx.fill(scene.treeLight)
    }
  }
  if (layers.signals && s > 1.4) {
    ctx.fillStyle = '#20242B'
    ctx.fill(scene.signalHousings)
    ctx.strokeStyle = '#4B525C'
    ctx.lineWidth = 0.2
    ctx.stroke(scene.mastArms)
    ctx.fillStyle = '#3F4650'
    ctx.fill(scene.poles)
    for (const sgn of scene.stopSigns) {
      octagon(ctx, sgn.x, sgn.y, 0.42)
      ctx.fillStyle = '#FFFFFF'
      ctx.fill()
      octagon(ctx, sgn.x, sgn.y, 0.36)
      ctx.fillStyle = '#C62828'
      ctx.fill()
    }
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  if (layers.labels) drawLabels(ctx, scene, cam)
}

// ---- cached static layers ---------------------------------------------------
// Static geometry (thousands of SUMO lane/junction/building polygons) costs far
// more to rasterise than the traffic on top of it, so it is drawn once into an
// off-screen bitmap with a margin around the viewport and re-used while the
// camera pans inside that margin. It is redrawn when the view settles after a
// zoom, when panning leaves the margin, or when layers/viewport change.

interface LayerCache {
  canvas: HTMLCanvasElement | null
  view: Camera // the view the bitmap was rendered for
  key: string
  builds: number
}

type Painter = (ctx: CanvasRenderingContext2D, scene: Scene, cam: Camera, layers: Layers, dpr: number) => void

const MARGIN = 0.2 // extra fraction of the viewport rendered on each side
export const groundCache: LayerCache = { canvas: null, view: new Camera(), key: '', builds: 0 }
export const canopyCache: LayerCache = { canvas: null, view: new Camera(), key: '', builds: 0 }

// Whole-city bitmaps at a moderate resolution, built once in idle time. While a
// zoom or glide leaves the detailed bitmap, frames blit these instead of
// re-rasterising the city; the detailed bitmap is rebuilt when the view settles.
const OVERVIEW_PX = 2048
export const groundOverview: LayerCache = { canvas: null, view: new Camera(), key: '', builds: 0 }
export const canopyOverview: LayerCache = { canvas: null, view: new Camera(), key: '', builds: 0 }
let overviewTimer: ReturnType<typeof setTimeout> | null = null

function buildOverview(cache: LayerCache, key: string, scene: Scene, layers: Layers, dpr: number, paint: Painter) {
  const [x0, y0, x1, y1] = scene.bounds
  const v = cache.view
  const s = Math.min(OVERVIEW_PX / ((x1 - x0) * dpr), OVERVIEW_PX / ((y1 - y0) * dpr))
  v.setViewport((x1 - x0) * s, (y1 - y0) * s)
  v.x = (x0 + x1) / 2
  v.y = (y0 + y1) / 2
  v.scale = s
  const c = (cache.canvas ??= document.createElement('canvas'))
  c.width = Math.round(v.width * dpr)
  c.height = Math.round(v.height * dpr)
  // screen-space labels would be the wrong size once scaled; they return with the detailed bitmap
  paint(c.getContext('2d')!, scene, v, { ...layers, labels: false }, dpr)
  cache.key = key
  cache.builds++
}

function overviewKey(scene: Scene, st: RenderState, dpr: number) {
  return `${scene.id}|${dpr}|${+st.layers.buildings}${+st.layers.trees}${+st.layers.signals}`
}

function scheduleOverviews(scene: Scene, st: RenderState, dpr: number) {
  const key = overviewKey(scene, st, dpr)
  if ((groundOverview.key === key && canopyOverview.key === key) || overviewTimer) return
  const layers = { ...st.layers }
  // off the critical path: one city-sized raster shortly after the view first settles
  overviewTimer = setTimeout(() => {
    overviewTimer = null
    if (groundOverview.key !== key) buildOverview(groundOverview, key, scene, layers, dpr, drawGround)
    if (canopyOverview.key !== key) buildOverview(canopyOverview, key, scene, layers, dpr, drawCanopy)
  }, 600)
}

function blit(ctx: CanvasRenderingContext2D, cache: LayerCache, cam: Camera, dpr: number) {
  const v = cache.view
  const f = cam.scale / v.scale
  const sx = (v.x - v.width / 2 / v.scale - cam.x) * cam.scale + cam.width / 2
  const sy = (cam.y - (v.y + v.height / 2 / v.scale)) * cam.scale + cam.height / 2
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.drawImage(cache.canvas!, sx, sy, v.width * f, v.height * f)
}

function covers(v: Camera, cam: Camera): boolean {
  const [a0, b0, a1, b1] = v.viewRect()
  const [c0, d0, c1, d1] = cam.viewRect()
  const eps = 0.5 / cam.scale
  return c0 >= a0 - eps && d0 >= b0 - eps && c1 <= a1 + eps && d1 <= b1 + eps
}

function drawLayer(ctx: CanvasRenderingContext2D, cache: LayerCache, scene: Scene, cam: Camera, st: RenderState, dpr: number, paint: Painter) {
  const key = `${scene.id}|${dpr}|${cam.width}x${cam.height}|${+st.layers.buildings}${+st.layers.trees}${+st.layers.labels}${+st.layers.signals}`
  const v = cache.view
  const valid = cache.canvas !== null && cache.key === key
  const ratio = cam.scale / v.scale
  const crisp = valid && Math.abs(ratio - 1) < 0.004 && covers(v, cam)
  if (!crisp) {
    const stretchable = valid && ratio > 0.6 && ratio < 2.5 && covers(v, cam)
    if (cam.zooming) {
      // mid-zoom: stretch the detailed bitmap while it covers the view, else the city overview
      if (!stretchable) {
        const ov = paint === drawGround ? groundOverview : canopyOverview
        if (ov.canvas && ov.key === overviewKey(scene, st, dpr)) {
          if (paint === drawGround) {
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
            ctx.fillStyle = C.land
            ctx.fillRect(0, 0, cam.width, cam.height)
          }
          blit(ctx, ov, cam, dpr)
        } else paint(ctx, scene, cam, st.layers, dpr)
        return
      }
    } else {
      v.setViewport(cam.width * (1 + 2 * MARGIN), cam.height * (1 + 2 * MARGIN))
      v.x = cam.x
      v.y = cam.y
      v.scale = cam.scale
      const c = (cache.canvas ??= document.createElement('canvas'))
      const w = Math.round(v.width * dpr)
      const h = Math.round(v.height * dpr)
      if (c.width !== w || c.height !== h) {
        c.width = w
        c.height = h
      }
      paint(c.getContext('2d')!, scene, v, st.layers, dpr)
      cache.key = key
      cache.builds++
      scheduleOverviews(scene, st, dpr)
    }
  }
  blit(ctx, cache, cam, dpr)
}

/** Drop cached bitmaps (e.g. when the network changes). */
export function invalidateLayers() {
  groundCache.key = canopyCache.key = ''
}

let vignetteCanvas: HTMLCanvasElement | null = null
function vignette(W: number, H: number): HTMLCanvasElement {
  if (vignetteCanvas && vignetteCanvas.width === Math.round(W / 2) && vignetteCanvas.height === Math.round(H / 2)) return vignetteCanvas
  // half resolution is plenty for a soft gradient
  const c = (vignetteCanvas ??= document.createElement('canvas'))
  c.width = Math.round(W / 2)
  c.height = Math.round(H / 2)
  const g = c.getContext('2d')!
  const vg = g.createRadialGradient(c.width / 2, c.height / 2, Math.min(c.width, c.height) * 0.42, c.width / 2, c.height / 2, Math.hypot(c.width, c.height) * 0.62)
  vg.addColorStop(0, 'rgba(244,245,247,0)')
  vg.addColorStop(1, 'rgba(244,245,247,0.38)')
  g.fillStyle = vg
  g.fillRect(0, 0, c.width, c.height)
  return c
}

// ---- batched dynamic drawing ---------------------------------------------
const SHIRTS = ['#334155', '#1E40AF', '#9F1239', '#047857', '#B45309', '#6B21A8', '#0F172A', '#64748B']
const HAIR = ['#3B2A20', '#1F1A17', '#6B4F3A', '#C9A27A', '#2D2D2D']
const personHash = new Map<string, number>()

/** Pedestrians as SUMO reports them: one atlas sprite each, rotated to the walking direction. */
function drawPedestrians(ctx: CanvasRenderingContext2D, persons: PersonView[], cam: Camera, dpr: number) {
  if (personHash.size > 6000) personHash.clear()
  const s = cam.scale
  const R = Math.max(0.42, 3.2 / s) * s // halo radius in CSS px
  for (const p of persons) {
    const [sx, sy] = cam.toScreen(p.x, p.y)
    if (sx < -R || sy < -R || sx > cam.width + R || sy > cam.height + R) continue
    let h = personHash.get(p.id)
    if (h === undefined) personHash.set(p.id, (h = hash(p.id)))
    const sp = personSprite(SHIRTS[h % 8], HAIR[h % 5])
    // SUMO angle: 0 = north, clockwise; sprite heading is +x, screen y points down
    const th = ((p.angle - 90) * Math.PI) / 180
    const c = Math.cos(th) * dpr
    const n = Math.sin(th) * dpr
    ctx.setTransform(c, n, -n, c, sx * dpr, sy * dpr)
    ctx.drawImage(sp.canvas, sp.sx, sp.sy, sp.sw, sp.sh, -R, -R, 2 * R, 2 * R)
  }
}

/** Live signal lamps (the housings are static and live in the canopy layer). */
function drawSignalHeads(ctx: CanvasRenderingContext2D, scene: Scene, tls: Record<string, string>, cam: Camera, px: number) {
  const s = cam.scale
  const lr = Math.max(0.3, px * 2.2)
  const [x0, y0, x1, y1] = cam.viewRect()
  for (const h of scene.signals) {
    if (h.x < x0 - 1 || h.x > x1 + 1 || h.y < y0 - 1 || h.y > y1 + 1) continue
    const state = tls[h.tls]
    let best = 'o'
    if (state) for (const li of h.links) if ((RANK[state[li]] ?? 0) > (RANK[best] ?? 0)) best = state[li]
    const col = SIG[best] ?? '#9CA3AF'
    if (s > 4) {
      const g = dotSprite(col, 0.35)
      ctx.drawImage(g.canvas, g.sx, g.sy, g.sw, g.sh, h.x - 0.75, h.y - 0.75, 1.5, 1.5)
    }
    const d = dotSprite(col)
    ctx.drawImage(d.canvas, d.sx, d.sy, d.sw, d.sh, h.x - lr, h.y - lr, 2 * lr, 2 * lr)
  }
}

function octagon(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath()
  for (let i = 0; i < 8; i++) {
    const a = Math.PI / 8 + (i * Math.PI) / 4
    const px = x + Math.cos(a) * r
    const py = y + Math.sin(a) * r
    if (i === 0) ctx.moveTo(px, py)
    else ctx.lineTo(px, py)
  }
  ctx.closePath()
}

// Per-vehicle look (paint, livery) is a pure function of the SUMO id; memoise it.
const looks = new Map<string, { color: string; extra: string; flat: string }>()
function lookFor(id: string, type: VType, lines: { id: string; color: string }[]) {
  let l = looks.get(id)
  if (!l) {
    if (looks.size > 8000) looks.clear()
    let color = paintFor(id, type)
    let extra = type === 'truck' ? cargoFor(id) : ''
    let flat = color
    if (type === 'bus') {
      const line = busLineFor(id, lines)
      color = '#F5F6F7'
      extra = `${line.color}|${line.id}`
      flat = line.color
    }
    if (type === 'ambulance') flat = '#F8FAFC'
    else if (type === 'police') flat = '#1E3A8A'
    else if (type === 'bike') flat = '#0F766E'
    looks.set(id, (l = { color, extra, flat }))
  }
  return l
}

// Transform of a vehicle in metre units: front bumper at x=0, left side at -y.
interface Placed {
  v: VehicleView
  type: VType
  L: number
  Wd: number
  a: number // matrix a/b for the metre frame (device px per metre × cos/sin)
  b: number
  x: number // device px
  y: number
}

/**
 * Vehicles exactly as SUMO reports them (interpolated position and heading).
 * Pass 1 draws every body from the shared sprite atlas (brake lamps baked in),
 * so the GPU can batch them; pass 2 adds the few live effects on top
 * (indicators, emergency beacons, hazard flashers, selection outline).
 */
function drawVehicles(ctx: CanvasRenderingContext2D, cam: Camera, st: RenderState, dpr: number): number {
  const s = cam.scale
  const ppm = spriteBucket(s * dpr)
  const blink = Math.floor(st.now / 380) % 2 === 0
  const flash = Math.floor(st.now / 160) % 2 === 0
  const margin = 20
  const overview = s < 1.6
  const extras: Placed[] = []
  let drawn = 0
  for (const v of st.vehicles) {
    const type: VType = CODE_TO_TYPE[v.code] ?? 'sedan'
    const [L, Wd] = DIMS[type]
    const [sx, sy] = cam.toScreen(v.x, v.y)
    if (sx < -margin * s || sy < -margin * s || sx > cam.width + margin * s || sy > cam.height + margin * s) continue
    drawn++
    const th = ((v.angle - 90) * Math.PI) / 180 + (type === 'wreck' ? wreckSpin(v.id) : 0)
    const cos = Math.cos(th)
    const sin = Math.sin(th)
    ctx.globalAlpha = v.alpha
    if (overview) {
      // oriented marker in body colour, exaggerated slightly so traffic stays legible
      const lw = Math.max(type === 'bike' ? 3 : 5, L * s * 1.15)
      const ww = Math.max(type === 'bike' ? 1.8 : 2.8, Wd * s * 1.15)
      const mk = markerSprite(lookFor(v.id, type, st.busLines).flat, type === 'ambulance' ? '#DC2626' : 'rgba(15,23,42,0.55)')
      ctx.setTransform(cos * dpr, sin * dpr, -sin * dpr, cos * dpr, sx * dpr, sy * dpr)
      ctx.drawImage(mk.canvas, mk.sx, mk.sy, mk.sw, mk.sh, -lw, -ww / 2, lw, ww)
      if (st.selected === v.id) extras.push({ v, type, L, Wd, a: cos * dpr * s, b: sin * dpr * s, x: sx * dpr, y: sy * dpr })
      continue
    }
    const look = lookFor(v.id, type, st.busLines)
    const sig = v.signals
    const sp = spriteFor(type, look.color, ppm, look.extra, (sig & 8) !== 0 && type !== 'bike' && type !== 'wreck')
    const k = (s * dpr) / sp.ppm
    ctx.setTransform(cos * k, sin * k, -sin * k, cos * k, sx * dpr, sy * dpr)
    ctx.drawImage(sp.canvas, sp.sx, sp.sy, sp.sw, sp.sh, -sp.ox, -sp.oy, sp.sw, sp.sh)
    const needs = (blink && sig & 3 && type !== 'bike' && type !== 'wreck') || type === 'ambulance' || type === 'police' || type === 'wreck' || st.selected === v.id
    if (needs) extras.push({ v, type, L, Wd, a: cos * k * sp.ppm, b: sin * k * sp.ppm, x: sx * dpr, y: sy * dpr })
  }
  ctx.globalAlpha = 1

  for (const { v, type, L, Wd, a, b, x, y } of extras) {
    ctx.setTransform(a, b, -b, a, x, y)
    const sig = v.signals
    if (!overview && blink && sig & 3 && type !== 'bike' && type !== 'wreck') {
      ctx.fillStyle = '#FFB020'
      for (const [bit, side] of [
        [1, 1],
        [2, -1],
      ] as const) {
        if (!(sig & bit)) continue
        ctx.fillRect(-0.16, side * (Wd / 2) - 0.14, 0.14, 0.28)
        ctx.fillRect(-L + 0.02, side * (Wd / 2) - 0.14, 0.14, 0.28)
      }
    }
    const tr = st.tracked.get(v.id)
    const beacons = !overview && (type === 'ambulance' || type === 'police') && (tr ? tr.priority !== false && tr.status !== 'returning' && tr.status !== 'completed' : (sig & 2048) !== 0)
    const lb = LIGHTBAR[type]
    if (beacons && lb) {
      // alternating halves of the roof light bar, plus a soft wash on the road
      const cx = lb.x - L
      const lit = flash ? 0 : 1
      const col = lb.colors[lit]
      const glow = ctx.createRadialGradient(cx, 0, 0, cx, 0, 3.2)
      glow.addColorStop(0, col === '#F8FAFC' ? 'rgba(255,255,255,0.32)' : `${col}55`)
      glow.addColorStop(1, `${col === '#F8FAFC' ? '#FFFFFF' : col}00`)
      ctx.fillStyle = glow
      ctx.fillRect(cx - 3.2, -3.2, 6.4, 6.4)
      ctx.shadowColor = col
      ctx.shadowBlur = 8 * dpr
      ctx.fillStyle = col
      ctx.beginPath()
      ctx.roundRect(cx - lb.len / 2, lit ? 0.04 : -lb.span / 2, lb.len, lb.span / 2 - 0.04, 0.07)
      ctx.fill()
      if (type === 'ambulance') {
        // module corner beacons flash with the bar
        ctx.fillStyle = flash ? '#EF4444' : '#FCA5A5'
        for (const side of [-1, 1]) ctx.fillRect(-L + 0.12, side > 0 ? Wd / 2 - 0.36 : -Wd / 2 + 0.12, 0.24, 0.24)
      }
      ctx.shadowBlur = 0
    } else if (!overview && type === 'wreck' && blink) {
      // hazard flashers
      ctx.fillStyle = '#F59E0B'
      ctx.shadowColor = '#F59E0B'
      ctx.shadowBlur = 8 * dpr
      for (const side of [-1, 1]) {
        ctx.fillRect(-0.3, side * (Wd / 2) - 0.12, 0.2, 0.24)
        ctx.fillRect(-L + 0.05, side * (Wd / 2) - 0.12, 0.2, 0.24)
      }
      ctx.shadowBlur = 0
    }
    if (st.selected === v.id) {
      ctx.strokeStyle = '#2563EB'
      ctx.lineWidth = (2.2 * dpr) / Math.hypot(a, b)
      ctx.beginPath()
      ctx.roundRect(-L - 0.5, -Wd / 2 - 0.5, L + 1, Wd + 1, 0.6)
      ctx.stroke()
    }
  }
  ctx.shadowBlur = 0
  return drawn
}

/** Wrecks stand lane-aligned in SUMO; a stable per-id yaw (±14–31°) makes them read as knocked askew. */
function wreckSpin(id: string): number {
  const h = hash(id)
  return (h & 1 ? 1 : -1) * (0.25 + ((h >>> 1) % 1000) / 3300)
}

function rng(seed: number) {
  let a = seed || 1
  return () => {
    a = (Math.imul(a ^ (a >>> 15), 1 | a) + 0x6d2b79f5) | 0
    return ((a ^ (a >>> 14)) >>> 0) / 4294967296
  }
}

const DEBRIS = ['#9CA3AF', '#6B7280', '#1F2937', '#D1D5DB', '#BFDBFE', '#E5E7EB']

/** Skid marks, a fluid stain and scattered debris under the wrecks (drawn below the vehicles). */
function drawCrashScene(ctx: CanvasRenderingContext2D, lanes: { pts: Pt[]; w: number }[], inc: Incident, L: number) {
  const r = rng(hash(inc.id))
  const at = (ln: { pts: Pt[] }, u: number, lat: number) => {
    const a = along(ln.pts, Math.min(L, Math.max(0, u)))
    return [a.x - a.dy * lat, a.y + a.dx * lat] as const
  }
  const impact = inc.pos + inc.length * 0.5
  ctx.lineCap = 'round'
  for (const ln of lanes) {
    const drift = (r() - 0.5) * 2.4
    ctx.strokeStyle = 'rgba(17,24,39,0.32)'
    ctx.lineWidth = 0.22
    for (const side of [-0.75, 0.75]) {
      ctx.beginPath()
      for (let d = 0; d <= 16; d += 2) {
        const f = d / 16
        const [x, y] = at(ln, inc.pos - 16 + d, side + drift * f * f)
        if (d) ctx.lineTo(x, y)
        else ctx.moveTo(x, y)
      }
      ctx.stroke()
    }
  }
  const mid = lanes[Math.floor(lanes.length / 2)]
  const [fx, fy] = at(mid, impact, (r() - 0.5) * 1.5)
  ctx.fillStyle = 'rgba(30,27,24,0.22)'
  ctx.beginPath()
  ctx.ellipse(fx, fy, 1.9 + r(), 1.1 + r() * 0.6, r() * Math.PI, 0, Math.PI * 2)
  ctx.fill()
  const world = ctx.getTransform()
  for (let i = 0; i < 34; i++) {
    const ln = lanes[Math.floor(r() * lanes.length)]
    const [x, y] = at(ln, inc.pos - 2 + r() * (inc.length + 9), (r() - 0.5) * (ln.w + 0.8))
    const w = 0.12 + r() * 0.34
    const h = 0.08 + r() * 0.2
    ctx.setTransform(world)
    ctx.translate(x, y)
    ctx.rotate(r() * Math.PI)
    ctx.fillStyle = DEBRIS[Math.floor(r() * DEBRIS.length)]
    ctx.fillRect(-w / 2, -h / 2, w, h)
  }
  ctx.setTransform(world)
  ctx.lineCap = 'butt'
}

function drawIncident(ctx: CanvasRenderingContext2D, scene: Scene, inc: Incident, s: number, now: number) {
  const lanes = inc.lanes.map((i) => scene.lanes.get(`${inc.edge}_${i}`)).filter(Boolean) as { pts: Pt[]; w: number }[]
  if (!lanes.length) return
  const ref = lanes[0].pts
  const L = polyLength(ref)
  const cone = (x: number, y: number, r = 0.32) => {
    ctx.fillStyle = '#F97316'
    ctx.beginPath()
    ctx.arc(x, y, r, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#FFFFFF'
    ctx.beginPath()
    ctx.arc(x, y, r * 0.45, 0, Math.PI * 2)
    ctx.fill()
  }
  if (inc.kind === 'construction') {
    const u0 = Math.max(0, inc.pos)
    const u1 = Math.min(L, inc.pos + inc.length)
    for (const ln of lanes) {
      const seg = slice(ln.pts, u0, u1)
      const poly = strip(seg, ln.w - 0.2)
      ctx.fillStyle = 'rgba(249,115,22,0.18)'
      ctx.beginPath()
      poly.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
      ctx.closePath()
      ctx.fill()
      // hatch
      ctx.save()
      ctx.clip()
      ctx.strokeStyle = 'rgba(249,115,22,0.55)'
      ctx.lineWidth = 0.35
      for (let u = u0; u < u1; u += 2.5) {
        const a = along(ln.pts, u)
        ctx.beginPath()
        ctx.moveTo(a.x - a.dy * 2 - a.dx * 2, a.y + a.dx * 2 - a.dy * 2)
        ctx.lineTo(a.x + a.dy * 2 + a.dx * 2, a.y - a.dx * 2 + a.dy * 2)
        ctx.stroke()
      }
      ctx.restore()
      // barrels along both edges + taper at the start
      if (s > 2) {
        for (const side of [1, -1]) {
          const edge = offset(ln.pts, (side * (ln.w - 0.5)) / 2)
          for (let u = u0 + 4; u < u1; u += 7) {
            const a = along(edge, u)
            cone(a.x, a.y, 0.36)
          }
        }
      }
    }
    if (inc.closed) {
      const a = along(ref, u0 + 1)
      ctx.strokeStyle = '#F97316'
      ctx.lineWidth = 0.45
      const span = lanes.reduce((acc, l) => acc + l.w, 0)
      ctx.beginPath()
      ctx.moveTo(a.x - a.dy * -0.8, a.y + a.dx * -0.8)
      ctx.lineTo(a.x - a.dy * (span - 0.8), a.y + a.dx * (span - 0.8))
      ctx.setLineDash([0.9, 0.9])
      ctx.stroke()
      ctx.setLineDash([])
    }
    return
  }
  // accident: red zone around the wrecks, cone taper upstream
  const u = inc.pos
  for (const ln of lanes) {
    const seg = slice(ln.pts, Math.max(0, u - 3), Math.min(L, u + inc.length + 4))
    // never thinner than a few pixels, so the blockage reads at overview zoom
    const poly = strip(seg, Math.max(ln.w, 6 / s))
    ctx.fillStyle = `rgba(239,68,68,${0.12 + 0.06 * Math.sin(now / 300)})`
    ctx.beginPath()
    poly.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
    ctx.closePath()
    ctx.fill()
  }
  if (s > 1.8) {
    drawCrashScene(ctx, lanes, inc, L)
    const outer = lanes[0]
    const taperLen = 22
    for (let d = 0; d <= taperLen; d += 2.75) {
      const t = d / taperLen
      const a = along(outer.pts, Math.max(0, u - taperLen + d - 2))
      const w = lanes.reduce((acc, l) => acc + l.w, 0)
      const v = -outer.w / 2 + 0.3 + (w - 0.6) * t
      cone(a.x - a.dy * v, a.y + a.dx * v)
    }
  }
}

function drawLabels(ctx: CanvasRenderingContext2D, scene: Scene, cam: Camera) {
  const s = cam.scale
  if (s < 0.6) return
  const close = s > 3.2
  ctx.font = `500 ${close ? 12 : 11}px Inter, system-ui, sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  const drawn: [number, number][] = []
  const byText = new Map<string, [number, number][]>()
  for (const lb of scene.labels) {
    const off = close ? lb.half + 2.2 : 0
    const wx = lb.angle === 90 ? lb.x + off : lb.x
    const wy = lb.angle === 90 ? lb.y : lb.y + off
    const [sx, sy] = cam.toScreen(wx, wy)
    if (sx < -100 || sy < -100 || sx > cam.width + 100 || sy > cam.height + 100) continue
    if (drawn.some(([x, y]) => Math.abs(x - sx) < 160 && Math.abs(y - sy) < 24)) continue
    const same = byText.get(lb.text) ?? []
    if (same.some(([x, y]) => Math.hypot(x - sx, y - sy) < (close ? 380 : 520))) continue
    same.push([sx, sy])
    byText.set(lb.text, same)
    drawn.push([sx, sy])
    ctx.save()
    ctx.translate(sx, sy)
    if (lb.angle === 90) ctx.rotate(-Math.PI / 2)
    ctx.lineWidth = 3.5
    ctx.strokeStyle = close ? 'rgba(236,237,232,0.95)' : 'rgba(97,102,110,0.95)'
    ctx.fillStyle = close ? '#4B5563' : '#F3F4F6'
    ctx.lineJoin = 'round'
    ctx.strokeText(lb.text, 0, 0)
    ctx.fillText(lb.text, 0, 0)
    ctx.restore()
  }
  // points of interest
  if (s > 0.9) {
    const icon: Record<string, [string, string]> = {
      hospital: ['H', '#DC2626'],
      police: ['P', '#1D4ED8'],
      school: ['S', '#B45309'],
      stadium: ['★', '#0F766E'],
      park: ['♣', '#15803D'],
    }
    for (const p of scene.pois) {
      const [sx, sy] = cam.toScreen(p.x, p.y)
      const [g, col] = icon[p.kind] ?? ['•', '#475569']
      ctx.fillStyle = '#FFFFFF'
      ctx.strokeStyle = 'rgba(15,23,42,0.12)'
      ctx.lineWidth = 1
      ctx.font = '600 12px Inter, system-ui, sans-serif'
      const tw = ctx.measureText(p.name).width
      ctx.beginPath()
      ctx.roundRect(sx - 11, sy - 11, tw + 34, 22, 11)
      ctx.fill()
      ctx.stroke()
      ctx.fillStyle = col
      ctx.beginPath()
      ctx.arc(sx, sy, 8, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = '#FFFFFF'
      ctx.font = '700 10px Inter, system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(g, sx, sy + 0.5)
      ctx.fillStyle = '#1F2937'
      ctx.font = '600 12px Inter, system-ui, sans-serif'
      ctx.textAlign = 'left'
      ctx.fillText(p.name, sx + 14, sy + 0.5)
      ctx.textAlign = 'center'
    }
  }
}

function drawTrackedTags(ctx: CanvasRenderingContext2D, cam: Camera, st: RenderState) {
  if (!st.tracked.size) return
  ctx.font = '600 11px Inter, system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (const v of st.vehicles) {
    const tr = st.tracked.get(v.id)
    if (!tr || tr.kind === 'bus') continue
    const [sx, sy] = cam.toScreen(v.x, v.y)
    const text = tr.label
    const w = ctx.measureText(text).width + 14
    const y = sy - Math.max(16, cam.scale * 3.5)
    const col = tr.kind === 'suspect' ? '#B91C1C' : tr.kind === 'police' ? '#1D4ED8' : '#B91C1C'
    ctx.fillStyle = '#FFFFFF'
    ctx.strokeStyle = 'rgba(15,23,42,0.15)'
    ctx.beginPath()
    ctx.roundRect(sx - w / 2, y - 10, w, 20, 6)
    ctx.fill()
    ctx.stroke()
    ctx.fillStyle = col
    ctx.beginPath()
    ctx.arc(sx - w / 2 + 8, y, 3, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#111827'
    ctx.fillText(text, sx + 3, y + 0.5)
  }
}
