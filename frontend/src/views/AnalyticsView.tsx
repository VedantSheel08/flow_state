import { FlaskConical, Loader2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Badge, Button, Empty, Segmented } from '../components/ui'
import { api } from '../lib/api'
import { clock, cn, num, secs } from '../lib/format'
import { useStore } from '../lib/store'
import type { CompareJob, Kpi, ModeSummary, RunResults } from '../lib/types'

// Validated categorical slots (dataviz reference palette, light mode)
const SERIES = '#2a78d6'
const SERIES_B = '#eb6834'

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

function TimeChart({ def, data, markers }: { def: ChartDef; data: Kpi[]; markers: { t: number; label: string }[] }) {
  const rows = useMemo(() => data.map((k) => ({ t: k.t, v: def.get(k) })), [data, def])
  const last = [...rows].reverse().find((r) => r.v != null)?.v ?? null
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-[13px] font-semibold text-ink">{def.title}</div>
          <div className="text-[11.5px] text-ink-3">{def.note}</div>
        </div>
        <div className="tnum text-[18px] font-semibold text-ink">
          {num(last, def.unit === 'km/h' ? 1 : 0)}
          <span className="ml-1 text-[11.5px] font-normal text-ink-3">{def.unit}</span>
        </div>
      </div>
      <div className="mt-3 h-[150px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="#EEF0F3" vertical={false} />
            <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(t) => clock(t)} tick={{ fontSize: 11, fill: '#8A94A6' }} tickLine={false} axisLine={{ stroke: '#E4E7EC' }} minTickGap={40} />
            <YAxis tick={{ fontSize: 11, fill: '#8A94A6' }} tickLine={false} axisLine={false} width={44} allowDecimals={false} />
            {markers.map((m, i) => (
              <ReferenceLine key={i} x={m.t} stroke="#D0D5DD" strokeDasharray="3 3" />
            ))}
            <Tooltip
              cursor={{ stroke: '#98A2B3', strokeWidth: 1 }}
              contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #E4E7EC', boxShadow: '0 4px 12px rgba(16,24,40,.08)' }}
              labelFormatter={(t) => `Simulated ${clock(Number(t))}`}
              formatter={(v) => [`${num(Number(v), def.unit === 'km/h' ? 1 : 0)} ${def.unit}`, def.title]}
            />
            <Line type="monotone" dataKey="v" stroke={SERIES} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
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
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
        <div className="mr-auto min-w-[260px] flex-1">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
            <FlaskConical size={14} className="text-ink-2" /> Controlled comparison
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
      <div className="px-4 py-3">
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
                const good = delta == null || row.better === 'none' || Math.abs(delta) < 1e-9 ? null : row.better === 'lower' ? delta < 0 : delta > 0
                return (
                  <tr key={row.label} className="border-b border-border last:border-0">
                    <td className="py-2 text-ink">{row.label}</td>
                    <td className="tnum py-2 text-right">{num(a, row.digits ?? 0)} {row.unit}</td>
                    <td className="tnum py-2 text-right">{num(b, row.digits ?? 0)} {row.unit}</td>
                    <td className="py-2 text-right">
                      {delta == null ? '—' : (
                        <Badge tone={good == null ? 'neutral' : good ? 'good' : 'bad'}>
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
        {done && <div className="pt-2 text-[11px] text-ink-3">Single seed: differences include stochastic variation in how SUMO resolves interactions. Repeat with other seeds before drawing conclusions.</div>}
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
      <div className="flex flex-1 items-center justify-center">
        <Empty title="No run to analyse">Start a scenario from the Simulation workspace. Charts fill from live SUMO measurements.</Empty>
      </div>
    )
  return (
    <div className="scroll-y min-w-0 flex-1 bg-canvas">
      <div className="mx-auto max-w-[1280px] px-6 py-5">
        <div className="mb-4 flex items-end justify-between">
          <div>
            <div className="label-caps">Analytics</div>
            <h1 className="mt-0.5 text-[20px] font-semibold tracking-[-0.01em] text-ink">{run.title}</h1>
            <div className="mt-0.5 text-[12.5px] text-ink-2">
              Seed <span className="tnum font-medium text-ink">{run.seed}</span> · {run.signalMode === 'actuated' ? 'actuated' : 'fixed-time'} signals · {clock(data[data.length - 1]?.t ?? 0)} of {clock(run.duration)} simulated
            </div>
          </div>
          {results && <Badge tone="neutral">Run complete</Badge>}
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {CHARTS.map((c) => (
            <TimeChart key={c.key} def={c} data={data} markers={run.markers} />
          ))}
        </div>
        <div className={cn('mt-3 grid grid-cols-1 gap-3 xl:grid-cols-[1fr_1.4fr]')}>
          <div className="rounded-lg border border-border bg-surface p-4">
            <div className="mb-2 text-[13px] font-semibold text-ink">Completed trips by mode</div>
            <ModeTable modes={modes} />
          </div>
          <Comparison />
        </div>
      </div>
    </div>
  )
}
