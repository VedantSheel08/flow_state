import { ChevronDown, Loader2, Pause, Play, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Camera } from '../map/camera'
import { canopyCache, groundCache, render, type RenderState } from '../map/renderer'
import { buildScene, type Scene } from '../map/scene'
import {
  LOWER_IS_BETTER, STATUS_TEXT, evState, isAmbulance, loadIndex, loadNetwork, loadScenario, locate, runComparison, sample, sideId, spriteCode, windowFor,
  type AggStat, type DemoAsset, type DemoFocus, type DemoIndex, type DemoSide, type DemoUnit, type UnitSide,
} from '../lib/flowstateDemo'
import { cn, num } from '../lib/format'
import type { NetworkGeom } from '../lib/types'
import { card, signed } from '../lib/resultsTheme'
import { Cap, H2 } from './resultsUi'

type Loaded = { asset: DemoAsset; net: NetworkGeom; scene: Scene }
const SPEED = 1.5 // replay a little faster than real time
const panel = 'rounded-[22px] bg-white/60 shadow-[0_10px_40px_-14px_rgb(12_19_34/0.2),inset_0_0_0_1px_rgb(255_255_255/0.75),0_0_0_1px_rgb(12_19_34/0.05)] backdrop-blur-2xl'
// Map frame: dark, very transparent hairline, soft blue shadow. No backdrop blur here: blurring behind a live canvas is what made the page lag.
const shell = 'rounded-[30px] bg-[rgb(12_19_34/0.04)] shadow-[0_34px_80px_-26px_rgb(37_99_235/0.30),0_12px_30px_-14px_rgb(12_19_34/0.18),inset_0_0_0_1px_rgb(12_19_34/0.12)]'

const secs = (v: number | null | undefined) => (v == null || !isFinite(v) ? '—' : `${v.toFixed(v < 10 ? 1 : 0)} s`)

// ---- one map ---------------------------------------------------------------
interface Swap { canvas: HTMLCanvasElement | null; view: Camera; key: string; builds: number }
const swapIn = (target: Swap, mine: Swap) => { Object.assign(target, mine) }
const swapOut = (target: Swap, mine: Swap) => { Object.assign(mine, target) }

/** Camera shared by both maps so they always show the same place and zoom. 'junction' frames the signal nearest the subject; dragging or zooming switches to 'free'. */
export type ViewMode = 'junction' | 'follow' | 'network' | 'free'
export interface SharedView { cx: number; cy: number; scale: number }
const MIN_ZOOM = 0.5
const MAX_ZOOM = 40

interface MapProps {
  scene: Scene
  net: NetworkGeom
  side: DemoSide
  /** vehicle to follow on this side (null: follow the scenario's focus point instead) */
  subject: number | null
  focus: DemoFocus | undefined
  clock: React.RefObject<number>
  mode: ViewMode
  view: React.RefObject<SharedView>
  onFree: () => void
  label: string
  tlsIds: string[]
  mainCodes: boolean
}

/** Main's own map renderer (roads, curbs, crosswalks, signal heads, buildings, vehicle sprites) drawing one recorded run. Drag to move, scroll to zoom. */
function SimMap({ scene, net, side, subject, focus, clock, mode, view, onFree, label, tlsIds, mainCodes }: MapProps) {
  const ref = useRef<HTMLCanvasElement>(null)
  const modeRef = useRef(mode)
  useEffect(() => {
    modeRef.current = mode
    if (mode === 'junction' || mode === 'follow') view.current.scale = 0 // re-frame from the default zoom
  }, [mode, view])
  const miniRef = useRef<HTMLCanvasElement>(null)
  const [mx0, my0, mx1, my1] = scene.roadBounds
  const MINI = 132
  const miniK = (MINI - 16) / Math.max(mx1 - mx0, my1 - my0)
  const freeRef = useRef(onFree)
  useEffect(() => { freeRef.current = onFree }, [onFree])
  const lastT = useRef(-1)

  useEffect(() => {
    const cv = ref.current!
    const ctx = cv.getContext('2d')!
    const cam = new Camera()
    cam.minScale = MIN_ZOOM
    cam.maxScale = MAX_ZOOM
    const mine = { ground: { canvas: null, view: new Camera(), key: '', builds: 0 } as Swap, canopy: { canvas: null, view: new Camera(), key: '', builds: 0 } as Swap }
    const [rx0, ry0, rx1, ry1] = scene.roadBounds
    const signalled = net.junctions.filter((j) => tlsIds.includes(j.id) || j.type === 'traffic_light')
    const roads = net.edges.map((e) => e.lanes.find((l) => l.role === 'car')?.shape).filter((sh): sh is number[] => !!sh && sh.length >= 4)
    let current: (typeof signalled)[number] | null = null
    let roadsCache: HTMLCanvasElement | null = null, miniAt = 0
    let raf = 0, visible = true, lastTs = 0, lastKey = ''
    let held: [number, number] | null = focus?.x != null && focus.y != null ? [focus.x, focus.y] : null
    let drag: { x: number; y: number; cx: number; cy: number } | null = null
    const io = new IntersectionObserver((e) => { visible = e.some((x) => x.isIntersecting) })
    io.observe(cv)

    const subjectAt = (placed: ReturnType<typeof sample>) => {
      const me = subject != null ? placed.find((p) => p.id === subject) : null
      if (me) held = [me.x, me.y]
      else if (!held && subject != null) {
        const r = side.frames.find((fr) => fr.v.some((q) => q[0] === subject))?.v.find((q) => q[0] === subject)
        if (r) held = [r[1] / 10, r[2] / 10]
      }
      return me ?? null
    }

    const frame = () => {
      raf = requestAnimationFrame(frame)
      const dpr = Math.min(1.25, window.devicePixelRatio || 1), w = cv.clientWidth, h = cv.clientHeight
      if (w < 10 || h < 10 || !visible) return
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr) }
      const t = clock.current
      const placed = sample(side.frames, t)
      const me = subjectAt(placed)
      const jumped = Math.abs(t - lastT.current) > 2
      lastT.current = t
      cam.setViewport(w, h)
      const v = view.current, m = modeRef.current
      const ts = performance.now()
      const ease = 1 - Math.exp(-Math.min(0.25, lastTs ? (ts - lastTs) / 1000 : 0.016) * 8) // frame-rate independent glide
      lastTs = ts
      if (m === 'network') {
        v.scale = Math.min(w / (rx1 - rx0 + 60), h / (ry1 - ry0 + 60))
        v.cx = (rx0 + rx1) / 2; v.cy = (ry0 + ry1) / 2
      } else if (m === 'junction') {
        // frame the signal nearest the subject; glide to the next one as it travels (with a margin so it does not flicker between two)
        const at = held ?? [(rx0 + rx1) / 2, (ry0 + ry1) / 2]
        const dist = (j: { x: number; y: number }) => Math.hypot(j.x - at[0], j.y - at[1])
        const best = signalled.reduce((a, j) => (dist(j) < dist(a) ? j : a), signalled[0])
        if (best && (!current || dist(best) + 25 < dist(current))) current = best
        // centre the junction, but slide towards a moving subject past 20% of the view so it never leaves the frame
        const lim = Math.max(20, (0.2 * Math.min(w, h)) / Math.max(1, v.scale))
        const slide = (d: number) => d - Math.max(-lim, Math.min(lim, d))
        const tx = (current?.x ?? (rx0 + rx1) / 2) + (current && held && me ? slide(held[0] - current.x) : 0)
        const ty = (current?.y ?? (ry0 + ry1) / 2) + (current && held && me ? slide(held[1] - current.y) : 0)
        if (!v.scale || v.scale < 1) { v.scale = Math.min(w, h) / 185; v.cx = tx; v.cy = ty }
        v.cx = jumped ? tx : v.cx + (tx - v.cx) * ease
        v.cy = jumped ? ty : v.cy + (ty - v.cy) * ease
      } else if (m === 'follow') {
        const [hx, hy] = held ?? [(rx0 + rx1) / 2, (ry0 + ry1) / 2]
        if (!v.scale || v.scale < 3) v.scale = Math.min(w, h) / 120
        v.cx = jumped ? hx : v.cx + (hx - v.cx) * Math.max(ease, 0.2)
        v.cy = jumped ? hy : v.cy + (hy - v.cy) * Math.max(ease, 0.2)
      }
      cam.jump(v.cx, v.cy, v.scale)
      const key = `${t.toFixed(3)}|${v.cx.toFixed(2)}|${v.cy.toFixed(2)}|${v.scale.toFixed(4)}|${w}|${h}`
      if (key === lastKey) return // paused and still: nothing to redraw
      lastKey = key
      const dt = side.frames.length > 1 ? side.frames[1].t - side.frames[0].t : 1
      const frameState = side.frames[Math.min(side.frames.length - 1, Math.max(0, Math.floor((t - side.frames[0].t) / dt)))]
      const st: RenderState = {
        vehicles: placed.map((p) => ({
          id: String(p.id), code: spriteCode(p, mainCodes), x: p.x, y: p.y, angle: p.angle, speed: p.speed,
          signals: (p.speed < 0.5 ? 8 : 0) | (isAmbulance(p, mainCodes) ? 2048 : 0), // brake lamps when nearly stopped; the beacon bit for emergency vehicles
          alpha: 1,
        })),
        persons: [], tls: Object.fromEntries(tlsIds.map((id, i) => [id, frameState.s[i] ?? ''])),
        layers: { buildings: true, trees: true, labels: true, pedestrians: false, flow: false, signals: true },
        selected: null, selectedJunction: null, hoverEdge: null, pickedEdge: null, tracked: new Map(), incidents: [], edges: {}, route: null, busLines: [],
        now: ts, pick: false, overlays: [], t: 0, safe: { x0: 0, y0: 0, x1: w, y1: h },
      }
      swapIn(groundCache, mine.ground); swapIn(canopyCache, mine.canopy) // each map keeps its own cached bitmaps
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, cv.width, cv.height)
      render(ctx, scene, cam, st, dpr)
      swapOut(groundCache, mine.ground); swapOut(canopyCache, mine.canopy)
      const mc = miniRef.current
      if (mc && ts - miniAt > 100) { // the minimap at 10 Hz; its street outline rasterised once
        miniAt = ts
        const mctx = mc.getContext('2d')!
        if (mc.width !== Math.round(MINI * dpr)) { mc.width = Math.round(MINI * dpr); mc.height = Math.round(MINI * dpr); roadsCache = null }
        const X = (x: number) => 8 + (x - rx0) * miniK, Y = (y: number) => MINI - 8 - (y - ry0) * miniK
        if (!roadsCache) {
          roadsCache = document.createElement('canvas')
          roadsCache.width = mc.width; roadsCache.height = mc.height
          const rc = roadsCache.getContext('2d')!
          rc.setTransform(dpr, 0, 0, dpr, 0, 0)
          rc.lineCap = 'round'; rc.lineJoin = 'round'; rc.strokeStyle = '#c3cad6'; rc.lineWidth = 3
          for (const sh of roads) {
            rc.beginPath(); rc.moveTo(X(sh[0]), Y(sh[1]))
            for (let i = 2; i < sh.length; i += 2) rc.lineTo(X(sh[i]), Y(sh[i + 1]))
            rc.stroke()
          }
        }
        mctx.setTransform(1, 0, 0, 1, 0, 0)
        mctx.clearRect(0, 0, mc.width, mc.height)
        mctx.drawImage(roadsCache, 0, 0)
        mctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        mctx.fillStyle = 'rgba(60,70,90,0.55)'
        for (const p of placed) mctx.fillRect(X(p.x) - 0.9, Y(p.y) - 0.9, 1.8, 1.8)
        const vw = (w / v.scale) * miniK, vh = (h / v.scale) * miniK
        mctx.strokeStyle = '#0c1322'; mctx.lineWidth = 1.5
        mctx.beginPath(); mctx.roundRect(X(v.cx) - vw / 2, Y(v.cy) - vh / 2, vw, vh, 3); mctx.stroke()
        const dot = me ?? (held && !subject ? { x: held[0], y: held[1] } : null)
        if (dot) { mctx.fillStyle = '#ff3b30'; mctx.beginPath(); mctx.arc(X(dot.x), Y(dot.y), 3, 0, Math.PI * 2); mctx.fill() }
      }
      if (me) { // ring the unit so it can be found in traffic
        const [sx, sy] = cam.toScreen(me.x, me.y)
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        ctx.beginPath(); ctx.arc(sx, sy, Math.max(12, 7 * cam.scale), 0, Math.PI * 2)
        ctx.strokeStyle = '#ff3b30'; ctx.lineWidth = 2; ctx.stroke()
      }
    }
    raf = requestAnimationFrame(frame)

    const clampView = () => {
      const v = view.current
      v.cx = Math.max(rx0 - 40, Math.min(rx1 + 40, v.cx))
      v.cy = Math.max(ry0 - 40, Math.min(ry1 + 40, v.cy))
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      if (modeRef.current !== 'free') freeRef.current()
      const v = view.current
      v.scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.scale * Math.exp(-e.deltaY * 0.0016)))
    }
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return
      cv.setPointerCapture(e.pointerId)
      drag = { x: e.clientX, y: e.clientY, cx: view.current.cx, cy: view.current.cy }
      cv.style.cursor = 'grabbing'
    }
    const onMove = (e: PointerEvent) => {
      if (!drag) return
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y
      if (Math.hypot(dx, dy) < 3) return
      if (modeRef.current !== 'free') { freeRef.current(); modeRef.current = 'free' }
      const v = view.current
      v.cx = drag.cx - dx / v.scale
      v.cy = drag.cy + dy / v.scale
      clampView()
    }
    const onUp = () => { drag = null; cv.style.cursor = 'grab' }
    cv.addEventListener('wheel', onWheel, { passive: false })
    cv.addEventListener('pointerdown', onDown); cv.addEventListener('pointermove', onMove); cv.addEventListener('pointerup', onUp); cv.addEventListener('pointercancel', onUp)
    return () => {
      cancelAnimationFrame(raf); io.disconnect()
      cv.removeEventListener('wheel', onWheel); cv.removeEventListener('pointerdown', onDown); cv.removeEventListener('pointermove', onMove)
      cv.removeEventListener('pointerup', onUp); cv.removeEventListener('pointercancel', onUp)
    }
  }, [scene, net, side, subject, focus, clock, view, miniK, tlsIds, mainCodes])

  const jumpTo = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const wx = mx0 + (e.clientX - r.left - 8) / miniK, wy = my0 + (MINI - 8 - (e.clientY - r.top)) / miniK
    onFree()
    const v = view.current
    if (!v.scale || v.scale < 3) v.scale = 7
    v.cx = Math.max(mx0, Math.min(mx1, wx)); v.cy = Math.max(my0, Math.min(my1, wy))
  }

  return (
    <>
      <canvas ref={ref} role="img" aria-label={`${label}. Drag to move, scroll to zoom.`} className="block h-full w-full cursor-grab touch-none" />
      <canvas ref={miniRef} aria-label="Whole network. Click to move there." onPointerDown={jumpTo} style={{ width: MINI, height: MINI }}
        className="absolute bottom-3 right-3 z-10 cursor-pointer rounded-[18px] bg-white/75 shadow-[0_6px_20px_-8px_rgb(12_19_34/0.35),0_0_0_1px_rgb(12_19_34/0.08)] backdrop-blur-xl" />
    </>
  )
}

// ---- measured numbers ------------------------------------------------------
function UnitTag({ side, id, t, span, label }: { side: DemoSide; id: number; t: number; span: UnitSide; label: string }) {
  const e = evState(side.frames, id, t)
  const here = locate(side.frames, id, t)
  const text = here
    ? e ? `${label} ${STATUS_TEXT[e.status] ?? 'moving'}${e.metresToSignal > 0 && e.status !== 0 ? ` · ${Math.round(e.metresToSignal)} m to signal` : ''}` : `${label} moving`
    : span.last != null && t > span.last ? `${label} arrived · ${secs(span.travelS)} trip` : `${label} not yet on the network`
  return <div className="tnum truncate text-center text-[13px] leading-none text-ink-2">{text}</div>
}

function Kpi({ label, left, right, unit = 's', digits = 0, lowerIsBetter = true }: { label: string; left: number | null | undefined; right: number | null | undefined; unit?: string; digits?: number; lowerIsBetter?: boolean }) {
  const ok = left != null && right != null
  const good = ok && (lowerIsBetter ? right < left : right > left)
  const same = ok && Math.abs(right - left) < 0.05
  return (
    <div className={cn('flex min-w-0 flex-col gap-2 p-5', panel)}>
      <span className="text-[12px] font-[550] text-ink-3">{label}</span>
      <div className="tnum flex flex-wrap items-baseline gap-x-2">
        <span className={cn('text-[30px] font-[650] leading-none tracking-[-0.03em]', !ok || same || good ? 'text-ink' : 'text-bad')}>{ok ? num(right, digits) : '—'}{ok ? unit : ''}</span>
        <span className="text-[13px] text-ink-4">from {ok ? num(left, digits) : '—'}{ok ? unit : ''}</span>
      </div>
    </div>
  )
}

const AGG_LABEL: Record<string, string> = {
  ev_travel_mean_s: 'Unit call-to-scene time',
  ev_stopped_red_s: 'Units stopped at red',
  ev_stopped_queue_s: 'Units stuck in queue',
  mean_wait_civilian_s: 'Other vehicles’ wait',
  mean_travel_s: 'Other vehicles’ trip time',
  time_loss_s: 'Other vehicles’ time lost',
  queue_total_mean_veh: 'Vehicles queued',
  throughput_vph: 'Throughput (veh/h)',
  teleports: 'Stuck vehicles (SUMO teleports)',
}
const AGG_GROUPS: { title: string; keys: string[] }[] = [
  { title: 'For emergency units', keys: ['ev_travel_mean_s', 'ev_stopped_red_s', 'ev_stopped_queue_s'] },
  { title: 'For everyone else', keys: ['mean_wait_civilian_s', 'mean_travel_s', 'time_loss_s', 'queue_total_mean_veh', 'throughput_vph', 'teleports'] },
]

function Aggregate({ asset }: { asset: DemoAsset }) {
  const runs = asset.aggregate.episodes
  const groups = AGG_GROUPS.filter((g) => g.keys.some((k) => asset.aggregate.metrics[k]))
  return (
    <div className="grid gap-4 min-[860px]:grid-cols-2">
      {groups.map((g) => (
        <div key={g.title} className={cn('p-5', panel)}>
          <div className="pb-2 text-[13px] font-semibold text-ink">{g.title}</div>
          {g.keys.map((k, i) => {
            const s: AggStat | undefined = asset.aggregate.metrics[k]
            if (!s || (s.left_mean === 0 && s.right_mean === 0)) return null // nothing happened on either side
            const lower = LOWER_IS_BETTER.has(k) || k === 'teleports'
            const good = lower ? s.mean_diff < 0 : s.mean_diff > 0 // mean_diff is FlowState − fixed-time
            const sure = s.pairs >= 3 && (s.ci95[0] > 0 || s.ci95[1] < 0) // the 95% interval excludes zero (needs a few runs)
            return (
              <div key={k} title={`95% interval of the change: ${num(s.ci95[0], 1)} to ${num(s.ci95[1], 1)}`} className={cn('flex items-center gap-3 py-3', i > 0 && 'border-t border-border')}>
                <div className="min-w-0 flex-1">
                  <div className="text-[14px] font-[550] text-ink">{AGG_LABEL[k] ?? k}</div>
                  <div className="tnum text-[12px] text-ink-3">{num(s.left_mean, 1)} → {num(s.right_mean, 1)} · better in {s.better} of {runs} runs{s.worse ? `, worse in ${s.worse}` : ''}</div>
                </div>
                <span className={cn('tnum shrink-0 rounded-full px-2.5 py-1 text-[13px] font-semibold', !sure || s.pct == null ? 'bg-white/60 text-ink-3' : good ? 'bg-white/80 text-good' : 'bg-white/80 text-bad')}>
                  {s.pct == null ? '—' : signed(s.pct)}
                </span>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function Units({ units, example, onPick }: { units: DemoUnit[]; example: number | null; onPick: (id: number) => void }) {
  return (
    <div className="grid gap-3 min-[720px]:grid-cols-3">
      {units.map((u: DemoUnit, i) => {
        const on = u.id === example
        const stopped = (s: DemoUnit['left']) => s.stoppedRedS + s.stoppedQueueS
        return (
          <button key={u.id} type="button" onClick={() => onPick(u.id)} aria-pressed={on}
            className={cn('flex flex-col gap-1.5 p-5 text-left transition-shadow', on ? 'rounded-[22px] bg-white/85 shadow-[0_0_0_2px_#0c1322] backdrop-blur-2xl' : cn(panel, 'hover:bg-white/80'))}>
            <span className="flex items-center justify-between text-[13px] font-semibold text-ink">{u.label ?? `Ambulance ${i + 1}`}{on && <span className="text-[11px] font-[550] text-ink-3">Showing</span>}</span>
            <span className="tnum text-[20px] font-[650] leading-none tracking-[-0.02em]">{secs(u.left.travelS)} → {secs(u.right.travelS)}</span>
            <span className={cn('tnum text-[13px] font-[550]', u.savedS != null && u.savedS < 0 ? 'text-bad' : 'text-ink-2')}>
              {u.savedS == null ? 'Did not arrive in one run' : u.savedS === 0 ? 'Same' : `${Math.abs(u.savedS).toFixed(0)} s ${u.savedS > 0 ? 'faster' : 'slower'}`}
            </span>
            <span className="tnum text-[12px] text-ink-3">Stopped {secs(stopped(u.left))} → {secs(stopped(u.right))}</span>
          </button>
        )
      })}
    </div>
  )
}

// ---- section ---------------------------------------------------------------
interface CatalogScenario { id: string; title: string; tagline: string }

export function SideBySide() {
  const [index, setIndex] = useState<DemoIndex | { error: string } | null>(null)
  const [catalog, setCatalog] = useState<CatalogScenario[]>([])
  const [tab, setTab] = useState<string | null>(null)
  const [data, setData] = useState<Record<string, Loaded>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [started, setStarted] = useState(0)
  const [catalogReady, setCatalogReady] = useState(false)
  useEffect(() => {
    let live = true
    loadIndex().then((ix) => { if (live) { setIndex(ix); setTab((s) => s ?? (ix.scenarios.some((x) => x.id === 'ambulance') ? 'ambulance' : ix.scenarios[0]?.id ?? null)) } }, (e: unknown) => { if (live) setIndex({ error: e instanceof Error ? e.message : String(e) }) })
    fetch('/api/scenarios').then((r) => (r.ok ? r.json() : [])).then((list: CatalogScenario[]) => { if (live) setCatalog(Array.isArray(list) ? list : []) }, () => {}).finally(() => { if (live) setCatalogReady(true) })
    return () => { live = false }
  }, [])
  useEffect(() => {
    if (!tab || !index || 'error' in index || data[tab] || !catalogReady) return
    let live = true
    const recorded = index.scenarios.some((x) => x.id === tab)
    const live_ = catalog.length > 0 // with the simulation backend every scenario, including the first one, is simulated now; without it the recorded runs are shown
    setBusy(live_); setError(null); setStarted(performance.now())
    const job = live_ ? runComparison(tab, null).catch(() => (recorded ? loadScenario(tab) : Promise.reject(new Error('Simulation backend unreachable')))) : loadScenario(tab)
    job.then(async (asset) => {
      const net = await loadNetwork(asset.network ?? index.network)
      if (live) setData((d) => ({ ...d, [tab]: { asset, net, scene: buildScene(net) } }))
    }).catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)) }).finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [tab, index, data, catalog, catalogReady])

  if (index && 'error' in index) {
    return (
      <section className={cn(card, 'flex flex-col gap-1 p-6')}>
        <H2>Side-by-side replay unavailable</H2>
        <Cap>{index.error}</Cap>
      </section>
    )
  }
  const recorded = index?.scenarios ?? []
  const options = [...recorded.map((x) => ({ id: x.id, title: x.title })), ...catalog.filter((c) => !recorded.some((r) => r.id === c.id)).map((c) => ({ id: c.id, title: c.title }))]
  const loaded = tab ? data[tab] : undefined
  const runs = loaded?.asset.aggregate.episodes ?? 0
  return (
    <section className="flex flex-col gap-7">
      {index && tab && (
        <div className="flex flex-col items-center gap-2.5">
          <div className="relative">
            <select aria-label="Scenario" value={tab} onChange={(e) => { setData((d) => { const n = { ...d }; delete n[e.target.value]; return n }); setTab(e.target.value) }}
              className="h-11 cursor-pointer appearance-none rounded-full bg-white/70 bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%2346505f%22 stroke-width=%222.5%22><path d=%22m6 9 6 6 6-6%22/></svg>')] bg-[length:12px] bg-[right_18px_center] bg-no-repeat pl-6 pr-11 text-[15px] font-[550] text-ink shadow-[0_0_0_1px_rgb(12_19_34/0.08),0_6px_20px_-10px_rgb(12_19_34/0.25)] outline-none backdrop-blur-xl">
              {options.map((o) => <option key={o.id} value={o.id}>{o.title}</option>)}
            </select>
            <ChevronDown size={15} strokeWidth={2.5} className="pointer-events-none absolute right-5 top-1/2 -translate-y-1/2 text-ink-3" />
          </div>
          <p className="m-0 text-center text-[13px] text-ink-3">
            {busy ? 'Generating a new run…' : error ? <span className="text-bad">{error}</span> : loaded ? `${loaded.asset.tagline ?? ''}${loaded.asset.tagline ? ' · ' : ''}${runs === 1 ? 'one SUMO run per side' : `${runs} paired SUMO runs`}` : ''}
          </p>
        </div>
      )}
      {loaded && !busy ? <Replay key={`${tab}:${loaded.asset.seed}`} data={loaded} /> : <Generating busy={busy} started={started} title={options.find((o) => o.id === tab)?.title ?? ''} />}
    </section>
  )
}

/** Shown while the backend runs the two simulations: the two empty map frames with what is happening and the elapsed time. */
function Generating({ busy, started, title }: { busy: boolean; started: number; title: string }) {
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    if (!busy) return
    const id = setInterval(() => setNow(performance.now()), 250)
    return () => clearInterval(id)
  }, [busy])
  const secsGone = Math.max(0, (now - started) / 1000)
  const steps = ['Building the scenario and drawing its traffic', 'Running fixed-time signals in SUMO', 'Running FlowState on the same traffic', 'Recording both runs']
  const step = Math.min(steps.length - 1, Math.floor(secsGone / 4.5))
  return (
    <div className="flex flex-col gap-7">
      <div className="grid gap-x-10 gap-y-12 py-2 min-[860px]:grid-cols-2">
        {['Fixed-time', 'FlowState'].map((n) => (
          <div key={n} className="flex flex-col items-center gap-6">
            <span className="text-[34px] font-[650] leading-none tracking-[-0.03em] text-ink">{n}</span>
            <div className={cn('w-full p-2.5', shell)}>
              <div className="relative flex h-[380px] items-center justify-center overflow-hidden rounded-[22px] bg-[#eef0f3] min-[860px]:h-[490px]">
                <div className="absolute inset-0 animate-pulse bg-[linear-gradient(110deg,transparent_30%,rgb(255_255_255/0.55)_50%,transparent_70%)]" />
                {busy && <Loader2 size={22} className="animate-spin text-ink-4" />}
              </div>
            </div>
          </div>
        ))}
      </div>
      {busy && (
        <div className={cn('flex flex-col items-center gap-2 px-6 py-5 text-center', panel)}>
          <span className="text-[15px] font-semibold text-ink">Generating {title}</span>
          <span className="text-[13px] text-ink-3">{steps[step]}… <span className="tnum">{secsGone.toFixed(0)} s</span></span>
          <div className="mt-1 h-1 w-[260px] overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-ink transition-[width] duration-300" style={{ width: `${Math.min(95, (secsGone / 18) * 100)}%` }} /></div>
        </div>
      )}
    </div>
  )
}

/** Per-second counts read from the recorded frames of one side: vehicles standing still and vehicles on the network. */
function series(side: DemoSide): { t: number; stopped: number; moving: number }[] {
  const out: { t: number; stopped: number; moving: number }[] = []
  let next = -Infinity
  for (const f of side.frames) {
    if (f.t < next) continue
    next = Math.floor(f.t) + 1
    let stopped = 0
    for (const v of f.v) if (v[5] < 5) stopped++
    out.push({ t: Math.round(f.t), stopped, moving: f.v.length - stopped })
  }
  return out
}

const FIXED = '#eb6834', FLOW = '#2a78d6'

/** Live comparison: vehicles stopped in traffic on each side, drawn up to the replay position (both curves come from the same recorded runs as the maps). */
function LiveChart({ asset, t, win }: { asset: DemoAsset; t: number; win: [number, number] }) {
  const data = useMemo(() => {
    const L = series(asset.left), R = series(asset.right)
    const byT = new Map(R.map((r) => [r.t, r.stopped]))
    return L.filter((p) => p.t >= win[0] && p.t <= win[1]).map((p) => ({ t: p.t, fixed: p.stopped, flow: byT.get(p.t) ?? null }))
  }, [asset, win])
  const shown = data.filter((p) => p.t <= t)
  const last = shown[shown.length - 1]
  const ymax = Math.max(5, ...data.map((p) => Math.max(p.fixed, p.flow ?? 0)))
  const mean = (k: 'fixed' | 'flow') => (shown.length ? shown.reduce((a, p) => a + (p[k] ?? 0), 0) / shown.length : 0)
  return (
    <div className={cn('flex flex-col gap-3 px-6 pb-4 pt-5', panel)}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[15px] font-semibold text-ink">Vehicles stopped in traffic</span>
          <span className="text-[12px] text-ink-3">Counted every second from both recorded runs · drawn as the replay plays</span>
        </div>
        <div className="tnum flex gap-5 text-[13px]">
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: FIXED }} />Fixed-time <b className="font-[650] text-ink">{last ? last.fixed : '—'}</b><span className="text-ink-4">avg {mean('fixed').toFixed(1)}</span></span>
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: FLOW }} />FlowState <b className="font-[650] text-ink">{last?.flow ?? '—'}</b><span className="text-ink-4">avg {mean('flow').toFixed(1)}</span></span>
        </div>
      </div>
      <div className="h-[190px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={shown} margin={{ top: 6, right: 6, bottom: 0, left: -18 }}>
            <defs>
              <linearGradient id="lc-fixed" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={FIXED} stopOpacity={0.22} /><stop offset="1" stopColor={FIXED} stopOpacity={0} /></linearGradient>
              <linearGradient id="lc-flow" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={FLOW} stopOpacity={0.22} /><stop offset="1" stopColor={FLOW} stopOpacity={0} /></linearGradient>
            </defs>
            <CartesianGrid stroke="rgb(12 19 34 / 0.06)" vertical={false} />
            <XAxis dataKey="t" type="number" domain={[win[0], win[1]]} tickFormatter={(v: number) => `${Math.round(v - win[0])}s`} tick={{ fontSize: 11, fill: '#8a93a3' }} axisLine={false} tickLine={false} />
            <YAxis domain={[0, Math.ceil(ymax * 1.1)]} tick={{ fontSize: 11, fill: '#8a93a3' }} axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip formatter={(v, k) => [String(v ?? ''), k === 'fixed' ? 'Fixed-time' : 'FlowState']} labelFormatter={(v) => `${Math.round(Number(v) - win[0])} s`} />
            <Area type="monotone" dataKey="fixed" stroke={FIXED} strokeWidth={2} fill="url(#lc-fixed)" isAnimationActive={false} dot={false} />
            <Area type="monotone" dataKey="flow" stroke={FLOW} strokeWidth={2} fill="url(#lc-flow)" isAnimationActive={false} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function Replay({ data }: { data: Loaded }) {
  const [example, setExample] = useState<number | null>(data.asset.example)
  return <Player key={example} data={data} example={example} setExample={setExample} />
}

function Player({ data, example, setExample }: { data: Loaded; example: number | null; setExample: (id: number) => void }) {
  const { asset, net, scene } = data
  const units = asset.ambulances
  const [mode, setMode] = useState<ViewMode>('junction')
  const [playing, setPlaying] = useState(true)
  const view = useRef<SharedView>({ cx: 300, cy: 300, scale: 0 })
  const win = useMemo<[number, number]>(() => (example != null ? windowFor(asset, example) : [asset.left.frames[0].t, asset.left.frames[asset.left.frames.length - 1].t]), [asset, example])
  const clock = useRef(win[0])
  const [t, setT] = useState(win[0])

  useEffect(() => {
    if (!playing) return
    let raf = 0, prev = performance.now(), shown = 0
    const step = (now: number) => {
      raf = requestAnimationFrame(step)
      const dt = Math.min(0.25, (now - prev) / 1000)
      prev = now
      clock.current = Math.min(win[1], clock.current + dt * SPEED)
      if (now - shown > 100) { shown = now; setT(clock.current) }
      if (clock.current >= win[1]) { setT(win[1]); setPlaying(false) }
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing, win])

  const unit = units.find((u) => u.id === example) ?? null
  const seek = (v: number) => { clock.current = v; setT(v) }
  const restart = () => { seek(win[0]); setPlaying(true) }
  const stops = (s: DemoUnit['left']) => s.stoppedRedS + s.stoppedQueueS
  const tlsIds = useMemo(() => asset.tlsIds ?? net.tls.map((x) => x.id), [asset, net])
  const district = asset.network === 'district'
  const bestCase = !!unit && stops(unit.right) <= asset.bestCaseStopS && unit.left.stoppedQueueS >= asset.minQueueS
  const sides = [
    { name: 'Fixed-time', sub: 'Pre-timed signal plans', side: asset.left, span: unit?.left, evId: unit ? sideId(unit, 'left') : null, label: 'Fixed-time map' },
    { name: 'FlowState', sub: 'Same plans + FlowState rules', side: asset.right, span: unit?.right, evId: unit ? sideId(unit, 'right') : null, label: 'FlowState map' },
  ] as const
  const focusText = asset.focus?.kind === 'junction' ? `Watching the busiest junction: ${asset.focus.label ?? ''}` : asset.focus?.kind === 'point' ? `Watching the ${asset.focus.label ?? 'incident'} site` : null

  return (
    <div className="flex flex-col gap-7">
      <div className="grid gap-x-10 gap-y-12 py-2 min-[860px]:grid-cols-2">
        {sides.map((x) => (
          <div key={x.name} className="flex flex-col items-center gap-6">
            <span className="text-[34px] font-[650] leading-none tracking-[-0.03em] text-ink">{x.name}</span>
            <div className={cn('w-full p-2.5', shell)}>
              <div className="relative h-[380px] overflow-hidden rounded-[22px] bg-[#e9ecef] min-[860px]:h-[490px]">
                <SimMap scene={scene} net={net} side={x.side} subject={x.evId} focus={asset.focus} clock={clock} mode={mode} view={view} onFree={() => setMode('free')} label={x.label} tlsIds={tlsIds} mainCodes={asset.codes === 'main'} />
                {(mode === 'junction' || mode === 'follow') && (
                  <div aria-hidden className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_64%,rgb(255_255_255/0.32)_100%)]" />
                )}
              </div>
            </div>
            <div className="flex flex-col items-center gap-1.5 text-center">
              <span className="text-[13px] leading-none text-ink-3">{x.sub}</span>
              {x.evId != null && x.span && unit ? <UnitTag side={x.side} id={x.evId} t={t} span={x.span} label={unit.label ?? 'Ambulance'} /> : focusText && <div className="text-[13px] leading-none text-ink-2">{focusText}</div>}
            </div>
          </div>
        ))}
      </div>

      <LiveChart asset={asset} t={t} win={win} />

      <div className={cn('flex items-center gap-4 !rounded-full px-4 py-2.5', panel)}>
        <button type="button" onClick={() => (clock.current >= win[1] ? restart() : setPlaying((p) => !p))} aria-label={playing ? 'Pause' : 'Play'} className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ink text-white transition-transform active:scale-95">
          {playing ? <Pause size={16} fill="currentColor" /> : t >= win[1] ? <RotateCcw size={15} /> : <Play size={16} fill="currentColor" className="translate-x-px" />}
        </button>
        <input type="range" aria-label="Replay position" min={win[0]} max={win[1]} step={0.1} value={t} onChange={(e) => seek(Number(e.target.value))} className="min-w-0 flex-1 accent-[#0c1322]" />
        <span className="tnum w-[70px] shrink-0 text-right text-[12px] text-ink-3">{(t - win[0]).toFixed(0)} / {(win[1] - win[0]).toFixed(0)} s</span>
        <button type="button" onClick={() => setMode((m) => (m === 'junction' ? 'network' : 'junction'))} aria-pressed={mode === 'network'}
          className={cn('h-9 shrink-0 rounded-full px-4 text-[13px] font-[550] transition-colors', mode === 'network' ? 'bg-ink text-white' : 'bg-white/70 text-ink-2 hover:bg-white')}>
          {mode === 'network' ? 'Back to intersection' : mode === 'free' ? 'Recentre' : 'Whole map'}
        </button>
      </div>

      {unit && (
        <div className="grid grid-cols-2 gap-5">
          <Kpi label={`${unit.label ?? 'This unit'} · trip`} left={unit.left.travelS} right={unit.right.travelS} />
          <Kpi label={`${unit.label ?? 'This unit'} · stopped`} left={stops(unit.left)} right={stops(unit.right)} />
        </div>
      )}

      {units.length > 0 && (
        <div className="flex flex-col gap-4 pt-4">
          <h3 className="m-0 text-[24px] font-[650] tracking-[-0.025em]">Every emergency unit in this run</h3>
          <Units units={units} example={example} onPick={setExample} />
        </div>
      )}

      <div className="flex flex-col gap-4 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="m-0 text-[24px] font-[650] tracking-[-0.025em]">Across {asset.aggregate.episodes} paired runs</h3>
          <span className="text-[12px] text-ink-3">Green: clearly better · red: clearly worse · gray: no clear difference</span>
        </div>
        <Aggregate asset={asset} />
      </div>

      <details className={cn('group px-6 py-4', panel)}>
        <summary className="cursor-pointer list-none text-[14px] font-[550] text-ink-2 marker:hidden [&::-webkit-details-marker]:hidden">How to read this</summary>
        <ul className="m-0 mt-3 flex list-disc flex-col gap-1.5 pl-5 text-[13px] leading-[1.5] text-ink-3">
          <li>{district ? 'The same downtown street network and map renderer as the Simulation page (OpenStreetMap geometry).' : 'A synthetic 2×2 grid; buildings and trees are generated decoration.'} Both sides replay identical demand and the same events (seed {asset.seed}); only the signal control differs. Vehicles are drawn between recorded half-second positions along their recorded heading, so the picture is a faithful replay, not a new simulation.</li>
          <li>Left: fixed-time plans. Right: {asset.rightIncludes}.</li>
          {units.length > 0 && <li>Emergency units obey the lights and cannot pass queued cars in both runs. Trip time is the call-to-scene leg. {bestCase ? `The unit shown first sat in a queue for at least ${asset.minQueueS} s on the fixed-time side and was held at most ${asset.bestCaseStopS} s on the right, so it is a best case` : 'The unit shown first is the one that gained the most in this run, so it is a best case'}; every unit in the run is listed above.</li>}
          <li>Averages come from {asset.aggregate.episodes} paired runs (seeds {asset.aggregate.seeds[0]}–{asset.aggregate.seeds[asset.aggregate.seeds.length - 1]}), 5 minutes each. A small sample: read the intervals (hover a row) before the percentages. Worse-run counts are shown for every measure.</li>
          <li>This replay is a separate experiment from the evaluation below (different network and controller variant).</li>
        </ul>
      </details>
    </div>
  )
}
