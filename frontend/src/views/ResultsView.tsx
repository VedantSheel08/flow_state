import { AlertTriangle, Check, Info, Loader2, Minus, Play, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { Badge, Button, Segmented, Tip } from '../components/ui'
import { api } from '../lib/api'
import {
  METRICS,
  VERDICT_LABEL,
  fromLive,
  improvementPct,
  pairs,
  results,
  scale,
  verdict,
  type ControllerId,
  type DemandLevel,
  type DemandResults,
  type MetricDef,
  type MetricKey,
  type RlInfo,
  type SummaryGroup,
  type Verdict,
} from '../lib/controllerResults'
import { cn, num } from '../lib/format'
import { useStore } from '../lib/store'

// Categorical slots from the dataviz reference palette (validated light mode, all pairs).
const COLOR: Record<ControllerId, string> = { queue_heuristic: '#2a78d6', fixed_time: '#eb6834', rl_ppo: '#1baf7a' }

const BASE = results.baseline
const FS = results.flowstate
const title = (c: ControllerId) => results.controllers[c].title

const HERO: MetricKey[] = ['mean_wait_s', 'mean_travel_s', 'queue_max_veh', 'throughput_vph']
const SCENARIO_METRICS: MetricKey[] = ['mean_wait_s', 'mean_travel_s', 'queue_max_veh']
const DETAIL: MetricKey[] = [
  'mean_wait_s',
  'mean_travel_s',
  'p95_travel_s',
  'throughput_vph',
  'completed_fraction',
  'queue_mean_veh',
  'queue_max_veh',
  'spillback_pct',
  'teleports',
  'collisions',
]

const TONE: Record<Verdict, 'good' | 'bad' | 'neutral'> = { better: 'good', worse: 'bad', unclear: 'neutral' }

function fmt(m: MetricDef, v: number | null | undefined) {
  const x = scale(m, v)
  return x == null ? '—' : `${num(x, m.digits)}${m.unit ? (m.unit === '%' ? '%' : ` ${m.unit}`) : ''}`
}

function Swatch({ c }: { c: ControllerId }) {
  return <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: COLOR[c] }} />
}

function VerdictBadge({ v }: { v: Verdict }) {
  const Icon = v === 'better' ? Check : v === 'worse' ? AlertTriangle : Minus
  return (
    <Badge tone={TONE[v]}>
      <Icon size={11} className="mr-0.5" />
      {VERDICT_LABEL[v]}
    </Badge>
  )
}

function changeText(pct: number | null | undefined) {
  if (pct == null || !isFinite(pct)) return '—'
  const word = pct < 0 ? 'lower' : 'higher'
  return `${Math.abs(pct).toFixed(1)}% ${word}`
}

// ---------------------------------------------------------------------------
function HeroTile({ k, group }: { k: MetricKey; group: SummaryGroup }) {
  const m = METRICS[k]
  const base = group[BASE]?.metrics[k]
  const fs = group[FS]?.metrics[k]
  if (!base || !fs) return null
  const v = verdict(m, fs.vs)
  return (
    <div className="flex flex-col rounded-xl border border-border bg-surface p-4 shadow-xs">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[12.5px] font-semibold text-ink">{m.label}</div>
          <div className="text-[11px] text-ink-3">{m.better === 'lower' ? 'Lower is better' : 'Higher is better'}</div>
        </div>
        <VerdictBadge v={v} />
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="tnum text-[28px] font-semibold leading-none tracking-[-0.02em] text-ink">{num(scale(m, fs.mean), m.digits)}</span>
        <span className="text-[12px] text-ink-3">{m.unit}</span>
        <span className="tnum ml-auto text-[13px] font-semibold text-ink">{changeText(fs.vs?.pct)}</span>
      </div>
      <div className="mt-2 space-y-1 text-[11.5px]">
        <div className="flex items-center gap-1.5 text-ink-2">
          <Swatch c={FS} /> {title(FS)}
          <span className="tnum ml-auto font-medium text-ink">{fmt(m, fs.mean)}</span>
        </div>
        <div className="flex items-center gap-1.5 text-ink-2">
          <Swatch c={BASE} /> {title(BASE)}
          <span className="tnum ml-auto font-medium text-ink">{fmt(m, base.mean)}</span>
        </div>
      </div>
      {fs.vs && (
        <div className="mt-3 border-t border-border pt-2 text-[11px] text-ink-3">
          Better in <span className="tnum font-medium text-ink-2">{fs.vs.better}</span> of {fs.vs.paired} paired runs, worse in{' '}
          <span className="tnum font-medium text-ink-2">{fs.vs.worse}</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
/** Dot-and-interval rows: FlowState's change vs fixed-time per traffic pattern, oriented so right = better. */
function ScenarioChart({ d, k }: { d: DemandResults; k: MetricKey }) {
  const m = METRICS[k]
  const rows = patternsIn(d).map((s) => {
    const g = d.perScenario[s.id]
    const base = g?.[BASE]?.metrics[k]
    const fs = g?.[FS]?.metrics[k]
    const imp = base && fs?.vs ? improvementPct(m, base.mean, fs.vs) : null
    return { s, base, fs, imp, v: verdict(m, fs?.vs) }
  })
  const extent = Math.max(10, ...rows.flatMap((r) => (r.imp ? [Math.abs(r.imp.lo), Math.abs(r.imp.hi)] : []))) * 1.1
  const x = (p: number) => `${50 + (p / extent) * 38}%`
  return (
    <div>
      <div className="grid grid-cols-[minmax(150px,1.1fr)_minmax(200px,2fr)_auto_auto] items-center gap-x-4 text-[11px] text-ink-3">
        <div className="pb-1.5">Traffic pattern</div>
        <div className="flex justify-between pb-1.5">
          <span>← Worse than fixed-time</span>
          <span>Better →</span>
        </div>
        <div className="pb-1.5 text-right">Fixed → FlowState</div>
        <div className="pb-1.5 text-right">Verdict</div>
        {rows.map(({ s, base, fs, imp, v }) => (
          <Row key={s.id}>
            <div className="py-2.5">
              <div className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
                {s.title}
                {s.heldOut && (
                  <Tip label="Never used to tune the controller — the fairest test of how it generalises">
                    <span>
                      <Badge tone="accent">Held out</Badge>
                    </span>
                  </Tip>
                )}
              </div>
              <div className="text-[11px] text-ink-3">{s.description}</div>
            </div>
            <Tip label={imp ? `${imp.mid >= 0 ? '+' : ''}${imp.mid.toFixed(1)}% (95% interval ${imp.lo.toFixed(1)}% to ${imp.hi.toFixed(1)}%)` : 'No data'}>
              <div className="relative h-8 cursor-default">
                <div className="absolute inset-y-1 left-1/2 w-px bg-border-strong" />
                {imp && (
                  <>
                    <div className="absolute top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-[#c5ccd5]" style={{ left: x(imp.lo), width: `calc(${x(imp.hi)} - ${x(imp.lo)})` }} />
                    <div
                      className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface"
                      style={{ left: x(imp.mid), background: COLOR[FS] }}
                    />
                    <div
                      className={cn('tnum absolute top-1/2 -translate-y-1/2 whitespace-nowrap text-[11px] font-medium text-ink-2', imp.mid >= 0 ? 'pl-3' : 'pr-3 -translate-x-full')}
                      style={{ left: imp.mid >= 0 ? x(Math.max(imp.mid, imp.hi)) : x(Math.min(imp.mid, imp.lo)) }}
                    >
                      {imp.mid >= 0 ? '+' : ''}
                      {imp.mid.toFixed(1)}%
                    </div>
                  </>
                )}
              </div>
            </Tip>
            <div className="tnum whitespace-nowrap text-right text-[12px] text-ink-2">
              {fmt(m, base?.mean)} → <span className="font-medium text-ink">{fmt(m, fs?.mean)}</span>
            </div>
            <div className="text-right">
              <VerdictBadge v={v} />
            </div>
          </Row>
        ))}
      </div>
      <div className="mt-2 text-[11px] text-ink-3">
        Dot: mean paired change over {d.seeds.length} seeds. Bar: 95% bootstrap interval. A pattern is only called a clear win or loss when the interval does not cross zero.
      </div>
    </div>
  )
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="col-span-4 grid grid-cols-subgrid items-center border-t border-border">{children}</div>
}

// ---------------------------------------------------------------------------
function PairedScatter({ d }: { d: DemandResults }) {
  const titles = Object.fromEntries(results.scenarios.map((s) => [s.id, s.title]))
  const pts = useMemo(() => pairs(d, BASE, FS).map((p) => ({ x: p.a.wait, y: p.b.wait, s: p.s, seed: p.seed })), [d])
  const wins = pts.filter((p) => p.y < p.x).length
  const step = 40
  const hi = Math.ceil(Math.max(...pts.flatMap((p) => [p.x, p.y])) / step) * step
  const lo = Math.floor(Math.min(...pts.flatMap((p) => [p.x, p.y])) / step) * step
  const ticks = Array.from({ length: (hi - lo) / step + 1 }, (_, i) => lo + i * step)
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-xs">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="text-[13px] font-semibold text-ink">Every run, head to head</div>
          <div className="text-[11.5px] text-ink-3">Each dot is one seed run twice with identical vehicles. Below the diagonal, FlowState waited less.</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="tnum text-[18px] font-semibold text-ink">
            {wins}
            <span className="text-[12px] font-normal text-ink-3"> / {pts.length}</span>
          </div>
          <div className="text-[11px] text-ink-3">runs FlowState won</div>
        </div>
      </div>
      <div className="mt-2 h-[260px]">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
            <CartesianGrid stroke="#EEF0F3" />
            <XAxis
              type="number"
              dataKey="x"
              domain={[lo, hi]}
              ticks={ticks}
              tick={{ fontSize: 11, fill: '#8A94A6' }}
              tickLine={false}
              axisLine={{ stroke: '#E4E7EC' }}
              label={{ value: 'Fixed-time mean wait (s)', position: 'insideBottom', offset: -10, fontSize: 11, fill: '#8A94A6' }}
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[lo, hi]}
              ticks={ticks}
              tick={{ fontSize: 11, fill: '#8A94A6' }}
              tickLine={false}
              axisLine={false}
              width={48}
              label={{ value: 'FlowState (s)', angle: -90, position: 'insideLeft', offset: 12, fontSize: 11, fill: '#8A94A6' }}
            />
            <ReferenceLine
              segment={[
                { x: lo, y: lo },
                { x: hi, y: hi },
              ]}
              stroke="#98A2B3"
              strokeDasharray="4 4"
            />
            <Tooltip
              cursor={false}
              content={({ active, payload }) => {
                const p = active && payload?.[0]?.payload
                if (!p) return null
                const diff = p.y - p.x
                return (
                  <div className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-[11.5px] shadow-float">
                    <div className="font-medium text-ink">
                      {titles[p.s]} · seed {p.seed}
                    </div>
                    <div className="tnum text-ink-2">
                      Fixed-time {p.x.toFixed(1)} s → FlowState {p.y.toFixed(1)} s
                    </div>
                    <div className={cn('tnum font-medium', diff < 0 ? 'text-good' : 'text-bad')}>
                      {diff < 0 ? '' : '+'}
                      {diff.toFixed(1)} s per vehicle
                    </div>
                  </div>
                )
              }}
            />
            <Scatter data={pts} fill={COLOR[FS]} fillOpacity={0.85} stroke="#fff" strokeWidth={1.5} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
function DetailTable({ d }: { d: DemandResults }) {
  const g = d.overall
  const cols = (['fixed_time', 'queue_heuristic', 'rl_ppo'] as ControllerId[]).filter((c) => g[c])
  return (
    <div className="rounded-xl border border-border bg-surface shadow-xs">
      <div className="border-b border-border px-4 py-3">
        <div className="text-[13px] font-semibold text-ink">All measures, all strategies</div>
        <div className="text-[11.5px] text-ink-3">Means over every paired run. Change is against fixed-time.</div>
      </div>
      <div className="overflow-x-auto px-4 pb-2">
        <table className="w-full min-w-[640px] text-[12.5px]">
          <thead>
            <tr className="border-b border-border text-left text-[11.5px] text-ink-3">
              <th className="py-2 font-normal">Measure</th>
              {cols.map((c) => (
                <th key={c} className="py-2 text-right font-normal">
                  <span className="inline-flex items-center gap-1.5">
                    <Swatch c={c} />
                    {title(c)}
                    {results.controllers[c].status === 'experimental' && <span className="text-ink-3">(experimental)</span>}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {DETAIL.map((k) => {
              const m = METRICS[k]
              return (
                <tr key={k} className="border-b border-border last:border-0">
                  <td className="py-2">
                    <div className="text-ink">{m.label}</div>
                    <div className="text-[11px] text-ink-3">{m.note}</div>
                  </td>
                  {cols.map((c) => {
                    const s = g[c]?.metrics[k]
                    const v = verdict(m, s?.vs)
                    return (
                      <td key={c} className="tnum py-2 text-right align-top">
                        <div className={cn('text-ink', c === FS && 'font-semibold')}>{fmt(m, s?.mean)}</div>
                        {s?.vs && s.vs.pct != null && isFinite(s.vs.pct) && (
                          <div className={cn('text-[11px]', v === 'better' ? 'text-good' : v === 'worse' ? 'text-bad' : 'text-ink-3')}>
                            {s.vs.pct > 0 ? '+' : ''}
                            {s.vs.pct.toFixed(1)}%{v === 'unclear' ? ' · unclear' : ''}
                          </div>
                        )}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
function Controllers() {
  const sf = results.safety
  const ids: ControllerId[] = ['fixed_time', 'queue_heuristic', 'rl_ppo']
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-xs">
      <div className="text-[13px] font-semibold text-ink">What was compared</div>
      <div className="mt-3 space-y-3">
        {ids.map((c) => {
          const ctl = results.controllers[c]
          return (
            <div key={c} className="flex gap-2.5">
              <span className="mt-[5px]">
                <Swatch c={c} />
              </span>
              <div>
                <div className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
                  {ctl.title}
                  <Badge tone={ctl.status === 'verified' ? 'good' : ctl.status === 'experimental' ? 'warn' : 'neutral'}>{ctl.status}</Badge>
                </div>
                <div className="text-[11.5px] text-ink-2">{ctl.detail}</div>
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-4 rounded-lg bg-subtle p-3">
        <div className="flex items-center gap-1.5 text-[12px] font-medium text-ink">
          <ShieldCheck size={13} className="text-good" /> Same safety layer for every strategy
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11.5px] text-ink-2 sm:grid-cols-3">
          <span>
            Min green <b className="tnum font-medium text-ink">{sf.minGreen} s</b>
          </span>
          <span>
            Yellow <b className="tnum font-medium text-ink">{sf.yellow} s</b>
          </span>
          <span>
            All-red <b className="tnum font-medium text-ink">{sf.allRed} s</b>
          </span>
          <span>
            Max green <b className="tnum font-medium text-ink">{sf.maxGreen} s</b>
          </span>
          <span>
            Decides every <b className="tnum font-medium text-ink">{sf.decision} s</b>
          </span>
        </div>
        <div className="mt-1.5 text-[11px] text-ink-3">Minimum green includes the pedestrian walk and clearance. No controller can skip yellow or all-red.</div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
function patternsIn(d: DemandResults) {
  return results.scenarios.filter((s) => d.perScenario[s.id])
}

function headline(d: DemandResults) {
  const fs = d.overall[FS]?.metrics
  const m = METRICS.mean_wait_s
  const pats = patternsIn(d)
  const wins = pats.filter((s) => verdict(m, d.perScenario[s.id]?.[FS]?.metrics.mean_wait_s?.vs) === 'better')
  const losses = pats.filter((s) => verdict(m, d.perScenario[s.id]?.[FS]?.metrics.mean_wait_s?.vs) === 'worse')
  const teleports = verdict(METRICS.teleports, fs?.teleports?.vs) === 'worse' ? { fs: fs?.teleports?.mean ?? 0, base: d.overall[BASE]?.metrics.teleports?.mean ?? 0 } : null
  return { wait: fs?.mean_wait_s?.vs?.pct, queue: fs?.queue_max_veh?.vs?.pct, wins, losses, teleports, patterns: pats.length, runs: fs?.mean_wait_s?.vs?.paired ?? 0 }
}

const SEED_OPTIONS = [3, 6]

/** Fresh comparisons through /api/rl; renders nothing on backends without it. */
function LivePanel({ info, level, onShow }: { info: RlInfo; level: DemandLevel; onShow: () => void }) {
  const job = useStore((s) => s.rlJob)
  const set = useStore((s) => s.set)
  const [pattern, setPattern] = useState('all')
  const [nSeeds, setNSeeds] = useState(3)
  const [error, setError] = useState<string | null>(null)
  const running = job?.status === 'queued' || job?.status === 'running'
  const adaptive = info.controllers.find((c) => c.id === info.default_controller)

  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return
    const iv = setInterval(() => {
      api
        .rlCompareJob(job.id)
        .then((j) => {
          set({ rlJob: j })
          if (j.status === 'done') onShow()
        })
        .catch((e) => setError((e as Error).message))
    }, 1500)
    return () => clearInterval(iv)
  }, [job, set, onShow])

  const start = async () => {
    setError(null)
    const base = 10000 + Math.floor(Math.random() * 80000)
    try {
      const j = await api.rlCompare({
        controllers: ['fixed_time', info.default_controller],
        scenarios: pattern === 'all' ? info.scenarios.map((s) => s.id) : [pattern],
        seeds: Array.from({ length: nSeeds }, (_, i) => base + i),
        intensity: info.intensity[level],
        baseline: 'fixed_time',
        include_series: false,
      })
      set({ rlJob: j })
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const pct = job ? (job.progress.done / Math.max(1, job.progress.total)) * 100 : 0
  return (
    <div className="mt-3 rounded-xl border border-border bg-surface p-4 shadow-xs">
      <div className="flex flex-wrap items-center gap-3">
        <div className="mr-auto min-w-[260px]">
          <div className="text-[13px] font-semibold text-ink">Run a fresh comparison</div>
          <div className="text-[11.5px] text-ink-3">
            Fixed-time vs {results.controllers[info.default_controller as ControllerId]?.title ?? adaptive?.title ?? info.default_controller} on new random seeds the controller has never seen, simulated in SUMO on this server now.
          </div>
        </div>
        <Segmented
          size="sm"
          value={pattern}
          onChange={setPattern}
          options={[{ value: 'all', label: 'All patterns' }, ...info.scenarios.map((s) => ({ value: s.id, label: s.title.replace(' (held out)', '') }))]}
        />
        <Segmented size="sm" value={nSeeds} onChange={setNSeeds} options={SEED_OPTIONS.map((n) => ({ value: n, label: `${n} seeds` }))} />
        <Button variant="primary" size="sm" onClick={start} disabled={running}>
          {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={12} />}
          {running ? 'Running…' : 'Run in SUMO'}
        </Button>
      </div>
      {running && job && (
        <div className="mt-3 flex items-center gap-3 text-[12px]">
          <div className="h-1.5 flex-1 rounded-full bg-muted">
            <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
          </div>
          <span className="tnum text-ink-3">
            {job.progress.done} / {job.progress.total} runs
          </span>
        </div>
      )}
      {job?.status === 'failed' && <div className="mt-2 text-[12px] text-bad">The comparison failed on the server. {job.error?.split('\n').slice(-1)[0]}</div>}
      {error && <div className="mt-2 text-[12px] text-bad">{error}</div>}
    </div>
  )
}

export function ResultsView() {
  const [level, setLevel] = useState<DemandLevel>('standard')
  const [k, setK] = useState<MetricKey>('mean_wait_s')
  const [info, setInfo] = useState<RlInfo | null>(null)
  const job = useStore((s) => s.rlJob)
  const [source, setSource] = useState<'recorded' | 'live'>(job?.status === 'done' ? 'live' : 'recorded')
  useEffect(() => {
    api.rlInfo().then(setInfo).catch(() => setInfo(null))
  }, [])
  const showLive = useCallback(() => setSource('live'), [])
  const live = useMemo(() => (job?.status === 'done' && job.result ? fromLive(job.result) : null), [job])
  const isLive = source === 'live' && live != null
  const d = isLive ? live : results.demand[level]
  const h = headline(d)
  const fallbacks = isLive ? (job?.result?.fallbacks ?? []) : []
  return (
    <div className="scroll-y min-w-0 flex-1 bg-canvas">
      <div className="mx-auto max-w-[1280px] px-6 py-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-[760px]">
            <div className="label-caps">Results · {isLive ? 'live SUMO comparison' : 'recorded SUMO evaluation'}</div>
            <h1 className="mt-0.5 text-[22px] font-semibold tracking-[-0.015em] text-ink">FlowState vs fixed-time signals</h1>
            <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
              Across <b className="tnum font-semibold text-ink">{h.runs}</b> paired runs, FlowState{' '}
              {h.wait != null && (
                <>
                  changed mean wait by <b className="tnum font-semibold text-ink">{h.wait > 0 ? '+' : ''}{h.wait.toFixed(1)}%</b>
                </>
              )}
              {h.queue != null && (
                <>
                  {' '}
                  and peak queues by <b className="tnum font-semibold text-ink">{h.queue > 0 ? '+' : ''}{h.queue.toFixed(1)}%</b>
                </>
              )}
              . It clearly cut waiting in <b className="font-semibold text-ink">{h.wins.length} of {h.patterns}</b> traffic {h.patterns === 1 ? 'pattern' : 'patterns'}
              {h.losses.length > 0 && (
                <>
                  {' '}
                  and was clearly worse in <b className="font-semibold text-ink">{h.losses.map((s) => s.title.toLowerCase()).join(', ')}</b>
                </>
              )}
              .
              {h.teleports && (
                <>
                  {' '}
                  The trade-off: more gridlock teleports (<b className="tnum font-semibold text-ink">{h.teleports.fs.toFixed(1)}</b> vs{' '}
                  <b className="tnum font-semibold text-ink">{h.teleports.base.toFixed(1)}</b> per run).
                </>
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {live && (
              <Segmented
                value={source}
                onChange={setSource}
                options={[
                  { value: 'recorded', label: 'Recorded evaluation' },
                  { value: 'live', label: 'Your live run' },
                ]}
              />
            )}
            {!isLive && (
              <Segmented
                value={level}
                onChange={setLevel}
                options={[
                  { value: 'standard', label: 'Standard peak', title: `Demand ×${results.demand.standard.intensity}` },
                  { value: 'stress', label: 'Over-saturated', title: `Demand ×${results.demand.stress.intensity}` },
                ]}
              />
            )}
          </div>
        </div>

        {info && <LivePanel info={info} level={level} onShow={showLive} />}

        {fallbacks.length > 0 && (
          <div className="mt-3 rounded-xl border border-warn/30 bg-warn-soft px-4 py-2.5 text-[12px] text-warn">
            {fallbacks.map((f) => `${f.requested} was not available, so ${f.used} ran instead: ${f.reason}`).join(' ')}
          </div>
        )}

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {HERO.map((key) => (
            <HeroTile key={key} k={key} group={d.overall} />
          ))}
        </div>

        <div className="mt-3 rounded-xl border border-border bg-surface p-4 shadow-xs">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-[13px] font-semibold text-ink">By traffic pattern</div>
              <div className="text-[11.5px] text-ink-3">Where adaptive control helps — and where it does not.</div>
            </div>
            <Segmented size="sm" value={k} onChange={setK} options={SCENARIO_METRICS.map((x) => ({ value: x, label: METRICS[x].label }))} />
          </div>
          <ScenarioChart d={d} k={k} />
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-[1.25fr_1fr]">
          <PairedScatter d={d} />
          <Controllers />
        </div>

        <div className="mt-3">
          <DetailTable d={d} />
        </div>

        <div className="mt-3 flex gap-2 rounded-xl border border-border bg-subtle px-4 py-3 text-[11.5px] leading-relaxed text-ink-2">
          <Info size={14} className="mt-0.5 shrink-0 text-ink-3" />
          <div>
            <b className="font-medium text-ink">How these numbers were made.</b> SUMO runs of a {results.network.description.replace('2 x 2', '2×2')}, demand ×{d.intensity},{' '}
            {h.patterns} traffic {h.patterns === 1 ? 'pattern' : 'patterns'} × {d.seeds.length} {isLive ? 'fresh random' : 'held-out'} seeds ({d.seeds[0]}–{d.seeds[d.seeds.length - 1]}) per strategy
            {isLive && d.wallSeconds != null ? `, computed on this server in ${Math.round(d.wallSeconds)} s` : ''}. The same seed produces exactly the same vehicles for every strategy, so each
            comparison is paired. Results are from the 2×2 evaluation grid, not the district map in the Simulation tab.{' '}
            {isLive ? (
              'Nothing here was tuned on these seeds.'
            ) : (
              <>
                Source: <code className="text-[11px]">{results.source.files.join(', ')}</code> at <code className="text-[11px]">{results.source.commit}</code>.
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
