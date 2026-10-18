import { Minus, Pause, Play, Plus, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Camera } from '../map/camera'
import { canopyCache, groundCache, render, type RenderState } from '../map/renderer'
import { buildScene, type Scene } from '../map/scene'
import {
  LOWER_IS_BETTER, STATUS_TEXT, evState, loadDemo, locate, sample, spriteCode, windowFor,
  type AggStat, type DemoAsset, type DemoSide, type DemoUnit, type UnitSide,
} from '../lib/flowstateDemo'
import { cn, num } from '../lib/format'
import type { NetworkGeom } from '../lib/types'
import { BASE, COLOR, FS, card, signed } from '../lib/resultsTheme'
import { Cap, Eyebrow, H2, Pill, Swatch, Td, Th } from './resultsUi'

type Loaded = { asset: DemoAsset; net: NetworkGeom; scene: Scene }
const SPEEDS = [1, 2, 4]
const LEFT_COLOR = COLOR[BASE]
const RIGHT_COLOR = COLOR[FS]

const secs = (v: number | null | undefined) => (v == null || !isFinite(v) ? '—' : `${v.toFixed(v < 10 ? 1 : 0)} s`)

// ---- one map ---------------------------------------------------------------
interface Swap { canvas: HTMLCanvasElement | null; view: Camera; key: string; builds: number }
const swapIn = (target: Swap, mine: Swap) => { Object.assign(target, mine) }
const swapOut = (target: Swap, mine: Swap) => { Object.assign(mine, target) }

/** Camera shared by both maps so they always show the same place and zoom. 'follow' keeps each map on its own ambulance; dragging or zooming switches to 'free'. */
export type ViewMode = 'follow' | 'network' | 'free'
export interface SharedView { cx: number; cy: number; scale: number }
const MIN_ZOOM = 0.5
const MAX_ZOOM = 40

interface MapProps {
  scene: Scene
  net: NetworkGeom
  side: DemoSide
  ambulance: number | null
  clock: React.RefObject<number>
  mode: ViewMode
  view: React.RefObject<SharedView>
  onFree: () => void
  label: string
}

/** Main's own map renderer (roads, curbs, crosswalks, signal heads, buildings, trees, vehicle sprites) drawing one recorded run. Drag to move, scroll to zoom. */
function SimMap({ scene, net, side, ambulance, clock, mode, view, onFree, label }: MapProps) {
  const ref = useRef<HTMLCanvasElement>(null)
  const modeRef = useRef(mode)
  useEffect(() => { modeRef.current = mode }, [mode])
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
    const tlsIds = net.tls.map((x) => x.id)
    const [rx0, ry0, rx1, ry1] = scene.roadBounds
    let raf = 0, visible = true, held: [number, number] | null = null
    let drag: { x: number; y: number; cx: number; cy: number } | null = null
    const io = new IntersectionObserver((e) => { visible = e.some((x) => x.isIntersecting) })
    io.observe(cv)

    const ambulanceAt = (placed: ReturnType<typeof sample>) => {
      const me = ambulance != null ? placed.find((p) => p.id === ambulance) : null
      if (me) held = [me.x, me.y]
      else if (!held && ambulance != null) {
        const r = side.frames.find((fr) => fr.v.some((q) => q[0] === ambulance))?.v.find((q) => q[0] === ambulance)
        if (r) held = [r[1] / 10, r[2] / 10]
      }
      return me ?? null
    }

    const frame = () => {
      raf = requestAnimationFrame(frame)
      const dpr = Math.min(2, window.devicePixelRatio || 1), w = cv.clientWidth, h = cv.clientHeight
      if (w < 10 || h < 10 || !visible) return
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr) }
      const t = clock.current
      const placed = sample(side.frames, t)
      const me = ambulanceAt(placed)
      const jumped = Math.abs(t - lastT.current) > 2
      lastT.current = t
      cam.setViewport(w, h)
      const v = view.current, m = modeRef.current
      if (m === 'network') {
        v.scale = Math.min(w / (rx1 - rx0 + 60), h / (ry1 - ry0 + 60))
        v.cx = (rx0 + rx1) / 2; v.cy = (ry0 + ry1) / 2
      } else if (m === 'follow') {
        const [hx, hy] = held ?? [(rx0 + rx1) / 2, (ry0 + ry1) / 2]
        if (!v.scale || v.scale < 3) v.scale = Math.min(w, h) / 120
        v.cx = jumped ? hx : v.cx + (hx - v.cx) * 0.25
        v.cy = jumped ? hy : v.cy + (hy - v.cy) * 0.25
      }
      cam.jump(v.cx, v.cy, v.scale)
      const frameState = side.frames[Math.min(side.frames.length - 1, Math.max(0, Math.floor(t - side.frames[0].t)))]
      const st: RenderState = {
        vehicles: placed.map((p) => ({
          id: String(p.id), code: spriteCode(p), x: p.x, y: p.y, angle: p.angle, speed: p.speed,
          signals: (p.speed < 0.5 ? 8 : 0) | (p.type === 2 ? 2048 : 0), // brake lamps when nearly stopped; the beacon bit for the ambulance
          alpha: 1,
        })),
        persons: [], tls: Object.fromEntries(tlsIds.map((id, i) => [id, frameState.s[i] ?? ''])),
        layers: { buildings: true, trees: true, labels: false, pedestrians: false, flow: false, signals: true },
        selected: null, selectedJunction: null, hoverEdge: null, pickedEdge: null, tracked: new Map(), incidents: [], edges: {}, route: null, busLines: [],
        now: performance.now(), pick: false, overlays: [], t: 0, safe: { x0: 0, y0: 0, x1: w, y1: h },
      }
      swapIn(groundCache, mine.ground); swapIn(canopyCache, mine.canopy) // each map keeps its own cached bitmaps
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, cv.width, cv.height)
      render(ctx, scene, cam, st, dpr)
      swapOut(groundCache, mine.ground); swapOut(canopyCache, mine.canopy)
      if (me) { // ring the ambulance so it can be found in traffic
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
  }, [scene, net, side, ambulance, clock, view])

  return <canvas ref={ref} role="img" aria-label={`${label}. Drag to move, scroll to zoom.`} className="block h-full w-full cursor-grab touch-none" />
}

// ---- measured numbers ------------------------------------------------------
function AmbulanceTag({ side, id, t, span }: { side: DemoSide; id: number; t: number; span: UnitSide }) {
  const e = evState(side.frames, id, t)
  const here = locate(side.frames, id, t)
  const stuck = here && e && (e.status === 1 || e.status === 2)
  const text = here
    ? e ? `Ambulance ${STATUS_TEXT[e.status] ?? 'moving'}${e.metresToSignal > 0 && e.status !== 0 ? ` · ${Math.round(e.metresToSignal)} m to signal` : ''}` : 'Ambulance moving'
    : span.last != null && t > span.last ? `Ambulance arrived · ${secs(span.travelS)} trip` : 'Ambulance not yet on the network'
  return (
    <div className="flex items-center gap-2 text-[12px] font-[550] leading-none text-ink">
      <span className={cn('inline-block h-2 w-2 shrink-0 rounded-full', stuck ? 'bg-[#ff3b30]' : here ? 'bg-[#34c759]' : 'bg-ink-4')} />
      <span className="tnum truncate">{text}</span>
    </div>
  )
}

function Kpi({ label, left, right, unit = 's', digits = 0, lowerIsBetter = true }: { label: string; left: number; right: number; unit?: string; digits?: number; lowerIsBetter?: boolean }) {
  const good = lowerIsBetter ? right < left : right > left
  const same = Math.abs(right - left) < 0.05
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-[12px] font-[550] text-ink-3">{label}</span>
      <div className="tnum flex flex-wrap items-baseline gap-x-2">
        <span className={cn('text-[30px] font-[650] leading-none tracking-[-0.03em]', same ? 'text-ink' : good ? 'text-good' : 'text-bad')}>{num(right, digits)}{unit}</span>
        <span className="text-[13px] text-ink-4">from {num(left, digits)}{unit}</span>
      </div>
    </div>
  )
}

const AGG_LABEL: Record<string, string> = {
  ev_travel_mean_s: 'Ambulance travel time',
  ev_stopped_red_s: 'Ambulance stopped at red',
  ev_stopped_queue_s: 'Ambulance stuck in queue',
  mean_wait_civilian_s: 'Other vehicles’ wait',
  mean_travel_s: 'Other vehicles’ travel time',
  queue_total_mean_veh: 'Vehicles queued (network mean)',
  throughput_vph: 'Throughput (veh/h)',
}

function Aggregate({ asset }: { asset: DemoAsset }) {
  const rows = Object.entries(asset.aggregate.metrics)
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr><Th left>Measure</Th><Th>Fixed-time</Th><Th>FlowState</Th><Th>Change</Th><Th>95% interval of change</Th><Th>Better / worse runs</Th></tr>
        </thead>
        <tbody>
          {rows.map(([k, s]: [string, AggStat], i) => {
            const lower = LOWER_IS_BETTER.has(k)
            const good = lower ? s.mean_diff < 0 : s.mean_diff > 0 // mean_diff is FlowState − fixed-time
            const last = i === rows.length - 1
            return (
              <tr key={k}>
                <Td left last={last}>{AGG_LABEL[k] ?? k}</Td>
                <Td last={last} className="tnum">{num(s.left_mean, 1)}</Td>
                <Td last={last} className="tnum">{num(s.right_mean, 1)}</Td>
                <Td last={last} className={cn('tnum', s.pct != null && (s.ci95[0] > 0 || s.ci95[1] < 0) ? (good ? 'font-[550] text-good' : 'text-bad') : 'text-ink-3')}>{s.pct == null ? '—' : signed(s.pct)}</Td>
                <Td last={last} className="tnum text-ink-3">{num(s.ci95[0], 1)} to {num(s.ci95[1], 1)}</Td>
                <Td last={last} className="tnum text-ink-3">{s.better} / {s.worse}</Td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Units({ asset, example, onPick }: { asset: DemoAsset; example: number | null; onPick: (id: number) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr><Th left>Ambulance</Th><Th>Fixed-time</Th><Th>FlowState</Th><Th>Time saved</Th><Th>Stopped (fixed → FlowState)</Th><Th>{''}</Th></tr>
        </thead>
        <tbody>
          {asset.ambulances.map((u: DemoUnit, i) => {
            const last = i === asset.ambulances.length - 1
            const stops = (s: DemoUnit['left']) => s.stoppedRedS + s.stoppedQueueS
            return (
              <tr key={u.id} className={cn(u.id === example && 'bg-flow-soft')}>
                <Td left last={last}>#{i + 1}{u.id === example ? ' · shown above' : ''}</Td>
                <Td last={last} className="tnum">{secs(u.left.travelS)}</Td>
                <Td last={last} className="tnum">{secs(u.right.travelS)}</Td>
                <Td last={last} className={cn('tnum', u.savedS > 0 ? 'font-[550] text-good' : u.savedS < 0 ? 'text-bad' : 'text-ink-3')}>{u.savedS === 0 ? 'same' : `${Math.abs(u.savedS).toFixed(0)} s ${u.savedS > 0 ? 'faster' : 'slower'}`}</Td>
                <Td last={last} className="tnum text-ink-3">{secs(stops(u.left))} → {secs(stops(u.right))}</Td>
                <Td last={last}>
                  <button type="button" onClick={() => onPick(u.id)} disabled={u.id === example} className="rounded-md px-2 py-1 text-[12px] font-[550] text-accent hover:bg-accent-soft disabled:cursor-default disabled:text-ink-disabled disabled:hover:bg-transparent">Watch</button>
                </Td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ---- section ---------------------------------------------------------------
export function SideBySide() {
  const [data, setData] = useState<Loaded | 'loading' | { error: string }>('loading')
  useEffect(() => {
    let live = true
    loadDemo().then(
      ({ asset, net }) => { if (live) setData({ asset, net, scene: buildScene(net) }) },
      (e: unknown) => { if (live) setData({ error: e instanceof Error ? e.message : String(e) }) },
    )
    return () => { live = false }
  }, [])

  if (data === 'loading') return <section className={cn(card, 'flex h-[420px] items-center justify-center text-ink-3')}>Loading the recorded run…</section>
  if ('error' in data) {
    return (
      <section className={cn(card, 'flex flex-col gap-1 p-6')}>
        <H2>Side-by-side replay unavailable</H2>
        <Cap>{data.error}</Cap>
      </section>
    )
  }
  return <Replay data={data} />
}

/** Choosing another ambulance remounts the player, which restarts the replay at that ambulance's window. */
const glass = 'bg-white/80 shadow-[0_1px_2px_rgb(12_19_34/0.08),0_0_0_1px_rgb(12_19_34/0.06)] backdrop-blur-xl'

function Segmented<T extends string | number>({ value, onChange, options, label }: { value: T | null; onChange: (v: T) => void; options: { v: T; label: string }[]; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-full bg-muted p-0.5">
      {options.map((o) => (
        <button key={o.v} type="button" aria-pressed={value === o.v} onClick={() => onChange(o.v)}
          className={cn('h-7 rounded-full px-3 text-[12px] font-[550] transition-colors', value === o.v ? 'bg-white text-ink shadow-thumb' : 'text-ink-3 hover:text-ink')}>{o.label}</button>
      ))}
    </div>
  )
}

function Replay({ data }: { data: Loaded }) {
  const [example, setExample] = useState<number | null>(data.asset.example)
  return <Player key={example} data={data} example={example} setExample={setExample} />
}

function Player({ data, example, setExample }: { data: Loaded; example: number | null; setExample: (id: number) => void }) {
  const { asset, net, scene } = data
  const [mode, setMode] = useState<ViewMode>('follow')
  const [speed, setSpeed] = useState(1)
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
      clock.current = Math.min(win[1], clock.current + dt * speed)
      if (now - shown > 100) { shown = now; setT(clock.current) }
      if (clock.current >= win[1]) { setT(win[1]); setPlaying(false) }
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, win])

  const unit = asset.ambulances.find((u) => u.id === example) ?? null
  const seek = (v: number) => { clock.current = v; setT(v) }
  const restart = () => { seek(win[0]); setPlaying(true) }
  const zoom = (k: number) => { setMode('free'); view.current.scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.current.scale * k)) }
  const stops = (s: DemoUnit['left']) => s.stoppedRedS + s.stoppedQueueS
  const tot = asset.totals
  const avg = asset.aggregate.metrics.ev_travel_mean_s
  const sides = [
    { name: 'Fixed-time signals', sub: 'Same plan at every junction', color: LEFT_COLOR, side: asset.left, span: unit?.left, label: 'Fixed-time map' },
    { name: 'FlowState', sub: 'Adaptive · forecast · green corridor', color: RIGHT_COLOR, side: asset.right, span: unit?.right, label: 'FlowState map' },
  ] as const

  return (
    <section className="flex flex-col overflow-hidden rounded-[24px] border border-transparent bg-surface shadow-hero">
      <div className="flex flex-col gap-3 px-8 pb-6 pt-8">
        <Eyebrow>Side by side · same traffic · recorded SUMO run</Eyebrow>
        <h2 className="m-0 text-balance text-[30px] font-[650] leading-[1.15] tracking-[-0.025em] text-ink">A queue the ambulance can’t get out of, and one it never meets.</h2>
        <p className="m-0 max-w-[760px] text-pretty text-[15px] leading-[1.5] text-ink-2">
          {asset.scenarioTitle} traffic on a synthetic 2×2 grid, replayed identically on both sides. On the left the ambulance is boxed in by queued cars at fixed-time signals. On the right FlowState clears the way ahead of it.
          In both runs the ambulance obeys the lights and cannot pass cars. Drag either map to move, scroll to zoom.
        </p>
      </div>

      <div className="grid gap-3 px-4 min-[860px]:grid-cols-2 min-[860px]:px-6">
        {sides.map((x) => (
          <div key={x.name} className="relative h-[380px] overflow-hidden rounded-[18px] bg-[#e9ecef] shadow-[0_0_0_1px_rgb(12_19_34/0.08)] min-[860px]:h-[460px]">
            <SimMap scene={scene} net={net} side={x.side} ambulance={example} clock={clock} mode={mode} view={view} onFree={() => setMode('free')} label={x.label} />
            <div className={cn('pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded-full py-1.5 pl-3 pr-4', glass)}>
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: x.color }} />
              <span className="text-[13px] font-semibold leading-none text-ink">{x.name}</span>
              <span className="hidden text-[12px] leading-none text-ink-3 min-[1000px]:inline">{x.sub}</span>
            </div>
            {example != null && x.span && (
              <div className={cn('pointer-events-none absolute bottom-3 left-3 max-w-[calc(100%-24px)] rounded-full px-3.5 py-1.5', glass)}>
                <AmbulanceTag side={x.side} id={example} t={t} span={x.span} />
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-6 py-4">
        <button type="button" onClick={() => (clock.current >= win[1] ? restart() : setPlaying((p) => !p))} aria-label={playing ? 'Pause' : 'Play'} className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ink text-white transition-transform active:scale-95">
          {playing ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" className="translate-x-px" />}
        </button>
        <button type="button" onClick={restart} aria-label="Restart" className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted text-ink-2 transition-colors hover:bg-track"><RotateCcw size={15} /></button>
        <input type="range" aria-label="Replay position" min={win[0]} max={win[1]} step={0.1} value={t} onChange={(e) => seek(Number(e.target.value))} className="min-w-[160px] flex-1 accent-[#0c1322]" />
        <span className="tnum w-[84px] text-right text-[12px] text-ink-3">{(t - win[0]).toFixed(0)} / {(win[1] - win[0]).toFixed(0)} s</span>
        <Segmented label="Speed" value={speed} onChange={setSpeed} options={SPEEDS.map((s) => ({ v: s, label: `${s}×` }))} />
        <Segmented label="Camera" value={mode} onChange={setMode} options={[{ v: 'follow' as ViewMode, label: 'Follow' }, { v: 'network' as ViewMode, label: 'Network' }, { v: 'free' as ViewMode, label: 'Free' }]} />
        <div className="inline-flex rounded-full bg-muted p-0.5">
          <button type="button" aria-label="Zoom out" onClick={() => zoom(1 / 1.5)} className="inline-flex h-7 w-8 items-center justify-center rounded-full text-ink-2 hover:bg-white hover:shadow-thumb"><Minus size={14} /></button>
          <button type="button" aria-label="Zoom in" onClick={() => zoom(1.5)} className="inline-flex h-7 w-8 items-center justify-center rounded-full text-ink-2 hover:bg-white hover:shadow-thumb"><Plus size={14} /></button>
        </div>
      </div>

      {unit && (
        <div className="grid grid-cols-2 gap-x-8 gap-y-5 border-t border-muted px-8 py-6 min-[860px]:grid-cols-4">
          <Kpi label="This ambulance · trip" left={unit.left.travelS} right={unit.right.travelS} />
          <Kpi label="This ambulance · stopped" left={stops(unit.left)} right={stops(unit.right)} />
          <Kpi label="All ambulances · mean trip" left={tot.left.ev_travel_mean_s} right={tot.right.ev_travel_mean_s} />
          <Kpi label="Other vehicles · mean wait" left={tot.left.mean_wait_civilian_s} right={tot.right.mean_wait_civilian_s} />
        </div>
      )}

      <div className="flex flex-col gap-3 border-t border-muted px-8 py-6">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="m-0 text-[17px] font-semibold tracking-[-0.01em]">All {asset.ambulances.length} ambulances in this run</h3>
          <Pill tone="live">choose one to watch</Pill>
        </div>
        <Units asset={asset} example={example} onPick={setExample} />
        <Cap>
          Shown first: the ambulance that sat in a queue for at least {asset.minQueueS} s on the fixed-time side and that FlowState held for at most {asset.bestCaseStopS} s, so it is a best case, not an average. The table lists every ambulance
          in the run, including any that gained little.
        </Cap>
      </div>

      <div className="flex flex-col gap-3 border-t border-muted px-8 py-6">
        <h3 className="m-0 text-[17px] font-semibold tracking-[-0.01em]">The average, not just one run</h3>
        <Cap>{asset.aggregate.episodes} held-out paired runs, seeds {asset.aggregate.seeds[0]}–{asset.aggregate.seeds[asset.aggregate.seeds.length - 1]}.</Cap>
        <Aggregate asset={asset} />
        <Cap>
          Ambulances get through faster on average ({secs(avg?.left_mean)} → {secs(avg?.right_mean)}), but there is a cost: the corridor holds side streets red, so more vehicles are queued across the network and other vehicles’ wait and
          throughput show no clear gain (intervals include zero). “Better / worse runs” counts the paired runs FlowState won or lost on that measure. Only the queue forecaster is a trained model; the controller and the corridor are hand-written rules.
        </Cap>
        <p className="m-0 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] leading-[1.5] text-ink-3">
          <span className="flex items-center gap-1.5"><Swatch c={BASE} /> Fixed-time</span>
          <span className="flex items-center gap-1.5"><Swatch c={FS} /> FlowState</span>
          <span>Roads, curbs, crosswalks and vehicles are drawn by the same map renderer as the live view. Buildings and trees are generated decoration on a synthetic grid (not a real city). This replay and its {asset.aggregate.episodes} runs are a separate experiment from the evaluation below: a different controller variant (with the ambulance corridor) on the synthetic grid.</span>
        </p>
      </div>
    </section>
  )
}
