import { ArrowDownRight, ArrowUpRight, FlaskConical, Loader2, Minus } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { PageMasthead } from '../components/PageHeader'
import { Badge, Button, Empty, Segmented } from '../components/ui'
import { SevenSegment, SignalLamp, SlidingNumber } from '../components/ui/traffic'
import { api } from '../lib/api'
import { clock, cn, num, secs } from '../lib/format'
import { useStore } from '../lib/store'
import type { CompareJob, Kpi, ModeSummary, RunResults } from '../lib/types'

// One run = one series, drawn in ink. Blue and orange stay reserved for
// FlowState and fixed-time on the Results page.
const LINE = '#0c1322'
// A/B variants of a controlled comparison: baseline grey, variant ink.
const SERIES = '#8a93a3'
const SERIES_B = '#0c1322'
const TIP_STYLE = { fontSize: 12, borderRadius: 10, border: 'none', background: '#0c1322', color: '#fff', boxShadow: '0 8px 24px -6px rgb(12 19 34 / .35)', padding: '8px 10px' }

interface ChartDef {
  key: string
  title: string
  unit: string
  get: (k: Kpi) => number | null
  note: string
}

const CHARTS: ChartDef[] = [
  { key: 'active', title: 'Vehicles in network', unit: 'veh', get: (k) => k.active, note: 'Motor vehicles and cyclists currently simulated' },
  { key: 'speed', title: 'Mean speed', unit: 'km/h', get: (k) => k.meanSpeed, note: 'Space-mean speed of motor vehicles' },
  { key: 'delay', title: 'Delay per trip', unit: 's', get: (k) => k.timeLoss, note: 'SUMO time loss, trips completed in the last 5 min' },
  { key: 'tt', title: 'Travel time', unit: 's', get: (k) => k.travelTime, note: 'Mean trip duration, last 5 min' },
  { key: 'thr', title: 'Throughput', unit: 'veh/h', get: (k) => k.throughput, note: 'Completed trips per hour, last 5 min' },
  { key: 'queue', title: 'Stopped at approaches', unit: 'veh', get: (k) => k.queued, note: 'Halting vehicles on intersection approaches' },
]

const compact = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : String(v))

function TimeChart({ def, data, markers, labels = false }: { def: ChartDef; data: Kpi[]; markers: { t: number; label: string }[]; labels?: boolean }) {
  const rows = useMemo(() => data.map((k) => ({ t: k.t, v: def.get(k) })), [data, def])
  const last = [...rows].reverse().find((r) => r.v != null)?.v ?? null
  return (
    <div className="spot rounded-xl border border-transparent bg-surface p-5 shadow-card">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-ink">{def.title}</div>
          <div className="text-pretty text-[12px] leading-[1.45] text-ink-3">{def.note}</div>
        </div>
        <div className="tnum shrink-0 text-[24px] font-[650] leading-none tracking-[-0.02em] text-ink">
          <SlidingNumber value={num(last, def.unit === 'km/h' ? 1 : 0)} />
          <span className="ml-1 text-[12px] font-medium tracking-normal text-ink-3">{def.unit}</span>
        </div>
      </div>
      <div className="mt-4 h-[150px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="#f1f3f6" vertical={false} />
            <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(t) => clock(t)} tick={{ fontSize: 11, fill: '#687284' }} tickLine={false} axisLine={{ stroke: '#e6e8ec' }} minTickGap={40} />
            <YAxis tick={{ fontSize: 11, fill: '#687284' }} tickLine={false} axisLine={false} width={48} allowDecimals={false} tickFormatter={compact} />
            {markers.map((m, i) => (
              <ReferenceLine
                key={i}
                x={m.t}
                stroke="#8a93a3"
                strokeDasharray="5 5"
                label={labels ? { value: m.label.length > 22 ? `${m.label.slice(0, 21)}…` : m.label, position: 'insideTopLeft', fontSize: 10, fill: '#687284', offset: 6 } : undefined}
              />
            ))}
            <Tooltip
              cursor={{ stroke: '#0c1322', strokeWidth: 1, strokeOpacity: 0.25 }}
              contentStyle={TIP_STYLE}
              labelStyle={{ color: '#c4cbd7', marginBottom: 2 }}
              itemStyle={{ color: '#fff', padding: 0 }}
              labelFormatter={(t) => `Simulated ${clock(Number(t))}`}
              formatter={(v) => [`${num(Number(v), def.unit === 'km/h' ? 1 : 0)} ${def.unit}`, def.title]}
            />
            <Line type="monotone" dataKey="v" stroke={LINE} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: '#fff', strokeWidth: 2, fill: LINE }} isAnimationActive={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

const MODE_NAMES: Record<string, string> = { car: 'Cars & vans', truck: 'Trucks', bus: 'Buses', bike: 'Cyclists', emergency: 'Emergency' }

function ModeTable({ modes }: { modes: Record<string, ModeSummary> }) {
  const rows = Object.entries(modes)
  if (!rows.length) return <div className="text-[12px] text-ink-3">No completed trips yet.</div>
  return (
    <table className="w-full text-[12.5px]">
      <thead>
        <tr className="border-b border-border text-left text-[11.5px] text-ink-3">
          <th className="py-2 font-normal">Mode</th>
          <th className="py-2 text-right font-normal">Completed trips</th>
          <th className="py-2 text-right font-normal">Travel time</th>
          <th className="py-2 text-right font-normal">Delay</th>
          <th className="py-2 text-right font-normal">Stopped</th>
          <th className="py-2 text-right font-normal">Trip speed</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([m, s]) => (
          <tr key={m} className="border-b border-border last:border-0">
            <td className="py-2 font-medium text-ink">{MODE_NAMES[m] ?? m}</td>
            <td className="tnum py-2 text-right">{num(s.trips)}</td>
            <td className="tnum py-2 text-right">{secs(s.travelTime)}</td>
            <td className="tnum py-2 text-right">{secs(s.timeLoss)}</td>
            <td className="tnum py-2 text-right">{secs(s.waiting)}</td>
            <td className="tnum py-2 text-right">{s.speed == null ? '—' : `${num(s.speed, 1)} km/h`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
interface Variable {
  key: string
  label: string
  values: [unknown, unknown]
  names: [string, string]
}

function variablesFor(scenario: string, params: Record<string, unknown>): Variable[] {
  const out: Variable[] = [{ key: 'signals', label: 'Signal control', values: ['fixed', 'actuated'], names: ['Fixed-time', 'Actuated'] }]
  if ('preemption' in params) out.push({ key: 'preemption', label: 'Emergency pre-emption', values: [false, true], names: ['Off', 'On'] })
  if ('tsp' in params) out.push({ key: 'tsp', label: 'Transit signal priority', values: [false, true], names: ['Off', 'On'] })
  if (scenario === 'bike_lanes') out.push({ key: 'network', label: 'King Street design', values: ['district', 'district_bike'], names: ['As built', 'Bike lanes'] })
  if (scenario === 'weather') {
    const cond = params.condition === 'random' ? 'snow' : (params.condition as string)
    out.push({ key: 'condition', label: 'Weather', values: ['clear', cond], names: ['Clear', cond.replace('_', ' ')] })
  }
  return out
}

type Row = { label: string; get: (r: RunResults) => number | null | undefined; unit: string; better: 'lower' | 'higher' | 'none'; digits?: number }

const COMPARE_ROWS: Row[] = [
  { label: 'Completed trips', get: (r) => r.metrics.completed, unit: '', better: 'higher' },
  { label: 'Mean travel time (motor)', get: (r) => r.last?.overall?.travelTime, unit: 's', better: 'lower', digits: 1 },
  { label: 'Mean delay per trip (motor)', get: (r) => r.last?.overall?.timeLoss, unit: 's', better: 'lower', digits: 1 },
  { label: 'Mean stopped time (motor)', get: (r) => r.last?.overall?.waiting, unit: 's', better: 'lower', digits: 1 },
  { label: 'Cyclist trip speed', get: (r) => r.metrics.byMode.bike?.speed, unit: 'km/h', better: 'higher', digits: 1 },
  { label: 'Bus travel time', get: (r) => (r.scenario.transit as { meanTravel?: number } | undefined)?.meanTravel, unit: 's', better: 'lower', digits: 1 },
  { label: 'Bus delay vs. timetable', get: (r) => (r.scenario.transit as { meanDelay?: number } | undefined)?.meanDelay, unit: 's', better: 'lower', digits: 1 },
  { label: 'Mean ambulance response', get: (r) => r.scenario.meanResponse as number | undefined, unit: 's', better: 'lower', digits: 1 },
  { label: 'Signal pre-emptions', get: (r) => r.signals.preemptions, unit: '', better: 'none' },
  { label: 'Green extensions for buses', get: (r) => r.signals.tspExtensions, unit: '', better: 'none' },
  { label: 'Gridlock teleports', get: (r) => r.teleports, unit: '', better: 'lower' },
]

function Comparison() {
  const run = useStore((s) => s.run)
  const job = useStore((s) => s.compare)
  const set = useStore((s) => s.set)
  const toast = useStore((s) => s.toast)
  const vars = useMemo(() => (run ? variablesFor(run.scenario, run.params) : []), [run])
  const [varKey, setVarKey] = useState('signals')
  useEffect(() => {
    if (!vars.find((v) => v.key === varKey)) setVarKey(vars[0]?.key ?? 'signals')
  }, [vars, varKey])
  const v = vars.find((x) => x.key === varKey)
  // poll as a fallback to WebSocket job messages
  useEffect(() => {
    if (!job || job.state !== 'running') return
    const iv = setInterval(() => api.compareJob(job.id).then((j) => set({ compare: j })).catch(() => {}), 2000)
    return () => clearInterval(iv)
  }, [job, set])
  if (!run) return null
  const start = async () => {
    if (!v) return
    try {
      const j = await api.compare(run.scenario, run.params, run.seed, v.key, v.values)
      set({ compare: { ...j, names: v.names } as CompareJob & { names: [string, string] } })
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }
  const names = ((job as unknown as { names?: [string, string] })?.names ?? v?.names ?? ['A', 'B']) as [string, string]
  const done = job?.state === 'done' && job.variants[0] && job.variants[1]
  return (
    <div className="spot rounded-xl border border-transparent bg-surface shadow-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <div className="mr-auto min-w-[260px] flex-1">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
            <FlaskConical size={15} className="text-ink-2" /> <span className="text-[15px]">Controlled comparison</span>
          </div>
          <div className="text-[11.5px] text-ink-3">
            Two headless SUMO runs of <span className="font-medium text-ink-2">{run.title}</span> with seed <span className="tnum font-medium text-ink-2">{run.seed}</span> — identical demand draws and events; only one setting differs.
          </div>
        </div>
        <Segmented size="sm" value={varKey} options={vars.map((x) => ({ value: x.key, label: x.label }))} onChange={setVarKey} />
        <Button variant="primary" size="sm" onClick={start} disabled={job?.state === 'running'}>
          {job?.state === 'running' ? <Loader2 size={13} className="animate-spin" /> : null}
          {job?.state === 'running' ? 'Running…' : 'Run comparison'}
        </Button>
      </div>
      <div className="px-5 py-4">
        {!job && <div className="py-4 text-[12px] text-ink-3">Runs both variants for the full scenario length (≈20–60 s of computation each, in parallel).</div>}
        {job && job.state === 'running' && (
          <div className="space-y-2 py-2">
            {[0, 1].map((i) => (
              <div key={i} className="flex items-center gap-3 text-[12px]">
                <span className="w-24 font-medium text-ink">{names[i]}</span>
                <div className="h-1.5 flex-1 rounded-full bg-muted">
                  <div className="h-full rounded-full transition-[width]" style={{ width: `${(job.progress[i] ?? 0) * 100}%`, background: i ? SERIES_B : SERIES }} />
                </div>
                <span className="tnum w-10 text-right text-ink-3">{Math.round((job.progress[i] ?? 0) * 100)}%</span>
              </div>
            ))}
          </div>
        )}
        {job?.state === 'error' && <div className="py-3 text-[12px] text-bad">A variant failed to complete. Check the server log.</div>}
        {done && (
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="border-b border-border text-left text-[11.5px] text-ink-3">
                <th className="py-2 font-normal">Measure (whole run)</th>
                <th className="py-2 text-right font-normal">
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: SERIES }} />
                  {names[0]}
                </th>
                <th className="py-2 text-right font-normal">
                  <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: SERIES_B }} />
                  {names[1]}
                </th>
                <th className="py-2 text-right font-normal">Change</th>
              </tr>
            </thead>
            <tbody>
              {COMPARE_ROWS.map((row) => {
                const a = row.get(job.variants[0]!)
                const b = row.get(job.variants[1]!)
                if ((a == null || a === 0) && (b == null || b === 0) && row.unit !== '') return null
                if (a == null && b == null) return null
                const delta = a != null && b != null ? b - a : null
                const pct = a ? (delta! / a) * 100 : null
                const dir = delta == null || Math.abs(delta) < 1e-9 ? 0 : delta > 0 ? 1 : -1
                const Arrow = dir > 0 ? ArrowUpRight : dir < 0 ? ArrowDownRight : Minus
                return (
                  <tr key={row.label} className="border-b border-border last:border-0">
                    <td className="py-2 text-ink">{row.label}</td>
                    <td className="tnum py-2 text-right">{num(a, row.digits ?? 0)} {row.unit}</td>
                    <td className="tnum py-2 text-right">{num(b, row.digits ?? 0)} {row.unit}</td>
                    <td className="py-2 text-right">
                      {delta == null ? '—' : (
                        <Badge tone="neutral">
                          <Arrow size={12} className="mr-0.5" />
                          {delta > 0 ? '+' : ''}
                          {num(delta, row.digits ?? 0)}
                          {pct != null && isFinite(pct) ? ` (${pct > 0 ? '+' : ''}${pct.toFixed(0)}%)` : ''}
                        </Badge>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {done && (
          <div className="mt-3 flex items-start gap-2 rounded-lg bg-subtle px-3 py-2.5 text-[12px] leading-[1.5] text-ink-2 shadow-[inset_0_0_0_1px_var(--color-track)]">
            <span className="shrink-0 font-semibold text-ink">Single seed, no verdict.</span>
            <span>Differences include random variation in how SUMO resolves interactions, so none is marked better or worse. The Results tab uses 50 paired runs for its verdicts.</span>
          </div>
        )}
      </div>
    </div>
  )
}

export function AnalyticsView() {
  const run = useStore((s) => s.run)
  const history = useStore((s) => s.history)
  const stats = useStore((s) => s.stats)
  const results = useStore((s) => s.results)
  const [loaded, setLoaded] = useState<Kpi[] | null>(null)
  useEffect(() => {
    // backfill history when opening analytics mid-run (e.g. after a reload)
    if (run && history.length < 3) api.metrics().then(setLoaded).catch(() => {})
  }, [run, history.length])
  const data = history.length >= 3 ? history : (loaded ?? history)
  const modes = (stats?.kpi as Kpi & { modes?: Record<string, ModeSummary> })?.modes ?? results?.metrics.byMode ?? {}
  if (!run)
    return (
      <div className="view-in scroll-y flex min-w-0 flex-1 flex-col bg-canvas">
        <PageMasthead word="analytics" kicker="Analytics" title="Live measurements">
          Every chart here fills from SUMO while a scenario runs.
        </PageMasthead>
        <div className="flex flex-1 items-center justify-center">
        <Empty title="No run to analyse yet">
          Start a scenario in Simulation and its live measurements chart here.
          <div className="mt-4">
            <Button variant="primary" onClick={() => useStore.getState().set({ view: 'simulate' })}>
              Open Simulation
            </Button>
          </div>
        </Empty>
        </div>
      </div>
    )
  return (
    <div className="view-in scroll-y min-w-0 flex-1 bg-canvas">
      <PageMasthead
        word="analytics"
        kicker={results ? 'Analytics · run complete' : 'Analytics · live'}
        title={run.title}
        right={
          <div className="flex items-center gap-3.5 rounded-[12px] bg-black/35 py-2.5 pl-3 pr-4 shadow-[inset_0_0_0_1px_rgb(255_255_255/0.07)]">
            <SevenSegment value={clock(data[data.length - 1]?.t ?? 0)} height={26} />
            <span className="flex flex-col gap-1 leading-none">
              <span className="tnum text-[12px] font-medium text-ink-2">of {clock(run.duration)} simulated</span>
              {results ? <span className="text-[12px] text-ink-3">Run complete</span> : <SignalLamp aspect="go" label={<span className="text-[12px] text-ink-2">Live</span>} />}
            </span>
          </div>
        }
      >
        Seed <span className="tnum font-semibold text-ink">{run.seed}</span> with {run.signalMode === 'actuated' ? 'actuated' : 'fixed-time'} signals.{' '}
        <span className="tnum">{clock(data[data.length - 1]?.t ?? 0)}</span> of <span className="tnum">{clock(run.duration)}</span> simulated. Dashed lines mark scenario events.
      </PageMasthead>
      <div className="mx-auto flex max-w-[1312px] flex-col gap-8 px-6 pb-16 pt-8 min-[1100px]:px-8">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {CHARTS.map((c) => (
            <TimeChart key={c.key} def={c} data={data} markers={run.markers} labels={c.key === CHARTS[0].key} />
          ))}
        </div>
        <div className={cn('grid grid-cols-1 gap-4 xl:grid-cols-[1fr_1.4fr]')}>
          <div className="spot rounded-xl border border-transparent bg-surface p-5 shadow-card">
            <div className="mb-3 text-[15px] font-semibold text-ink">Completed trips by mode</div>
            <ModeTable modes={modes} />
            <div className="mt-2 text-[11px] text-ink-3">Cumulative since the scenario clock started. Trips still under way are not included.</div>
          </div>
          <Comparison />
        </div>
      </div>
    </div>
  )
}
