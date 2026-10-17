// Scenario footprints on the map: where an incident, call, surge or line acts.
// Zones and routes are drawn in world space under traffic; pins and the
// off-screen indicators that lead to them are drawn in screen space on top.

import type { Overlay } from '../lib/types'
import { clock } from '../lib/format'
import type { Camera } from './camera'
import { along, polyLength, strip, type Pt } from './geom'
import type { Scene } from './scene'

export const TONE: Record<string, string> = {
  incident: '#DC2626',
  emergency: '#2563EB',
  demand: '#D97706',
  transit: '#7C3AED',
  environment: '#0284C7',
}
const GLYPH: Record<string, string> = { incident: '!', emergency: '+', demand: '●', transit: '■', environment: '~' }

export const toneColor = (o: Overlay) => o.color ?? TONE[o.tone] ?? '#475467'

export interface PlacedOverlay {
  o: Overlay
  /** world anchor for the pin (points: the scene; zones: the block nearest the zone's middle) */
  x: number
  y: number
  segs: { pts: Pt[]; w: number }[]
}

export type Phase = 'planned' | 'active' | 'done'

export function phaseAt(o: Overlay, t: number): Phase {
  if (o.tStart != null && t < o.tStart) return 'planned'
  if (o.tEnd != null && t > o.tEnd) return 'done'
  return 'active'
}

/** Resolve overlay edges against the scene geometry (once per run). */
export function placeOverlays(scene: Scene, overlays: Overlay[] | undefined): PlacedOverlay[] {
  const out: PlacedOverlay[] = []
  for (const o of overlays ?? []) {
    if (o.kind === 'weather') continue
    const segs = o.edges.map((e) => scene.edgeCenter.get(e)).filter(Boolean) as { pts: Pt[]; w: number }[]
    if (!segs.length) continue
    let x: number
    let y: number
    if (o.kind === 'point') {
      const p = segs[0].pts
      const a = along(p, o.pos ?? polyLength(p) / 2)
      ;[x, y] = [a.x, a.y]
    } else {
      const mids = segs.map((s) => along(s.pts, polyLength(s.pts) / 2))
      const cx = mids.reduce((acc, m) => acc + m.x, 0) / mids.length
      const cy = mids.reduce((acc, m) => acc + m.y, 0) / mids.length
      const best = mids.reduce((b, m) => (Math.hypot(m.x - cx, m.y - cy) < Math.hypot(b.x - cx, b.y - cy) ? m : b))
      ;[x, y] = [best.x, best.y]
    }
    out.push({ o, x, y, segs })
  }
  return out
}

/** Does this overlay deserve a pin, and an arrow when it is off-screen? */
function pinned(p: PlacedOverlay, phase: Phase): { pin: boolean; arrow: boolean } {
  if (phase === 'done' || p.o.kind === 'route') return { pin: false, arrow: false }
  // time-bounded events and incidents lead the eye; standing zones (school zone) only get a pin
  const event = p.o.kind === 'point' || p.o.tone === 'incident' || p.o.tEnd != null
  return { pin: true, arrow: phase === 'active' && event }
}

function hex(col: string, a: number): string {
  const n = parseInt(col.slice(1), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}

/** World space, under traffic: tinted blocks, line routes and a ring around each scene. */
export function drawOverlayZones(ctx: CanvasRenderingContext2D, placed: PlacedOverlay[], t: number, px: number, now: number) {
  for (const p of placed) {
    const phase = phaseAt(p.o, t)
    if (phase === 'done') continue
    const col = toneColor(p.o)
    const planned = phase === 'planned'
    if (p.o.kind === 'route') {
      ctx.strokeStyle = hex(col, 0.55)
      ctx.lineWidth = Math.max(1, px * 4)
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      for (const s of p.segs) {
        ctx.beginPath()
        s.pts.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])))
        ctx.stroke()
      }
      ctx.lineCap = 'butt'
      continue
    }
    if (p.o.kind === 'zone') {
      ctx.fillStyle = hex(col, planned ? 0.08 : 0.2)
      ctx.strokeStyle = hex(col, planned ? 0.35 : 0.7)
      ctx.lineWidth = Math.max(0.3, px * 1.5)
      ctx.setLineDash(planned ? [px * 6, px * 4] : [])
      for (const s of p.segs) {
        const poly = strip(s.pts, Math.max(s.w + 2, px * 10))
        ctx.beginPath()
        poly.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])))
        ctx.closePath()
        ctx.fill()
        ctx.stroke()
      }
      ctx.setLineDash([])
      continue
    }
    // point: a ring around the scene, pulsing while active, dashed while planned
    const r = Math.max(10, px * 18)
    ctx.lineWidth = Math.max(0.4, px * 2)
    if (planned) {
      ctx.strokeStyle = hex(col, 0.6)
      ctx.setLineDash([px * 5, px * 4])
      ctx.beginPath()
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2)
      ctx.stroke()
      ctx.setLineDash([])
    } else {
      const f = (now % 1600) / 1600
      ctx.strokeStyle = hex(col, 0.75 * (1 - f))
      ctx.beginPath()
      ctx.arc(p.x, p.y, r * (0.6 + 0.9 * f), 0, Math.PI * 2)
      ctx.stroke()
      ctx.fillStyle = hex(col, 0.14)
      ctx.beginPath()
      ctx.arc(p.x, p.y, r * 0.6, 0, Math.PI * 2)
      ctx.fill()
    }
  }
}

export interface Rect {
  x0: number
  y0: number
  x1: number
  y1: number
}

/**
 * Where to show a point lying outside `r` (screen px): the point where the ray from
 * the centre of `r` towards it leaves `r`, and the ray's angle. Null when inside.
 */
export function edgeIndicator(sx: number, sy: number, r: Rect): { x: number; y: number; angle: number } | null {
  if (sx >= r.x0 && sx <= r.x1 && sy >= r.y0 && sy <= r.y1) return null
  const cx = (r.x0 + r.x1) / 2
  const cy = (r.y0 + r.y1) / 2
  const dx = sx - cx
  const dy = sy - cy
  const tx = dx > 0 ? (r.x1 - cx) / dx : dx < 0 ? (r.x0 - cx) / dx : Infinity
  const ty = dy > 0 ? (r.y1 - cy) / dy : dy < 0 ? (r.y0 - cy) / dy : Infinity
  const k = Math.min(tx, ty)
  return { x: cx + dx * k, y: cy + dy * k, angle: Math.atan2(dy, dx) }
}

export interface OverlayHit {
  id: string
  x: number
  y: number
  w: number
  h: number
  wx: number
  wy: number
}

function chip(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  ctx.fillStyle = '#FFFFFF'
  ctx.strokeStyle = 'rgba(15,23,42,0.14)'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.roundRect(x, y, w, h, h / 2)
  ctx.fill()
  ctx.stroke()
}

function dist(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m / 10) * 10} m`
}

/**
 * Screen space, on top: a labelled pin per scene, or an arrow chip at the edge of
 * `safe` pointing to it when it is out of view. Returns click targets.
 */
export function drawOverlayPins(ctx: CanvasRenderingContext2D, cam: Camera, placed: PlacedOverlay[], t: number, now: number, safe: Rect): OverlayHit[] {
  const hits: OverlayHit[] = []
  ctx.textBaseline = 'middle'
  const [vx, vy] = [cam.x, cam.y]
  for (const p of placed) {
    const phase = phaseAt(p.o, t)
    const { pin, arrow } = pinned(p, phase)
    if (!pin) continue
    const col = toneColor(p.o)
    const [sx, sy] = cam.toScreen(p.x, p.y)
    const label = phase === 'planned' && p.o.tStart != null ? `${p.o.label} · ${clock(p.o.tStart)}` : p.o.label
    const ind = edgeIndicator(sx, sy, safe)
    if (ind) {
      if (!arrow) continue
      ctx.font = '600 11.5px Inter, system-ui, sans-serif'
      const text = `${label} · ${dist(Math.hypot(p.x - vx, p.y - vy))}`
      const w = ctx.measureText(text).width + 36
      const h = 26
      let x = Math.min(Math.max(ind.x - w / 2, safe.x0), safe.x1 - w)
      let y = Math.min(Math.max(ind.y - h / 2, safe.y0), safe.y1 - h)
      // stack chips that would land on top of one another
      for (const q of hits) {
        if (x < q.x + q.w && x + w > q.x && y < q.y + q.h && y + h > q.y) y = y + h + 6 > safe.y1 - h ? q.y - h - 6 : q.y + h + 6
      }
      chip(ctx, x, y, w, h)
      // arrow head pointing at the scene
      ctx.save()
      ctx.translate(x + 14, y + h / 2)
      ctx.rotate(ind.angle)
      ctx.fillStyle = col
      ctx.beginPath()
      ctx.arc(0, 0, 9, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = '#FFFFFF'
      ctx.beginPath()
      ctx.moveTo(5, 0)
      ctx.lineTo(-3, -4)
      ctx.lineTo(-3, 4)
      ctx.closePath()
      ctx.fill()
      ctx.restore()
      ctx.fillStyle = '#111827'
      ctx.textAlign = 'left'
      ctx.fillText(text, x + 28, y + h / 2 + 0.5)
      hits.push({ id: p.o.id, x, y, w, h, wx: p.x, wy: p.y })
      continue
    }
    // pin above the scene
    const planned = phase === 'planned'
    ctx.globalAlpha = planned ? 0.75 : 1
    ctx.font = '600 11.5px Inter, system-ui, sans-serif'
    const w = ctx.measureText(label).width + 32
    const h = 24
    const x = sx - 12
    const y = sy - h - 14
    ctx.strokeStyle = col
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(sx, sy - 14)
    ctx.lineTo(sx, sy - 3)
    ctx.stroke()
    ctx.fillStyle = col
    ctx.beginPath()
    ctx.arc(sx, sy, 3, 0, Math.PI * 2)
    ctx.fill()
    chip(ctx, x, y, w, h)
    const pulse = planned ? 0 : 0.5 + 0.5 * Math.sin(now / 220)
    ctx.fillStyle = col
    ctx.beginPath()
    ctx.arc(x + 12, y + h / 2, 8 + pulse, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#FFFFFF'
    ctx.font = '700 10px Inter, system-ui, sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText(GLYPH[p.o.tone] ?? '•', x + 12, y + h / 2 + 0.5)
    ctx.fillStyle = '#111827'
    ctx.font = '600 11.5px Inter, system-ui, sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText(label, x + 25, y + h / 2 + 0.5)
    ctx.globalAlpha = 1
    hits.push({ id: p.o.id, x, y, w, h, wx: p.x, wy: p.y })
  }
  ctx.textAlign = 'center'
  return hits
}

/**
 * The scene worth jumping to now: the latest live point (crash, call), else a live
 * time-bounded zone (surge, spike), else the next one that is planned.
 */
export function focusTarget(placed: PlacedOverlay[], t: number): PlacedOverlay | null {
  const events = placed.filter((p) => p.o.kind === 'point' || (p.o.kind === 'zone' && (p.o.tEnd != null || p.o.tone === 'incident')))
  const by = (a: PlacedOverlay, b: PlacedOverlay) => (a.o.tStart ?? 0) - (b.o.tStart ?? 0)
  const live = events.filter((p) => phaseAt(p.o, t) === 'active')
  const points = live.filter((p) => p.o.kind === 'point').sort(by)
  if (points.length) return points[points.length - 1]
  if (live.length) return live.sort(by)[live.length - 1]
  return events.filter((p) => phaseAt(p.o, t) === 'planned').sort(by)[0] ?? null
}

/** Topmost overlay target under a screen point. */
export function hitOverlay(hits: OverlayHit[], sx: number, sy: number): OverlayHit | null {
  for (let i = hits.length - 1; i >= 0; i--) {
    const h = hits[i]
    if (sx >= h.x && sx <= h.x + h.w && sy >= h.y && sy <= h.y + h.h) return h
  }
  return null
}
