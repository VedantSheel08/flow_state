import { Pause, Play, RotateCcw } from 'lucide-react'
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
type ViewMode = 'follow' | 'network'
const SPEEDS = [1, 2, 4]
const LEFT_COLOR = COLOR[BASE]
const RIGHT_COLOR = COLOR[FS]

const secs = (v: number | null | undefined) => (v == null || !isFinite(v) ? '—' : `${v.toFixed(v < 10 ? 1 : 0)} s`)

// ---- one map ---------------------------------------------------------------
interface Swap { canvas: HTMLCanvasElement | null; view: Camera; key: string; builds: number }
const swapIn = (target: Swap, mine: Swap) => { Object.assign(target, mine) }
const swapOut = (target: Swap, mine: Swap) => { Object.assign(mine, target) }

interface MapProps {
  scene: Scene
  net: NetworkGeom
  side: DemoSide
  ambulance: number | null
  clock: React.RefObject<number>
  mode: ViewMode
  label: string
}

/** Main's own map renderer (roads, curbs, crosswalks, signal heads, buildings, trees, vehicle sprites) drawing one recorded run. */
function SimMap({ scene, net, side, ambulance, clock, mode, label }: MapProps) {
  const ref = useRef<HTMLCanvasElement>(null)
  const modeRef = useRef(mode)
  useEffect(() => { modeRef.current = mode }, [mode])
  const lastT = useRef(-1)

  useEffect(() => {
    const cv = ref.current!
    const ctx = cv.getContext('2d')!
    const cam = new Camera()
    cam.minScale = 0.3
    const mine = { ground: { canvas: null, view: new Camera(), key: '', builds: 0 } as Swap, canopy: { canvas: null, view: new Camera(), key: '', builds: 0 } as Swap }
    const tlsIds = net.tls.map((x) => x.id)
    const [rx0, ry0, rx1, ry1] = scene.roadBounds
    let raf = 0, visible = true, held: [number, number] | null = null
    const io = new IntersectionObserver((e) => { visible = e.some((x) => x.isIntersecting) })
    io.observe(cv)

    const frame = () => {
      raf = requestAnimationFrame(frame)
      const dpr = Math.min(2, window.devicePixelRatio || 1), w = cv.clientWidth, h = cv.clientHeight
      if (w < 10 || h < 10 || !visible) return
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr) }
      const t = clock.current
      const placed = sample(side.frames, t)
      const jumped = Math.abs(t - lastT.current) > 2
      lastT.current = t
      cam.setViewport(w, h)
      if (modeRef.current === 'network') {
        const s = Math.min(w / (rx1 - rx0 + 60), h / (ry1 - ry0 + 60))
        cam.jump((rx0 + rx1) / 2, (ry0 + ry1) / 2, s)
      } else {
        const me = ambulance != null ? placed.find((p) => p.id === ambulance) : null
        if (me) held = [me.x, me.y]
        else if (!held && ambulance != null) {
          const f = side.frames.find((fr) => fr.v.some((r) => r[0] === ambulance))
          const r = f?.v.find((q) => q[0] === ambulance)
          if (r) held = [r[1] / 10, r[2] / 10]
        }
        const [hx, hy] = held ?? [(rx0 + rx1) / 2, (ry0 + ry1) / 2]
        const s = Math.min(w, h) / 120
        if (jumped || cam.scale !== s) cam.jump(hx, hy, s)
        else cam.jump(cam.x + (hx - cam.x) * 0.25, cam.y + (hy - cam.y) * 0.25, s)
      }
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
      // ring the ambulance so it can be followed in traffic
      const me = ambulance != null ? placed.find((p) => p.id === ambulance) : null
      if (me) {
        const [sx, sy] = cam.toScreen(me.x, me.y)
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        ctx.beginPath(); ctx.arc(sx, sy, Math.max(12, 7 * cam.scale), 0, Math.PI * 2)
        ctx.strokeStyle = '#d92d20'; ctx.lineWidth = 2; ctx.stroke()
      }
    }
    raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); io.disconnect() }
  }, [scene, net, side, ambulance, clock])

  return <canvas ref={ref} role="img" aria-label={label} className="block h-full w-full" />
}

// ---- measured numbers ------------------------------------------------------
function AmbulanceTag({ side, id, t, color, name, span }: { side: DemoSide; id: number; t: number; color: string; name: string; span: UnitSide }) {
  const e = evState(side.frames, id, t)
  const here = locate(side.frames, id, t)
  const text = here
    ? e ? `ambulance ${STATUS_TEXT[e.status] ?? 'moving'}${e.metresToSignal > 0 && e.status !== 0 ? ` · ${Math.round(e.metresToSignal)} m to signal` : ''}` : 'ambulance moving'
    : span.last != null && t > span.last ? `ambulance arrived · ${secs(span.travelS)} trip` : 'ambulance not yet on the network'
  return (
    <div className="flex min-h-[22px] flex-wrap items-center gap-2 text-[12px] text-ink-2">
      <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
      <span className="font-semibold text-ink">{name}</span>
      <span className="tnum">{text}</span>
    </div>
  )
}

function Kpi({ label, left, right, unit = 's', digits = 0, lowerIsBetter = true }: { label: string; left: number; right: number; unit?: string; digits?: number; lowerIsBetter?: boolean }) {
  const good = lowerIsBetter ? right < left : right > left
  const same = Math.abs(right - left) < 0.05
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Eyebrow>{label}</Eyebrow>
      <div className="tnum flex flex-wrap items-baseline gap-x-2 text-[13px]">
        <span className="text-ink-3">{num(left, digits)}{unit}</span>
        <span className="text-ink-4">→</span>
        <span className={cn('text-[20px] font-[650] tracking-[-0.02em]', same ? 'text-ink' : good ? 'text-good' : 'text-bad')}>{num(right, digits)}{unit}</span>
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
function Replay({ data }: { data: Loaded }) {
  const [example, setExample] = useState<number | null>(data.asset.example)
  return <Player key={example} data={data} example={example} setExample={setExample} />
}

function Player({ data, example, setExample }: { data: Loaded; example: number | null; setExample: (id: number) => void }) {
  const { asset, net, scene } = data
  const [mode, setMode] = useState<ViewMode>('follow')
  const [speed, setSpeed] = useState(1)
  const [playing, setPlaying] = useState(true)
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
  const stops = (s: DemoUnit['left']) => s.stoppedRedS + s.stoppedQueueS
  const tot = asset.totals
  const worseRuns = asset.aggregate.metrics.ev_travel_mean_s
  const sideLabel = (name: string, color: string, sub: string) => (
    <div className="flex flex-col gap-0.5 px-4 pb-3 pt-3.5">
      <div className="flex items-center gap-2 text-[15px] font-semibold"><span className="inline-block h-2 w-2 rounded-[2px]" style={{ background: color }} />{name}</div>
      <Cap className="text-[12px]">{sub}</Cap>
    </div>
  )

  return (
    <section className={cn(card, 'flex flex-col overflow-hidden')}>
      <div className="flex flex-col gap-2 border-b border-muted px-6 pb-4 pt-6">
        <Eyebrow>Side by side · same traffic, same seed · recorded SUMO run</Eyebrow>
        <H2>An ambulance crosses the grid: fixed-time signals vs FlowState</H2>
        <Cap>
          {asset.scenarioTitle} traffic on a synthetic 2×2 signalised grid. Both maps replay the identical demand; only the signal control differs. Left: the plain 30 s fixed-time plan, ambulance stuck in the queue.
          Right: FlowState, {asset.rightIncludes}.
        </Cap>
      </div>

      <div className="grid min-[860px]:grid-cols-2 min-[860px]:divide-x min-[860px]:divide-muted">
        {([['Fixed-time signals', LEFT_COLOR, 'Same plan at every junction, blind to the ambulance', asset.left, 'Fixed-time map'], ['FlowState', RIGHT_COLOR, 'Adaptive signals · queue forecast · green corridor for the ambulance', asset.right, 'FlowState map']] as const).map(([name, color, sub, side, label]) => (
          <div key={name} className="flex min-w-0 flex-col">
            {sideLabel(name, color, sub)}
            <div className="relative h-[360px] bg-[#e9ecef] min-[860px]:h-[440px]">
              <SimMap scene={scene} net={net} side={side} ambulance={example} clock={clock} mode={mode} label={label} />
            </div>
            <div className="px-4 py-2.5">
              {example != null && unit && <AmbulanceTag side={side} id={example} t={t} color={color} name={name} span={side === asset.left ? unit.left : unit.right} />}
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-muted px-6 py-3">
        <button type="button" onClick={() => (clock.current >= win[1] ? restart() : setPlaying((p) => !p))} aria-label={playing ? 'Pause' : 'Play'} className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-ink text-white">
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button type="button" onClick={restart} aria-label="Restart" className="inline-flex h-8 w-8 items-center justify-center rounded-full text-ink-2 shadow-[inset_0_0_0_1px_var(--color-border-strong)] hover:bg-muted"><RotateCcw size={14} /></button>
        <input type="range" aria-label="Replay position" min={win[0]} max={win[1]} step={0.1} value={t} onChange={(e) => seek(Number(e.target.value))} className="min-w-[160px] flex-1 accent-[#2563eb]" />
        <span className="tnum w-[88px] text-right text-[12px] text-ink-3">{(t - win[0]).toFixed(0)} / {(win[1] - win[0]).toFixed(0)} s</span>
        <div role="group" aria-label="Speed" className="flex gap-1">
          {SPEEDS.map((s) => (
            <button key={s} type="button" onClick={() => setSpeed(s)} aria-pressed={speed === s} className={cn('h-7 rounded-md px-2 text-[12px] font-[550]', speed === s ? 'bg-ink text-white' : 'text-ink-2 hover:bg-muted')}>{s}×</button>
          ))}
        </div>
        <div role="group" aria-label="Camera" className="flex gap-1">
          {(['follow', 'network'] as const).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)} aria-pressed={mode === m} className={cn('h-7 rounded-md px-2 text-[12px] font-[550]', mode === m ? 'bg-accent-soft text-accent-strong' : 'text-ink-2 hover:bg-muted')}>{m === 'follow' ? 'Follow ambulance' : 'Whole network'}</button>
          ))}
        </div>
      </div>

      {unit && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 border-t border-muted px-6 py-5 min-[860px]:grid-cols-4">
          <Kpi label="This ambulance · travel" left={unit.left.travelS} right={unit.right.travelS} />
          <Kpi label="This ambulance · stopped" left={stops(unit.left)} right={stops(unit.right)} />
          <Kpi label="All ambulances · mean travel" left={tot.left.ev_travel_mean_s} right={tot.right.ev_travel_mean_s} />
          <Kpi label="Other vehicles · mean wait" left={tot.left.mean_wait_civilian_s} right={tot.right.mean_wait_civilian_s} />
        </div>
      )}

      <div className="flex flex-col gap-3 border-t border-muted px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <Eyebrow>All {asset.ambulances.length} ambulances in this run</Eyebrow>
          <Pill tone="live">choose one to watch</Pill>
        </div>
        <Units asset={asset} example={example} onPick={setExample} />
        <Cap>
          The ambulance shown first is the one FlowState held for the least time among those it sped up (stopped ≤ {asset.bestCaseStopS} s), so it is a best case, not an average: the table lists every ambulance in the run,
          including any that did not improve.
        </Cap>
      </div>

      <div className="flex flex-col gap-3 border-t border-muted px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <Eyebrow>The average, not just one run · {asset.aggregate.episodes} held-out paired runs, seeds {asset.aggregate.seeds[0]}–{asset.aggregate.seeds[asset.aggregate.seeds.length - 1]}</Eyebrow>
        </div>
        <Aggregate asset={asset} />
        <Cap>
          Ambulances get through faster on average ({secs(worseRuns?.left_mean)} → {secs(worseRuns?.right_mean)}), but there is a cost: the corridor holds side streets red, so more vehicles are queued across the network
          and other vehicles’ wait and throughput show no clear gain (intervals include zero). “Better / worse runs” counts the paired runs FlowState won or lost on that measure. Only the queue forecaster is a trained model;
          the controller and the corridor are hand-written rules.
        </Cap>
        <div className="flex flex-wrap items-center gap-4 text-[12px] text-ink-3">
          <span className="flex items-center gap-1.5"><Swatch c={BASE} /> Fixed-time</span>
          <span className="flex items-center gap-1.5"><Swatch c={FS} /> FlowState</span>
          <span>Roads, curbs, crosswalks and vehicles are drawn by the same map renderer as the live view. Buildings and trees are generated decoration on a synthetic grid (not a real city). This replay and its {asset.aggregate.episodes} runs are a separate experiment from the evaluation below: a different controller variant (with the ambulance corridor) on the synthetic grid.</span>
        </div>
      </div>
    </section>
  )
}
