import { useEffect, useRef } from 'react'
import type { Geometry, Pt, Veh } from '../lib/types'
import { STATUS_COLOR, STATUS_TEXT } from '../lib/types'

export interface AmbulanceView { id: number; status: number; seconds?: number; trail?: Pt[] }
export interface Scene {
  vehicles: Veh[]
  signals: string[] // one SUMO signal state string per controlled junction
  queue?: number[] // measured halted vehicles per approach
  forecast?: number[] // forecast halted vehicles per approach (+30 s): a model output, drawn dashed
  ambulances?: AmbulanceView[]
  mlJunctions?: number[] // junctions where the ML changed a decision just now
  camera?: { c: Pt; half: number } | null
}
export interface View { cx: number; cy: number; scale: number }
interface Props {
  geom: Geometry
  scene: () => Scene | null
  view?: React.MutableRefObject<View | null>
  title?: string
  accent?: string
  onPick?: (approach: number | null) => void
  selected?: number | null
}

const SLOT_M = 7.5
const CAR_COLORS = ['#8fb4e8', '#d3dae4', '#e08e72', '#79c0a3', '#c9aee6', '#e6c777', '#93a0b4', '#b7c6d9']
const ROAD = '#262c38', WALK = '#1b2230', GROUND = '#0b1019'

interface Road { edge: string; lane: number; s: Pt[]; w: number; left: Pt[] }
const cache = new WeakMap<Geometry, { roads: Road[]; maxLane: Map<string, number> }>()

function offset(s: Pt[], d: number): Pt[] {
  return s.map((p, i) => {
    const a = s[Math.max(0, i - 1)], b = s[Math.min(s.length - 1, i + 1)]
    const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1
    return [p[0] - (dy / L) * d, p[1] + (dx / L) * d] as Pt // left of travel direction
  })
}
function prepared(g: Geometry) {
  let c = cache.get(g)
  if (!c) {
    const roads: Road[] = [], maxLane = new Map<string, number>()
    for (const l of g.lanes) {
      if (l.i) continue
      const k = l.id.lastIndexOf('_'), edge = l.id.slice(0, k), lane = +l.id.slice(k + 1)
      roads.push({ edge, lane, s: l.s, w: l.w, left: offset(l.s, l.w / 2) })
      maxLane.set(edge, Math.max(maxLane.get(edge) ?? 0, lane))
    }
    c = { roads, maxLane }
    cache.set(g, c)
  }
  return c
}
const sig = (c: string) => (c === 'G' || c === 'g' ? '#22c55e' : c === 'y' ? '#facc15' : '#ef4444')
function lamp(state: string, idx: number[]) {
  let r = 'r'
  for (const k of idx) { const c = state[k] ?? 'r'; if (c === 'G' || c === 'g') return 'G'; if (c === 'y') r = 'y' }
  return r
}

export default function RoadMap({ geom, scene, view, title, accent = '#38bdf8', onPick, selected }: Props) {
  const ref = useRef<HTMLCanvasElement>(null)
  const local = useRef<View | null>(null)
  const vref = view ?? local
  const sceneRef = useRef(scene)
  sceneRef.current = scene
  const selRef = useRef(selected)
  selRef.current = selected

  useEffect(() => {
    const cv = ref.current!, ctx = cv.getContext('2d')!
    const { roads, maxLane } = prepared(geom)
    const [x0, y0, x1, y1] = geom.bounds
    let raf = 0, fitted = false, drag: { x: number; y: number; cx: number; cy: number; moved: boolean } | null = null
    const fit = () => {
      const w = cv.clientWidth, h = cv.clientHeight
      vref.current = { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, scale: Math.min(w / (x1 - x0 + 20), h / (y1 - y0 + 20)) }
    }
    const S = (v: View, w: number, h: number) => (x: number, y: number): [number, number] => [w / 2 + (x - v.cx) * v.scale, h / 2 - (y - v.cy) * v.scale]
    const W = (v: View, w: number, h: number) => (sx: number, sy: number): [number, number] => [v.cx + (sx - w / 2) / v.scale, v.cy - (sy - h / 2) / v.scale]

    const path = (pts: Pt[], P: ReturnType<typeof S>) => { ctx.beginPath(); pts.forEach(([x, y], i) => { const [sx, sy] = P(x, y); if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy) }) }

    const drawRoads = (v: View, P: ReturnType<typeof S>) => {
      ctx.lineCap = 'butt'; ctx.lineJoin = 'round'
      for (const r of roads) { path(r.s, P); ctx.strokeStyle = WALK; ctx.lineWidth = (r.w + 5.5) * v.scale; ctx.stroke() }
      for (const j of geom.junctions) {
        const pts = j.s.length ? j.s : []
        path(pts, P); ctx.closePath(); ctx.fillStyle = WALK; ctx.lineWidth = 5.5 * v.scale; ctx.strokeStyle = WALK; ctx.stroke(); ctx.fill()
      }
      for (const r of roads) { path(r.s, P); ctx.strokeStyle = ROAD; ctx.lineWidth = r.w * v.scale + 0.6; ctx.stroke() }
      for (const j of geom.junctions) { path(j.s, P); ctx.closePath(); ctx.fillStyle = ROAD; ctx.fill() }
      for (const l of geom.lanes) if (l.i) { path(l.s, P); ctx.strokeStyle = ROAD; ctx.lineWidth = l.w * v.scale; ctx.stroke() }
      // lane markings: dashed white between lanes of one direction, solid yellow on the centre line
      for (const r of roads) {
        const centre = r.lane === (maxLane.get(r.edge) ?? 0)
        path(r.left, P)
        if (centre) { ctx.setLineDash([]); ctx.strokeStyle = 'rgba(234,179,8,.85)'; ctx.lineWidth = Math.max(0.8, 0.22 * v.scale) }
        else { ctx.setLineDash([2.5 * v.scale, 4.5 * v.scale]); ctx.strokeStyle = 'rgba(226,232,240,.45)'; ctx.lineWidth = Math.max(0.6, 0.14 * v.scale) }
        ctx.stroke()
      }
      ctx.setLineDash([])
      // stop lines and crosswalks at every signalised approach
      for (const a of geom.approaches) {
        const bx = a.b[0], by = a.b[1], dx = a.b[0] - a.a[0], dy = a.b[1] - a.a[1], L = Math.hypot(dx, dy) || 1
        const ux = dx / L, uy = dy / L, nx = -uy, ny = ux
        const lane0 = roads.find((r) => r.edge === a.edge && r.lane === 0)
        const lw = lane0?.w ?? 3.2, nl = (maxLane.get(a.edge) ?? 0) + 1
        const lo = -lw / 2, hi = lw * (nl - 0.5)
        const pt = (back: number, lat: number) => P(bx - ux * back + nx * lat, by - uy * back + ny * lat)
        let [sx, sy] = pt(1.4, lo); let [ex, ey] = pt(1.4, hi)
        ctx.strokeStyle = 'rgba(248,250,252,.9)'; ctx.lineWidth = Math.max(1, 0.5 * v.scale); ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke()
        ctx.strokeStyle = 'rgba(248,250,252,.38)'; ctx.lineWidth = Math.max(0.8, 0.5 * v.scale)
        for (let lat = lo + 0.3; lat <= hi; lat += 1.0) { [sx, sy] = pt(3.2, lat); [ex, ey] = pt(6.6, lat); ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke() }
      }
    }

    const drawSignals = (v: View, P: ReturnType<typeof S>, st: string[], t: number) => {
      const ji = new Map(geom.controlled.map((j, i) => [j, i]))
      for (const h of geom.heads) {
        const state = st[ji.get(h.j) ?? -1]
        if (!state) continue
        const [sx, sy] = P(h.x, h.y), col = sig(lamp(state, h.k)), r = Math.max(2.6, 1.0 * v.scale)
        ctx.fillStyle = '#0a0d14'; ctx.beginPath(); ctx.arc(sx, sy, r + 1.4, 0, Math.PI * 2); ctx.fill()
        ctx.shadowColor = col; ctx.shadowBlur = 9 + 3 * Math.sin(t / 220); ctx.fillStyle = col; ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0
      }
    }

    const drawQueues = (v: View, P: ReturnType<typeof S>, sc: Scene) => {
      geom.approaches.forEach((a, i) => {
        const dx = a.a[0] - a.b[0], dy = a.a[1] - a.b[1], L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L, nx = -uy, ny = ux
        const strip = (m: number, fill: string | null, stroke: string | null, dash: number[]) => {
          const len = Math.min(L, (m / 2) * SLOT_M)
          const p = (along: number, lat: number) => P(a.b[0] + ux * (1.8 + along) + nx * lat * -1, a.b[1] + uy * (1.8 + along) + ny * lat * -1)
          const c = [p(0, -1.6), p(len, -1.6), p(len, 4.8), p(0, 4.8)]
          ctx.beginPath(); c.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath()
          if (fill) { ctx.fillStyle = fill; ctx.fill() }
          if (stroke) { ctx.setLineDash(dash); ctx.strokeStyle = stroke; ctx.lineWidth = Math.max(1.2, 0.35 * v.scale); ctx.stroke(); ctx.setLineDash([]) }
        }
        const full = (q: number) => q >= a.cap * 0.5
        if (sc.queue && sc.queue[i] > 0) strip(sc.queue[i], full(sc.queue[i]) ? 'rgba(244,63,94,.28)' : 'rgba(251,146,60,.2)', null, [])
        if (sc.forecast && sc.forecast[i] > 0.4) strip(sc.forecast[i], null, full(sc.forecast[i]) ? '#f0abfc' : '#a78bfa', [5, 4])
        if (selRef.current === i) { const [sx, sy] = P(a.b[0] + ux * 28, a.b[1] + uy * 28); ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(sx, sy, 11, 0, Math.PI * 2); ctx.stroke() }
      })
    }

    const drawVehicle = (veh: Veh, v: View, P: ReturnType<typeof S>, t: number) => {
      const x = veh[1] / 10, y = veh[2] / 10, type = veh[4], speed = veh[5] / 10
      const len = type === 1 ? 8.5 : type === 2 ? 6.2 : 4.6, wid = type === 1 ? 2.4 : 1.95
      const ex = Math.max(1, Math.min(2.4, 3.6 / (wid * v.scale))) // keep vehicles legible when zoomed out
      const L = len * v.scale * ex, Wd = wid * v.scale * ex
      const [sx, sy] = P(x, y)
      ctx.save(); ctx.translate(sx, sy); ctx.rotate((veh[3] * Math.PI) / 180) // SUMO angle: clockwise from north; (x, y) is the front bumper
      ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.beginPath(); ctx.roundRect(-Wd / 2 + 1, 1, Wd, L, Math.min(2.5, Wd / 2.5)); ctx.fill()
      const body = type === 2 ? '#f8fafc' : type === 1 ? '#8696ab' : CAR_COLORS[veh[0] % CAR_COLORS.length]
      ctx.fillStyle = body; ctx.beginPath(); ctx.roundRect(-Wd / 2, 0, Wd, L, Math.min(2.5, Wd / 2.5)); ctx.fill()
      if (Wd > 5) { // windscreen, roof and lights only when there is room to see them
        ctx.fillStyle = 'rgba(15,23,42,.55)'; ctx.fillRect(-Wd * 0.38, L * 0.12, Wd * 0.76, L * 0.2)
        if (type === 1) { ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.fillRect(-Wd / 2, L * 0.34, Wd, 1) }
        ctx.fillStyle = '#fde68a'; ctx.fillRect(-Wd * 0.4, 0, Wd * 0.22, 1.4); ctx.fillRect(Wd * 0.18, 0, Wd * 0.22, 1.4)
      }
      if (speed < 1.0) { ctx.fillStyle = '#ef4444'; ctx.fillRect(-Wd * 0.45, L - 1.8, Wd * 0.28, 1.8); ctx.fillRect(Wd * 0.17, L - 1.8, Wd * 0.28, 1.8) } // brake lights
      if (type === 2) {
        ctx.fillStyle = '#dc2626'; ctx.fillRect(-Wd / 2, L * 0.45, Wd, Math.max(1.2, L * 0.1))
        const flash = Math.floor(t / 140) % 2 === 0
        ctx.shadowBlur = 10; ctx.shadowColor = flash ? '#ef4444' : '#3b82f6'
        ctx.fillStyle = flash ? '#ef4444' : '#3b82f6'; ctx.fillRect(-Wd * 0.45, L * 0.2, Wd * 0.4, Math.max(1.4, L * 0.06))
        ctx.fillStyle = flash ? '#3b82f6' : '#ef4444'; ctx.fillRect(Wd * 0.05, L * 0.2, Wd * 0.4, Math.max(1.4, L * 0.06)); ctx.shadowBlur = 0
      }
      ctx.restore()
    }

    const frame = (t: number) => {
      const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight
      if (w < 10 || h < 10) { raf = requestAnimationFrame(frame); return }
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr) }
      if (!vref.current || (!fitted && !view)) fit()
      fitted = true
      const v = vref.current!, P = S(v, w, h)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.fillStyle = GROUND; ctx.fillRect(0, 0, w, h)
      drawRoads(v, P)
      const sc = sceneRef.current()
      for (const j of geom.junctions) {
        if (!j.tls) continue
        const [sx, sy] = P(j.c[0], j.c[1]); ctx.font = '600 11px system-ui'; const tw = ctx.measureText(j.id).width
        ctx.fillStyle = 'rgba(8,12,20,.8)'; ctx.beginPath(); ctx.roundRect(sx + 12, sy - 26, tw + 12, 17, 8); ctx.fill(); ctx.fillStyle = '#94a3b8'; ctx.fillText(j.id, sx + 18, sy - 14)
      }
      if (sc) {
        drawQueues(v, P, sc)
        const evs = new Map((sc.ambulances ?? []).map((a) => [a.id, a]))
        for (const a of sc.ambulances ?? []) if (a.trail && a.trail.length > 1) { path(a.trail, P); ctx.strokeStyle = STATUS_COLOR[a.status] + '99'; ctx.lineWidth = Math.max(2, 0.9 * v.scale); ctx.lineJoin = 'round'; ctx.stroke() }
        for (const veh of sc.vehicles) if (veh[4] !== 2) drawVehicle(veh, v, P, t)
        for (const veh of sc.vehicles) if (veh[4] === 2) drawVehicle(veh, v, P, t)
        drawSignals(v, P, sc.signals, t)
        for (const veh of sc.vehicles) {
          const a = veh[4] === 2 ? evs.get(veh[0]) : undefined
          if (!a) continue
          const [sx, sy] = P(veh[1] / 10, veh[2] / 10), col = STATUS_COLOR[a.status], pulse = 16 + 3 * Math.sin(t / 160)
          ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(sx, sy, pulse, 0, Math.PI * 2); ctx.stroke()
          const text = `AMBULANCE · ${STATUS_TEXT[a.status]}${a.seconds && a.status !== 0 && a.status !== 3 ? ` · ${a.seconds | 0} s` : ''}`
          ctx.font = '600 11px system-ui'; const tw = ctx.measureText(text).width
          ctx.fillStyle = 'rgba(8,12,20,.88)'; ctx.beginPath(); ctx.roundRect(sx + 22, sy - 26, tw + 22, 20, 10); ctx.fill()
          ctx.fillStyle = col; ctx.beginPath(); ctx.arc(sx + 32, sy - 16, 4, 0, Math.PI * 2); ctx.fill()
          ctx.fillStyle = '#e2e8f0'; ctx.fillText(text, sx + 40, sy - 12)
        }
        for (const k of sc.mlJunctions ?? []) {
          const jn = geom.junctions.find((j) => j.id === geom.controlled[k]); if (!jn) continue
          const [sx, sy] = P(jn.c[0], jn.c[1]); ctx.strokeStyle = '#c084fc'; ctx.lineWidth = 3; ctx.shadowColor = '#c084fc'; ctx.shadowBlur = 12
          ctx.beginPath(); ctx.arc(sx, sy, Math.max(16, 15 * v.scale) + 2 * Math.sin(t / 120), 0, Math.PI * 2); ctx.stroke(); ctx.shadowBlur = 0
        }
        if (sc.camera) {
          const [ax, ay] = P(sc.camera.c[0] - sc.camera.half, sc.camera.c[1] + sc.camera.half), [bx, by] = P(sc.camera.c[0] + sc.camera.half, sc.camera.c[1] - sc.camera.half)
          ctx.setLineDash([7, 5]); ctx.strokeStyle = '#22d3ee'; ctx.lineWidth = 1.5; ctx.strokeRect(ax, ay, bx - ax, by - ay); ctx.setLineDash([])
          ctx.fillStyle = '#22d3ee'; ctx.font = '600 10px system-ui'; ctx.fillText('camera footprint', ax + 6, ay + 14)
        }
      }
      if (title) {
        ctx.font = '700 13px system-ui'; const tw = ctx.measureText(title).width
        ctx.fillStyle = 'rgba(8,12,20,.8)'; ctx.beginPath(); ctx.roundRect(10, 10, tw + 24, 28, 14); ctx.fill()
        ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(24, 24, 4, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#e2e8f0'; ctx.fillText(title, 34, 29)
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const v = vref.current!, r = cv.getBoundingClientRect(), [wx, wy] = W(v, cv.clientWidth, cv.clientHeight)(e.clientX - r.left, e.clientY - r.top)
      const scale = Math.min(40, Math.max(0.3, v.scale * Math.exp(-e.deltaY * 0.0015)))
      vref.current = { scale, cx: wx - (wx - v.cx) * (v.scale / scale), cy: wy - (wy - v.cy) * (v.scale / scale) }
    }
    const onDown = (e: PointerEvent) => { const v = vref.current!; drag = { x: e.clientX, y: e.clientY, cx: v.cx, cy: v.cy, moved: false } }
    const onMove = (e: PointerEvent) => {
      if (!drag) return
      const v = vref.current!
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) drag.moved = true
      vref.current = { ...v, cx: drag.cx - (e.clientX - drag.x) / v.scale, cy: drag.cy + (e.clientY - drag.y) / v.scale }
    }
    const onUp = (e: PointerEvent) => {
      if (drag && !drag.moved && onPick) {
        const r = cv.getBoundingClientRect(), [wx, wy] = W(vref.current!, cv.clientWidth, cv.clientHeight)(e.clientX - r.left, e.clientY - r.top)
        let best: number | null = null, bd = 25
        geom.approaches.forEach((a, i) => { const d = Math.hypot(a.b[0] + (a.a[0] - a.b[0]) * 0.15 - wx, a.b[1] + (a.a[1] - a.b[1]) * 0.15 - wy); if (d < bd) { bd = d; best = i } })
        onPick(best)
      }
      drag = null
    }
    cv.addEventListener('wheel', onWheel, { passive: false }); cv.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp); cv.addEventListener('dblclick', fit)
    return () => {
      cancelAnimationFrame(raf); cv.removeEventListener('wheel', onWheel); cv.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); cv.removeEventListener('dblclick', fit)
    }
  }, [geom, vref, view, title, accent, onPick])

  return <canvas ref={ref} className="map" title="Scroll to zoom · drag to pan · double-click to fit" />
}
