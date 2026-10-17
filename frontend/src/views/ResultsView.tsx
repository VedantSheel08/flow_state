import { BadgeCheck, ChevronDown, CircleCheck, FlaskConical, Info, Loader2, Lock, Play, RotateCcw, ShieldCheck, Siren, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { PageMasthead } from '../components/PageHeader'
import { LampHead, SevenSegment, type Aspect } from '../components/ui/traffic'
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
  type MetricStats,
  type RlInfo,
  type RlJob,
  type Verdict,
} from '../lib/controllerResults'
import { cn, num } from '../lib/format'
import { useStore } from '../lib/store'

const COLOR: Record<ControllerId, string> = { queue_heuristic: '#2a78d6', fixed_time: '#eb6834', rl_ppo: '#1baf7a' }
const BASE = results.baseline
const FS = results.flowstate
const title = (c: ControllerId) => results.controllers[c].title

const HERO: MetricKey[] = ['mean_wait_s', 'mean_travel_s', 'queue_max_veh', 'throughput_vph']
const PATTERN_METRICS: MetricKey[] = ['mean_wait_s', 'mean_travel_s', 'queue_max_veh']
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
  'emergency_mean_stopped_s',
]

const HERO_LABEL: Partial<Record<MetricKey, { what: string; per: string }>> = {
  mean_wait_s: { what: 'less waiting', per: 's of waiting per vehicle' },
  mean_travel_s: { what: 'shorter travel times', per: 's per trip' },
  queue_max_veh: { what: 'shorter peak queues', per: 'vehicles at the worst moment of each run' },
  throughput_vph: { what: 'more throughput', per: 'vehicles per hour' },
}

// ---- hero motion ------------------------------------------------------------
const reduceMotion = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** True one frame after mount, so CSS transitions run from their start state. */
function useShown() {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)))
    return () => cancelAnimationFrame(id)
  }, [])
  return shown
}

/** Counts from 0 to target once, easing out; instant under reduced motion. */
function useCountUp(target: number, ms = 900) {
  const [v, setV] = useState(() => (reduceMotion() ? target : 0))
  const raf = useRef(0)
  useEffect(() => {
    // reduced motion: the initial state is already the target (the hero remounts per dataset)
    if (reduceMotion()) return
    const t0 = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms)
      setV(target * (1 - Math.pow(1 - p, 4)))
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [target, ms])
  return v
}

// ---- small pieces ---------------------------------------------------------
const card = 'rounded-[12px] border border-transparent bg-surface shadow-card'

function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('text-[12px] font-semibold leading-[1.4] text-ink-3', className)}>{children}</div>
}

function H2({ children, className }: { children: ReactNode; className?: string }) {
  return <h2 className={cn('m-0 text-balance text-[18px] font-semibold leading-[1.44] tracking-[-0.01em] text-ink', className)}>{children}</h2>
}

function Cap({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn('m-0 text-pretty text-[13px] leading-[1.54] text-ink-3', className)}>{children}</p>
}

const VTONE: Record<Verdict, string> = {
  better: 'bg-good-soft text-good shadow-[inset_0_0_0_1px_var(--color-good-line)]',
  worse: 'bg-bad-soft text-bad shadow-[inset_0_0_0_1px_var(--color-bad-line)]',
  unclear: 'bg-muted text-ink-2 shadow-[inset_0_0_0_1px_var(--color-border-strong)]',
}

function Pill({ tone, children }: { tone: Verdict | 'warn' | 'live'; children: ReactNode }) {
  const t =
    tone === 'warn'
      ? 'bg-warn-soft text-warn shadow-[inset_0_0_0_1px_var(--color-warn-line)]'
      : tone === 'live'
        ? 'bg-flow-soft text-flow-ink shadow-[inset_0_0_0_1px_var(--color-flow-line)]'
        : VTONE[tone]
  return <span className={cn('inline-flex h-[22px] shrink-0 items-center gap-[5px] whitespace-nowrap rounded-full pl-1.5 pr-2 text-[12px] font-[550]', t)}>{children}</span>
}

/** Verdicts read like a signal: go, caution, stop. The lit lamp's position and the label both carry it. */
const ASPECT: Record<Verdict, Aspect> = { better: 'go', unclear: 'caution', worse: 'stop' }

function VerdictBadge({ v, label }: { v: Verdict; label?: string }) {
  return (
    <Pill tone={v}>
      <LampHead aspect={ASPECT[v]} size={4} className="-ml-0.5" />
      {label ?? VERDICT_LABEL[v]}
    </Pill>
  )
}

function SignalKey() {
  const items: [Verdict, string][] = [
    ['better', 'Clear improvement'],
    ['unclear', 'No clear difference'],
    ['worse', 'Clearly worse'],
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12.5px] text-ink-3">
      {items.map(([v, l]) => (
        <span key={v} className="flex items-center gap-1.5">
          <LampHead aspect={ASPECT[v]} size={5} />
          {l}
        </span>
      ))}
      <span>· the 95% interval must exclude zero. Lower is better, except throughput.</span>
    </div>
  )
}

function Swatch({ c, size = 8 }: { c: ControllerId; size?: number }) {
  return <span className="inline-block shrink-0 rounded-[2px]" style={{ width: size, height: size, background: COLOR[c] }} />
}

function fmtVal(m: MetricDef, v: number | null | undefined, unit = true) {
  const x = scale(m, v)
  if (x == null) return '—'
  const u = !unit || !m.unit ? '' : m.unit === '%' ? '%' : ` ${m.unit}`
  return `${num(x, m.digits)}${u}`
}

const signed = (p: number, digits = 1) => `${p > 0 ? '+' : p < 0 ? '−' : ''}${Math.abs(p).toFixed(digits)}%`

/** Change text, coloured only when the verdict is clear. */
function Delta({ m, s, mark = false, className }: { m: MetricDef; s?: MetricStats; mark?: boolean; className?: string }) {
  const pct = s?.vs?.pct
  if (pct == null || !isFinite(pct)) return <span className={cn('text-ink-3', className)}>—</span>
  const v = verdict(m, s?.vs)
  return (
    <span className={cn('tnum', v === 'better' ? 'font-[550] text-good' : v === 'worse' ? 'font-[550] text-bad' : 'text-ink-3', className)}>
      {mark && v === 'better' ? '✓ ' : mark && v === 'worse' ? '▲ ' : ''}
      {signed(pct)}
    </span>
  )
}

function Bars({ m, base, fs, size = 'sm', grow = false }: { m: MetricDef; base: number; fs: number; size?: 'sm' | 'lg'; grow?: boolean }) {
  const shown = useShown()
  const max = Math.max(base, fs) * 1.087 || 1
  const rows: [ControllerId, string, number][] = [
    [BASE, size === 'lg' ? 'Fixed-time' : 'Fixed', base],
    [FS, 'FlowState', fs],
  ]
  const h = size === 'lg' ? 'h-3' : 'h-2'
  return (
    <div className={cn('flex flex-col', size === 'lg' ? 'max-w-[620px] gap-2.5' : 'gap-1.5 text-[12px]')}>
      {rows.map(([c, label, v]) => (
        <div key={c} className="flex items-center gap-2">
          <span className={cn('flex shrink-0 items-center gap-2', size === 'lg' ? 'w-32' : 'w-[78px]', c === FS ? 'font-medium text-ink-2' : 'text-ink-3')}>
            <Swatch c={c} />
            {label}
          </span>
          <div className={cn('min-w-0 flex-1 overflow-hidden rounded-full bg-muted', h)}>
            <div
              className={cn('rounded-r-[4px]', h, grow && 'grow-x')}
              data-shown={grow ? shown : undefined}
              style={{ width: `${(v / max) * 100}%`, background: COLOR[c], transitionDelay: grow && c === FS ? '120ms' : undefined }}
            />
          </div>
          <span className={cn('tnum shrink-0 text-right', size === 'lg' ? 'w-16' : 'w-12', c === FS ? 'font-semibold text-ink' : 'text-ink-2')}>
            {num(scale(m, v), m.digits)}
          </span>
        </div>
      ))}
    </div>
  )
}

// ---- derived story ---------------------------------------------------------
function patternsIn(d: DemandResults) {
  return results.scenarios.filter((s) => d.perScenario[s.id])
}

function story(d: DemandResults) {
  const o = d.overall[FS]?.metrics ?? {}
  const b = d.overall[BASE]?.metrics ?? {}
  const v = (k: MetricKey) => verdict(METRICS[k], o[k]?.vs)
  const wait = METRICS.mean_wait_s
  const pats = patternsIn(d).map((s) => {
    const fs = d.perScenario[s.id]?.[FS]?.metrics.mean_wait_s
    return { s, fs, base: d.perScenario[s.id]?.[BASE]?.metrics.mean_wait_s, v: verdict(wait, fs?.vs) }
  })
  const wins = pats.filter((p) => p.v === 'better')
  const losses = pats.filter((p) => p.v === 'worse')
  const clear = HERO.filter((k) => v(k) === 'better')
  const unclear = HERO.filter((k) => v(k) === 'unclear')
  const strongest = [...clear].sort((x, y) => Math.abs(o[y]?.vs?.pct ?? 0) - Math.abs(o[x]?.vs?.pct ?? 0))[0]
  const tradeoffs = DETAIL.filter((k) => v(k) === 'worse')
  const ev: MetricKey | null = v('emergency_mean_stopped_s') === 'better' && (b.emergency_mean_stopped_s?.mean ?? 0) > 0 ? 'emergency_mean_stopped_s' : null
  return { o, b, v, pats, wins, losses, clear, unclear, strongest, tradeoffs, ev, runs: o.mean_wait_s?.vs?.paired ?? 0 }
}
type Story = ReturnType<typeof story>

const lowerList = (ks: MetricKey[]) => ks.map((k) => METRICS[k].label.toLowerCase())
const joinAnd = (xs: string[]) => (xs.length < 2 ? (xs[0] ?? '') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)
const cap1 = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function B({ children }: { children: ReactNode }) {
  return <b className="tnum font-semibold text-ink">{children}</b>
}

function Summary({ d, st, live }: { d: DemandResults; st: Story; live: boolean }) {
  const wait = st.o.mean_wait_s?.vs?.pct
  const queue = st.o.queue_max_veh?.vs?.pct
  const tp = st.v('teleports') === 'worse' ? { fs: st.o.teleports?.mean ?? 0, base: st.b.teleports?.mean ?? 0 } : null
  const lead = live ? `Across ${st.runs} paired runs you just started` : d.intensity > 1 ? 'With more traffic than the grid can clear' : `Across ${st.runs} paired runs`
  return (
    <p className="m-0 max-w-[760px] text-pretty text-[15px] leading-[1.6] text-ink-2">
      {lead}, FlowState changed mean wait by {wait != null ? <B>{signed(wait)}</B> : '—'} and peak queues by {queue != null ? <B>{signed(queue)}</B> : '—'}. It clearly cut waiting in{' '}
      {st.wins.length} of {st.pats.length} traffic {st.pats.length === 1 ? 'pattern' : 'patterns'}
      {st.losses.length > 0 && <> and was clearly worse in {joinAnd(st.losses.map((p) => p.s.title.toLowerCase()))}</>}.
      {tp && (
        <>
          {' '}
          The trade-off: more gridlock teleports (<B>{tp.fs.toFixed(1)}</B> vs <B>{tp.base.toFixed(1)}</B> per run).
        </>
      )}
    </p>
  )
}

// ---- hero ------------------------------------------------------------------
function StrongestResult({ st, live }: { st: Story; live: boolean }) {
  const k = st.strongest!
  const m = METRICS[k]
  const fs = st.o[k]!
  const base = st.b[k]!
  const imp = fs.vs ? improvementPct(m, base.mean, fs.vs) : null
  const lbl = HERO_LABEL[k]!
  const ev = st.ev ? { m: METRICS[st.ev], fs: st.o[st.ev]!, base: st.b[st.ev]! } : null
  const shown = useCountUp(fs.vs?.pct ?? 0)
  return (
    <div className="flex min-w-0 flex-[999_1_540px] flex-col gap-5 px-9 py-9">
      <div className="flex flex-wrap items-center gap-2.5">
        <Eyebrow>{live ? 'Strongest result in your run' : 'Strongest result'}</Eyebrow>
        <VerdictBadge v="better" />
      </div>
      <div className="flex flex-wrap items-end gap-5">
        <div className="tnum text-[72px] font-[650] leading-none tracking-[-0.045em] text-ink min-[1280px]:text-[104px]" aria-label={signed(fs.vs?.pct ?? 0)}>
          {signed(shown)}
        </div>
        <div className="flex flex-col gap-0.5 pb-1.5">
          <div className="text-[22px] font-semibold leading-[1.27] tracking-[-0.015em]">{lbl.what}</div>
          <div className="tnum text-[14px] text-ink-2">
            {num(scale(m, base.mean), m.digits)} → {num(scale(m, fs.mean), m.digits)} {lbl.per}
          </div>
        </div>
      </div>
      <Bars m={m} base={base.mean} fs={fs.mean} size="lg" grow />
      <Cap className="tnum">
        Better in {fs.vs?.better} of {fs.vs?.paired} paired runs, worse in {fs.vs?.worse}.{imp && ` 95% interval: ${imp.lo.toFixed(1)}% to ${imp.hi.toFixed(1)}% better than fixed-time.`}
      </Cap>
      {ev && (
        <div className="tnum flex flex-wrap items-center gap-3 rounded-[10px] bg-subtle px-3.5 py-3 shadow-[inset_0_0_0_1px_var(--color-track)]">
          <Siren size={16} className="text-ink-2" />
          <span className="min-w-[240px] flex-1 text-ink-2">
            <b className="font-semibold text-ink">Emergency vehicles stood still {Math.abs(ev.fs.vs?.pct ?? 0).toFixed(0)}% less</b> · {fmtVal(ev.m, ev.base.mean)} → {fmtVal(ev.m, ev.fs.mean)}, better in{' '}
            {ev.fs.vs?.better} of {ev.fs.vs?.paired} runs
          </span>
          <VerdictBadge v="better" />
        </div>
      )}
    </div>
  )
}

function WhatStillHolds({ st }: { st: Story }) {
  const others = st.clear.map((k) => `${METRICS[k].label.toLowerCase()} was clearly ${METRICS[k].better === 'lower' ? 'lower' : 'higher'} (${signed(st.o[k]?.vs?.pct ?? 0)})`)
  return (
    <div className="flex min-w-0 flex-[999_1_540px] flex-col gap-5 px-9 py-9">
      <div className="flex flex-wrap items-center gap-2.5">
        <Eyebrow>What still holds</Eyebrow>
        <VerdictBadge v={st.wins.length ? 'better' : 'unclear'} />
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <div className="tnum text-[72px] font-[650] leading-none tracking-[-0.045em] min-[1280px]:text-[96px]">
          {st.wins.length}
          <span className="text-[36px] font-medium text-ink-4"> / {st.pats.length}</span>
        </div>
        <div className="pb-2 text-[22px] font-semibold leading-[1.27] tracking-[-0.015em]">traffic patterns with clearly less waiting</div>
      </div>
      {st.wins.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {st.wins.map((p) => (
            <div key={p.s.id} className="tnum flex items-center gap-2 text-ink-2">
              <CircleCheck size={14} strokeWidth={2.5} className="text-good" />
              <span className="font-medium text-ink">{p.s.title}</span>
              <span>
                {signed(p.fs?.vs?.pct ?? 0)} wait · {p.fs?.vs?.better} of {p.fs?.vs?.paired} runs
              </span>
            </div>
          ))}
        </div>
      )}
      <Cap>
        {others.length ? `Overall, ${joinAnd(others)}. ` : ''}
        {st.unclear.length ? `${cap1(joinAnd(lowerList(st.unclear)))}: no clear difference.` : ''}
      </Cap>
    </div>
  )
}

function SidePanel({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-w-0 flex-[1_1_400px] flex-col gap-[22px] bg-white/[0.025] p-9">
      <span aria-hidden className="lane-v absolute bottom-9 left-0 top-9 w-[2px]" />
      {children}
    </div>
  )
}

function Step({ n, head, badge, children }: { n: number; head: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex gap-3.5">
      <span className="tnum flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface text-[12px] font-semibold text-ink-2 shadow-[inset_0_0_0_1px_var(--color-border-strong)]">{n}</span>
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] font-semibold">{head}</span>
          {badge}
        </div>
        <div className="text-ink-2">{children}</div>
      </div>
    </div>
  )
}

function TenSeconds({ st }: { st: Story }) {
  const anyWorse = st.losses.length > 0 || st.tradeoffs.length > 0
  const worked: { v: Verdict; label: string } = st.clear.length ? { v: 'better', label: anyWorse ? 'Mostly' : 'Yes' } : { v: 'unclear', label: 'Not clearly' }
  const clearText = st.clear.map((k) => `${METRICS[k].label.toLowerCase()} (${signed(st.o[k]?.vs?.pct ?? 0)})`)
  const loss = st.losses[0]
  return (
    <SidePanel>
      <Eyebrow>The 10-second version</Eyebrow>
      <Step n={1} head="What we compared">
        Fixed-time signals vs FlowState adaptive on identical traffic: same seed, same vehicles, same safety rules. {st.runs} paired SUMO runs.
      </Step>
      <Step n={2} head="Did it work?" badge={<VerdictBadge v={worked.v} label={worked.label} />}>
        {clearText.length ? `Clearly better ${joinAnd(clearText)}. ` : ''}Waiting dropped clearly in {st.wins.length} of {st.pats.length} traffic patterns.
        {st.unclear.length ? ` ${cap1(joinAnd(lowerList(st.unclear)))}: no clear difference.` : ''}
      </Step>
      <Step n={3} head="Where it didn’t" badge={loss || st.tradeoffs.length ? <VerdictBadge v="worse" /> : undefined}>
        {loss
          ? `${loss.s.title}${loss.s.heldOut ? ', the pattern we held out of all tuning' : ''}: ${Math.abs(loss.fs?.vs?.pct ?? 0).toFixed(1)}% more waiting than fixed-time, worse in ${loss.fs?.vs?.worse} of ${loss.fs?.vs?.paired} runs.`
          : st.tradeoffs.length
            ? `${cap1(joinAnd(lowerList(st.tradeoffs)))} clearly worse overall.`
            : 'No traffic pattern was clearly worse.'}
      </Step>
    </SidePanel>
  )
}

function TradeOff({ st }: { st: Story }) {
  const k: MetricKey = st.tradeoffs.includes('teleports') ? 'teleports' : st.tradeoffs[0]
  const m = METRICS[k]
  const fs = st.o[k]!
  const base = st.b[k]!
  const rest = st.tradeoffs.filter((x) => x !== k)
  return (
    <SidePanel>
      <div className="flex flex-wrap items-center gap-2.5">
        <Eyebrow>The trade-off</Eyebrow>
        <VerdictBadge v="worse" />
      </div>
      <div className="tnum text-[22px] font-semibold leading-[1.27] tracking-[-0.015em]">
        {fmtVal(m, fs.mean, false)} vs {fmtVal(m, base.mean, false)} {k === 'teleports' ? 'gridlock teleports per run' : `${m.unit} ${m.label.toLowerCase()}`}
      </div>
      <Bars m={m} base={base.mean} fs={fs.mean} grow />
      <div className="text-ink-2">
        {k === 'teleports' && 'SUMO removes vehicles stuck in gridlock. '}FlowState was worse in {fs.vs?.worse} of {fs.vs?.paired} runs, better in {fs.vs?.better}.
        {rest.length > 0 && ` ${joinAnd(rest.map((x) => `${METRICS[x].label.toLowerCase()} (${signed(st.o[x]?.vs?.pct ?? 0)})`)).replace(/^./, (c) => c.toUpperCase())} ${rest.length === 1 ? 'was' : 'were'} also clearly worse.`}
      </div>
    </SidePanel>
  )
}

function Agreement({ st, rec }: { st: Story; rec: Story }) {
  const agree = HERO.filter((k) => st.v(k) === rec.v(k)).length
  return (
    <SidePanel>
      <Eyebrow>Does it match the recording?</Eyebrow>
      <div className="text-[22px] font-semibold leading-[1.27] tracking-[-0.015em]">
        {agree} of {HERO.length} headline verdicts agree
      </div>
      <p className="m-0 text-ink-2">Anyone can rerun the claim on the spot. Agreement is checked per measure: same verdict, same direction, against the recorded evaluation at the same demand.</p>
    </SidePanel>
  )
}

function Hero({ st, live, rec }: { st: Story; live: boolean; rec: Story | null }) {
  const big = st.strongest && Math.abs(st.o[st.strongest]?.vs?.pct ?? 0) >= 5
  return (
    <section className="asphalt flex flex-wrap overflow-hidden rounded-[18px] shadow-[0_1px_2px_rgba(12,19,34,0.12),0_18px_44px_-14px_rgba(12,19,34,0.45)]">
      {big ? <StrongestResult st={st} live={live} /> : <WhatStillHolds st={st} />}
      {st.tradeoffs.length > 0 && st.v('teleports') === 'worse' ? <TradeOff st={st} /> : live && rec ? <Agreement st={st} rec={rec} /> : <TenSeconds st={st} />}
    </section>
  )
}

// ---- live comparison -------------------------------------------------------
const startedAt = new Map<string, number>()
const SEED_OPTIONS = [3, 6]

function useLiveJob(info: RlInfo | null) {
  const job = useStore((s) => s.rlJob)
  const set = useStore((s) => s.set)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return
    const iv = setInterval(() => {
      api
        .rlCompareJob(job.id)
        .then((j) => set({ rlJob: j }))
        .catch((e) => setError((e as Error).message))
    }, 1500)
    return () => clearInterval(iv)
  }, [job, set])
  const start = useCallback(
    async (scenarios: string[], nSeeds: number, intensity: number) => {
      if (!info) return
      setError(null)
      const base = 10000 + Math.floor(Math.random() * 80000)
      try {
        const j = await api.rlCompare({
          controllers: ['fixed_time', info.default_controller],
          scenarios,
          seeds: Array.from({ length: nSeeds }, (_, i) => base + i),
          intensity,
          baseline: 'fixed_time',
          include_series: false,
        })
        startedAt.set(j.id, performance.now())
        set({ rlJob: j })
      } catch (e) {
        setError((e as Error).message)
      }
    },
    [info, set],
  )
  return { job, error, start }
}

function PatternSelect({ info, value, onChange, disabled }: { info: RlInfo; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <label className="flex items-center gap-2 text-[12px] font-medium text-ink-3">
      Pattern
      <span className="relative">
        <select
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 min-w-[168px] cursor-pointer appearance-none rounded-lg bg-surface pl-3 pr-8 text-[13px] font-medium text-ink shadow-[inset_0_0_0_1px_var(--color-border-strong),0_1px_2px_rgba(12,19,34,0.04)] disabled:cursor-not-allowed disabled:bg-subtle disabled:text-ink-disabled"
        >
          <option value="all">All patterns</option>
          {info.scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title.replace(' (held out)', '')}
            </option>
          ))}
        </select>
        <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
      </span>
    </label>
  )
}

function LiveStrip({ info, level, live }: { info: RlInfo; level: DemandLevel; live: ReturnType<typeof useLiveJob> }) {
  const [pattern, setPattern] = useState('all')
  const [nSeeds, setNSeeds] = useState(6)
  const { job, error, start } = live
  const running = job?.status === 'queued' || job?.status === 'running'
  const run = () => start(pattern === 'all' ? info.scenarios.map((s) => s.id) : [pattern], nSeeds, info.intensity[level])
  const failed = job?.status === 'failed' || error
  const controls = (
    <div className="flex flex-wrap items-center gap-3">
      <PatternSelect info={info} value={pattern} onChange={setPattern} disabled={running} />
      <span className="flex items-center gap-2 text-[12px] font-medium text-ink-3">
        Seeds
        <SeedToggle value={nSeeds} onChange={setNSeeds} disabled={running} />
      </span>
      <button
        onClick={run}
        disabled={running}
        aria-busy={running}
        className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-ink pl-3 pr-3.5 text-[13px] font-semibold text-white shadow-[0_1px_2px_rgba(12,19,34,0.2),inset_0_1px_0_rgba(255,255,255,0.08)] transition-[background-color,scale] duration-150 ease-out hover:bg-[#1d2638] active:scale-[0.96] disabled:cursor-progress disabled:bg-border-strong"
      >
        {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={13} fill="currentColor" />}
        {running ? 'Running…' : 'Run in SUMO'}
      </button>
    </div>
  )
  if (running && job) return <RunningPanel job={job} controls={controls} />
  return (
    <section className={cn(card, 'flex flex-col gap-3 py-3.5 pl-5 pr-4')}>
      <div className="flex flex-wrap items-center gap-4">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-ink">
          <RotateCcw size={16} />
        </span>
        <div className="min-w-0 flex-[999_1_280px]">
          <div className="text-[14px] font-semibold">Run a fresh comparison</div>
          <Cap>Both controllers, identical new seeds the controller has never seen, live in SUMO on this server. About 15 seconds.</Cap>
        </div>
        {controls}
      </div>
      {failed && (
        <div role="alert" className="flex items-center gap-3 rounded-[10px] bg-bad-soft px-3.5 py-3 text-[#7a1426] shadow-[inset_0_0_0_1px_var(--color-bad-line)]">
          <TriangleAlert size={16} className="shrink-0 text-bad" />
          <div className="min-w-0 flex-1">
            <b className="font-semibold">The SUMO run stopped.</b> {error ?? job?.error?.trim().split('\n').slice(-1)[0]}
          </div>
          <button onClick={run} className="h-[30px] rounded-lg bg-surface px-3 text-[13px] font-semibold text-ink shadow-[inset_0_0_0_1px_var(--color-bad-line)]">
            Try again
          </button>
        </div>
      )}
    </section>
  )
}

function SeedToggle({ value, onChange, disabled }: { value: number; onChange: (v: number) => void; disabled?: boolean }) {
  return (
    <span role="group" aria-label="Seeds per pattern" className="inline-flex gap-0.5 rounded-lg bg-track p-0.5">
      {SEED_OPTIONS.map((n) => (
        <button
          key={n}
          disabled={disabled}
          aria-pressed={value === n}
          onClick={() => onChange(n)}
          className={cn(
            'tnum h-7 rounded-md px-3 text-[13px] font-medium transition-[background-color,color,box-shadow] duration-150',
            value === n ? 'bg-surface text-ink shadow-thumb' : 'text-ink-2 hover:text-ink',
            disabled && 'cursor-not-allowed text-ink-disabled',
          )}
        >
          {n}
        </button>
      ))}
    </span>
  )
}

function RunningPanel({ job, controls }: { job: RlJob; controls: ReactNode }) {
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    const iv = setInterval(() => setNow(performance.now()), 500)
    return () => clearInterval(iv)
  }, [])
  const t0 = startedAt.get(job.id)
  const elapsed = t0 != null ? (now - t0) / 1000 : null
  const { done, total } = job.progress
  const left = elapsed != null && done > 0 ? Math.max(0, (elapsed / done) * (total - done)) : null
  return (
    <section aria-live="polite" className={cn(card, 'flex flex-col gap-5 px-6 pb-6 pt-[22px] shadow-[0_0_0_1px_var(--color-flow-line),0_0_0_4px_rgba(42,120,214,0.1),0_1px_2px_rgba(12,19,34,0.04)]')}>
      <div className="flex flex-wrap items-center gap-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-flow-soft text-flow">
          <Loader2 size={18} className="animate-spin" />
        </span>
        <div className="min-w-0 flex-[999_1_300px]">
          <div className="text-[15px] font-semibold">Running a fresh comparison in SUMO</div>
          <Cap>Both controllers drive identical vehicles on every seed.</Cap>
        </div>
        {controls}
      </div>
      <div className="flex flex-col gap-2">
        <div className="tnum flex items-baseline justify-between">
          <span className="flex items-center gap-2.5">
            <span className="rounded-lg bg-[#080d17] px-2.5 py-1.5 shadow-[inset_0_0_0_1px_rgb(255_255_255/0.06)]">
              <SevenSegment value={String(done).padStart(String(total).length, ' ')} height={22} />
            </span>
            <span className="text-[14px] text-ink-3">of {total} runs</span>
          </span>
          <Cap className="tnum">{elapsed != null ? `${left != null ? `about ${Math.ceil(left)} s left · ` : ''}${Math.floor(elapsed)} s elapsed` : 'Running'}</Cap>
        </div>
        <div role="progressbar" aria-label="Runs completed" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} className="h-2 overflow-hidden rounded-[4px] bg-track">
          <div className="h-2 rounded-[4px] bg-ink transition-[width] duration-300 ease-linear" style={{ width: `${(done / Math.max(1, total)) * 100}%` }} />
        </div>
      </div>
      <p className="m-0 flex items-center gap-1.5 text-[12px] text-ink-3">
        <Info size={13} /> The results below stay on screen. Your run opens as its own view when it finishes.
      </p>
    </section>
  )
}

function Receipt({ job, onBack, onRerun }: { job: RlJob; onBack: () => void; onRerun: () => void }) {
  const r = job.result!
  const all = r.request.scenarios.length === results.scenarios.length
  const pat = all ? 'All patterns' : r.request.scenarios.map((s) => results.scenarios.find((x) => x.id === s)?.title ?? s).join(', ')
  return (
    <section className={cn(card, 'flex flex-wrap items-center gap-4 bg-[#f7faff] py-3.5 pl-5 pr-4 shadow-[0_0_0_1px_#cfe0f6]')}>
      <Pill tone="live">
        <CircleCheck size={13} strokeWidth={2.5} />
        Finished in {Math.max(1, Math.round(r.wall_seconds))} s
      </Pill>
      <div className="tnum flex min-w-0 flex-[999_1_360px] flex-wrap gap-x-[18px] gap-y-1.5 text-ink-2">
        <span>
          <b className="font-semibold text-ink">{pat}</b> × {r.request.seeds.length} seeds
        </span>
        <span>{r.episodes.length / 2} paired runs</span>
        <span>Demand ×{r.request.intensity}</span>
        <span>
          Seeds {r.request.seeds[0]}–{r.request.seeds[r.request.seeds.length - 1]}
        </span>
      </div>
      <div className="flex gap-2">
        <button onClick={onBack} className="h-8 rounded-lg bg-surface px-3.5 text-[13px] font-semibold text-ink shadow-[inset_0_0_0_1px_var(--color-border-strong)] hover:bg-subtle">
          Back to recorded
        </button>
        <button onClick={onRerun} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-ink px-3.5 text-[13px] font-semibold text-white hover:bg-[#1d2638]">
          <RotateCcw size={13} /> Run again
        </button>
      </div>
      <p className="m-0 basis-full text-[12px] leading-[1.5] text-ink-3">{r.caveat}</p>
      {r.fallbacks.length > 0 && (
        <p className="m-0 basis-full text-[12px] text-warn">{r.fallbacks.map((f) => `${f.requested} was unavailable, so ${f.used} ran instead: ${f.reason}`).join(' ')}</p>
      )}
    </section>
  )
}

function RecordedVsLive({ d, rec, recRuns }: { d: DemandResults; rec: DemandResults; recRuns: number }) {
  const liveRuns = d.overall[FS]?.metrics.mean_wait_s?.vs?.paired ?? 0
  return (
    <section className={cn(card, 'overflow-hidden')}>
      <div className="flex flex-col gap-1 px-6 pb-4 pt-5">
        <H2>Recorded vs your live run</H2>
        <Cap>Change vs fixed-time. Same verdict rule for both: the 95% interval must exclude zero over 3+ paired runs.</Cap>
      </div>
      <div className="overflow-x-auto">
        <table className="tnum w-full min-w-[820px] border-collapse">
          <thead>
            <tr className="bg-subtle">
              <Th left>Measure</Th>
              <Th>Recorded · {recRuns} runs</Th>
              <Th>Live · {liveRuns} runs</Th>
              <Th left>Live verdict</Th>
            </tr>
          </thead>
          <tbody>
            {HERO.map((k, i) => {
              const m = METRICS[k]
              const r = rec.overall[FS]?.metrics[k]
              const l = d.overall[FS]?.metrics[k]
              const last = i === HERO.length - 1
              return (
                <tr key={k}>
                  <Td left last={last} className="font-medium">
                    {m.label}
                  </Td>
                  <Td last={last}>
                    {r?.vs?.pct != null ? signed(r.vs.pct) : '—'} <span className="ml-2">{<VerdictBadge v={verdict(m, r?.vs)} />}</span>
                  </Td>
                  <Td last={last} className="font-semibold">
                    {l?.vs?.pct != null ? signed(l.vs.pct) : '—'}
                  </Td>
                  <Td left last={last}>
                    <VerdictBadge v={verdict(m, l?.vs)} />
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

// ---- tiles -----------------------------------------------------------------
function Tile({ k, d }: { k: MetricKey; d: DemandResults }) {
  const m = METRICS[k]
  const base = d.overall[BASE]?.metrics[k]
  const fs = d.overall[FS]?.metrics[k]
  if (!base || !fs) return null
  return (
    <article className={cn(card, 'flex flex-col gap-3.5 p-5')}>
      <div className="flex flex-wrap items-center justify-between gap-x-1.5 gap-y-1.5">
        <span className="whitespace-nowrap font-medium text-ink-2">{m.label}</span>
        <VerdictBadge v={verdict(m, fs.vs)} />
      </div>
      <div className="flex items-baseline gap-2.5">
        <span className="tnum text-[34px] font-[650] leading-[1.12] tracking-[-0.03em]">
          {num(scale(m, fs.mean), m.digits)}
          <span className="text-[16px] font-medium tracking-normal text-ink-3"> {m.unit}</span>
        </span>
        <Delta m={m} s={fs} className="text-[14px]" />
      </div>
      <Bars m={m} base={base.mean} fs={fs.mean} />
      {fs.vs && (
        <div className="tnum border-t border-muted pt-3 text-[12px] text-ink-3">
          Better in <b className="font-semibold text-ink">{fs.vs.better}</b> of {fs.vs.paired} paired runs, worse in {fs.vs.worse}
        </div>
      )}
    </article>
  )
}

// ---- by traffic pattern ----------------------------------------------------
const PATTERN_GRID = 'grid grid-cols-[minmax(180px,220px)_minmax(240px,1fr)_150px_172px] items-center gap-5'

function PatternChart({ d, k }: { d: DemandResults; k: MetricKey }) {
  const m = METRICS[k]
  const rows = patternsIn(d).map((s) => {
    const g = d.perScenario[s.id]
    const base = g?.[BASE]?.metrics[k]
    const fs = g?.[FS]?.metrics[k]
    return { s, base, fs, imp: base && fs?.vs ? improvementPct(m, base.mean, fs.vs) : null, v: verdict(m, fs?.vs) }
  })
  const maxAbs = Math.max(5, ...rows.flatMap((r) => (r.imp ? [Math.abs(r.imp.lo), Math.abs(r.imp.hi)] : [])))
  const tick = Math.ceil(maxAbs / 10) * 10
  const ext = (tick * 7) / 6
  const x = (p: number) => `${50 + (p / ext) * 50}%`
  const ticks = [-tick, -tick / 2, 0, tick / 2, tick]
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[860px]">
        <div className={cn(PATTERN_GRID, 'border-b border-border px-3 pb-2')}>
          <Eyebrow>Pattern</Eyebrow>
          <div className="relative h-8">
            <div className="absolute inset-x-0 top-0 flex justify-between text-[12px] font-medium text-ink-2">
              <span>← Worse than fixed-time</span>
              <span>Better →</span>
            </div>
            {ticks.map((t) => (
              <span key={t} className={cn('tnum absolute top-4 -translate-x-1/2 text-[11px]', t === 0 ? 'font-semibold text-ink' : 'text-ink-3')} style={{ left: x(t) }}>
                {t === 0 ? '0' : signed(t, 0)}
              </span>
            ))}
          </div>
          <Eyebrow className="text-right">Fixed → FlowState</Eyebrow>
          <Eyebrow>Verdict</Eyebrow>
        </div>
        {rows.map(({ s, base, fs, imp, v }) => {
          const bad = v === 'worse'
          return (
            <div
              key={s.id}
              className={cn(PATTERN_GRID, 'px-3 py-3.5', bad ? 'my-1.5 rounded-[10px] bg-[#fff7f8] shadow-[inset_0_0_0_1px_#fde2e6]' : 'border-b border-muted')}
            >
              <div>
                <div className="flex items-center gap-2 font-semibold">
                  {s.title}
                  {s.heldOut && (
                    <span className="inline-flex h-5 items-center gap-1 rounded-[6px] bg-surface px-[7px] text-[11px] font-semibold tracking-[0.02em] text-ink-2 shadow-[inset_0_0_0_1px_var(--color-border-strong)]" title="Never used to tune the controller">
                      <Lock size={11} strokeWidth={2.5} /> Held out
                    </span>
                  )}
                </div>
                <div className="text-[12px] leading-[1.33] text-ink-3">{s.description}</div>
              </div>
              <div className="relative h-7" title={imp ? `${signed(imp.mid)} (95% interval ${signed(imp.lo)} to ${signed(imp.hi)})` : undefined}>
                {[-tick / 2, tick / 2].map((t) => (
                  <div key={t} className={cn('absolute inset-y-0 w-px', bad ? 'bg-[#fbe4e8]' : 'bg-muted')} style={{ left: x(t) }} />
                ))}
                <div className="absolute -inset-y-1 left-1/2 w-0 border-l-[1.5px] border-dashed border-ink-4" />
                {imp && (
                  <>
                    <div className="absolute top-[11px] h-1.5 rounded-[3px] bg-[rgba(42,120,214,0.25)]" style={{ left: x(imp.lo), width: `calc(${x(imp.hi)} - ${x(imp.lo)})` }} />
                    <div className="absolute top-2 -ml-1.5 h-3 w-3 rounded-full bg-flow" style={{ left: x(imp.mid), boxShadow: `0 0 0 2px ${bad ? '#fff7f8' : '#fff'}` }} />
                    <span className={cn('tnum absolute -top-2 -translate-x-1/2 text-[11px] font-semibold', bad ? 'text-bad' : 'text-ink-2')} style={{ left: x(imp.mid) }}>
                      {signed(imp.mid)}
                    </span>
                  </>
                )}
              </div>
              <div className="tnum text-right">
                <span className="text-ink-3">{num(scale(m, base?.mean), m.digits)} →</span> <b className="font-semibold">{fmtVal(m, fs?.mean)}</b>
                {fs?.vs && (
                  <div className="text-[12px] text-ink-3">
                    {bad ? `worse in ${fs.vs.worse}` : `better in ${fs.vs.better}`} of {fs.vs.paired}
                  </div>
                )}
              </div>
              <div>
                <VerdictBadge v={v} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---- head to head ----------------------------------------------------------
function HeadToHead({ d }: { d: DemandResults }) {
  const titles = Object.fromEntries(results.scenarios.map((s) => [s.id, s.title]))
  const held = new Set(results.scenarios.filter((s) => s.heldOut).map((s) => s.id))
  const pts = useMemo(() => pairs(d, BASE, FS).map((p) => ({ x: p.a.wait, y: p.b.wait, s: p.s, seed: p.seed })), [d])
  const [hover, setHover] = useState<number | null>(null)
  // the plot fills its column; height follows width within a readable range
  const wrap = useRef<HTMLDivElement>(null)
  const [cw, setCw] = useState(560)
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setCw(Math.round(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  if (!pts.length) return null
  const wins = pts.filter((p) => p.y < p.x).length
  const losses = pts.filter((p) => p.y > p.x)
  const lossBy = new Map<string, number>()
  for (const p of losses) lossBy.set(p.s, (lossBy.get(p.s) ?? 0) + 1)
  const [topLoss, topN] = [...lossBy.entries()].sort((a, b) => b[1] - a[1])[0] ?? ['', 0]
  const far = [...pts].sort((a, b) => Math.abs(b.y - b.x) - Math.abs(a.y - a.x))[0]
  const all = pts.flatMap((p) => [p.x, p.y])
  const lo = Math.max(0, Math.floor(Math.min(...all) / 10) * 10 - 10)
  const hi = Math.ceil(Math.max(...all) / 10) * 10 + 10
  const step = hi - lo > 300 ? 100 : 50
  const ticks: number[] = []
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t)
  const ML = 60
  const W = Math.max(300, cw - ML - 16)
  const S = Math.round(Math.min(500, Math.max(360, W * 0.6)))
  const TW = ML + W + 16
  const TH = S + 60
  const px = (v: number) => ((v - lo) / (hi - lo)) * W
  const py = (v: number) => ((v - lo) / (hi - lo)) * S
  const hp = hover != null ? pts[hover] : null
  return (
    <section className={cn(card, 'flex flex-wrap gap-8 p-6')}>
      <div className="flex min-w-0 flex-[999_1_540px] flex-col gap-4">
        <div className="flex flex-col gap-1">
          <H2>Every run, head to head</H2>
          <Cap>Each dot is one seed run twice, once per controller. Below the diagonal, FlowState waited less.</Cap>
        </div>
        <div ref={wrap} className="overflow-x-auto">
          <div className="relative" style={{ width: TW, height: TH }}>
            <svg width={TW} height={TH} role="img" aria-label={`Paired mean waits: FlowState waited less in ${wins} of ${pts.length} runs`} onMouseLeave={() => setHover(null)}>
              <text x={14} y={4 + S / 2} transform={`rotate(-90 14 ${4 + S / 2})`} textAnchor="middle" fontSize={12} fontWeight={500} fill="#46505f">
                FlowState wait (s)
              </text>
              <text x={ML + W / 2} y={S + 48} textAnchor="middle" fontSize={12} fontWeight={500} fill="#46505f">
                Fixed-time wait (s)
              </text>
              <g transform={`translate(${ML} 4)`}>
                {ticks.map((t) => (
                  <g key={t}>
                    <line x1={0} x2={W} y1={S - py(t)} y2={S - py(t)} stroke="#f1f3f6" />
                    <line y1={0} y2={S} x1={px(t)} x2={px(t)} stroke="#f1f3f6" />
                    <text x={-8} y={S - py(t) + 4} textAnchor="end" fontSize={11} fill="#687284" className="tnum">
                      {t}
                    </text>
                    <text x={px(t)} y={S + 18} textAnchor="middle" fontSize={11} fill="#687284" className="tnum">
                      {t}
                    </text>
                  </g>
                ))}
                <polygon points={`0,${S} ${W},${S} ${W},0`} fill="rgba(42,120,214,0.045)" />
                <line x1={0} y1={S} x2={W} y2={0} stroke="#8a93a3" strokeWidth={1.5} strokeDasharray="5 5" />
                <line x1={0} y1={0} x2={0} y2={S} stroke="#d3d8df" />
                <line x1={0} y1={S} x2={W} y2={S} stroke="#d3d8df" />
                <text x={W - 12} y={S - 14} textAnchor="end" fontSize={12} fontWeight={600} fill="#46505f">
                  FlowState waited less
                </text>
                <text x={12} y={22} fontSize={12} fontWeight={600} fill="#46505f">
                  Fixed-time waited less
                </text>
                {pts.map((p, i) => {
                  const win = p.y <= p.x
                  const c = win ? COLOR[FS] : COLOR[BASE]
                  const h = held.has(p.s)
                  return (
                    <g key={`${p.s}/${p.seed}`} onMouseEnter={() => setHover(i)} style={{ cursor: 'default' }}>
                      <circle cx={px(p.x)} cy={S - py(p.y)} r={12} fill="transparent" />
                      <circle cx={px(p.x)} cy={S - py(p.y)} r={5.5} fill={h ? '#fff' : c} stroke={h ? c : '#fff'} strokeWidth={h ? 2.5 : 2} />
                      {hover === i && <circle cx={px(p.x)} cy={S - py(p.y)} r={9} fill="none" stroke="#0c1322" strokeWidth={2} />}
                    </g>
                  )
                })}
              </g>
            </svg>
            {hp && (
              <div
                className="tnum pointer-events-none absolute z-10 w-[200px] rounded-[10px] bg-ink px-3 py-2.5 text-[12px] leading-[1.5] text-white shadow-tip"
                style={{ left: Math.min(ML + px(hp.x) + 16, TW - 200), top: Math.max(0, 4 + S - py(hp.y) - 70) }}
              >
                <div className="font-semibold">
                  {titles[hp.s]} · seed {hp.seed}
                </div>
                <div className="flex justify-between text-[#c7ccd6]">
                  <span className="flex items-center gap-1.5">
                    <Swatch c={BASE} /> Fixed-time
                  </span>
                  <span>{hp.x.toFixed(1)} s</span>
                </div>
                <div className="flex justify-between text-[#c7ccd6]">
                  <span className="flex items-center gap-1.5">
                    <Swatch c={FS} /> FlowState
                  </span>
                  <span>{hp.y.toFixed(1)} s</span>
                </div>
                <div className="mt-1 flex justify-between border-t border-white/15 pt-1">
                  <span>FlowState</span>
                  <span className="font-semibold">
                    {hp.y - hp.x > 0 ? '+' : '−'}
                    {Math.abs(hp.y - hp.x).toFixed(1)} s ({signed(((hp.y - hp.x) / hp.x) * 100, 0)})
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
      <div className="flex min-w-0 flex-[1_1_260px] flex-col gap-5 pt-1 min-[1280px]:max-w-[300px]">
        <div className="flex flex-col gap-1.5 rounded-[12px] bg-subtle p-6 shadow-[inset_0_0_0_1px_var(--color-track)]">
          <div className="tnum flex items-baseline gap-1.5">
            <span className="text-[64px] font-[650] leading-none tracking-[-0.04em]">{wins}</span>
            <span className="text-[24px] font-medium text-ink-4">/ {pts.length}</span>
          </div>
          <div className="text-[15px] font-semibold">runs FlowState won on mean wait</div>
          <div className="text-ink-2">
            Fixed-time won the other {losses.length}
            {topN >= 2 ? `, including ${topN} in ${titles[topLoss]?.toLowerCase()}.` : '.'}
          </div>
        </div>
        <div className="flex flex-col gap-2.5 text-[12px] text-ink-2">
          <Eyebrow>Legend</Eyebrow>
          <span className="flex items-center gap-2.5">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR[FS] }} />
            FlowState waited less
          </span>
          <span className="flex items-center gap-2.5">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR[BASE] }} />
            Fixed-time waited less
          </span>
          {[...held].some((h) => d.perScenario[h]) && (
            <span className="flex items-center gap-2.5">
              <span className="h-2.5 w-2.5 rounded-full shadow-[inset_0_0_0_2px_#46505f]" />
              Hollow = {results.scenarios.find((s) => s.heldOut)?.title.toLowerCase()} (held out)
            </span>
          )}
        </div>
        <p className="tnum m-0 text-[12px] leading-[1.5] text-ink-3">
          Hover any dot for its pattern, seed and both waits. The biggest gap: {titles[far.s]?.toLowerCase()}, seed {far.seed}: {far.x.toFixed(1)} s → {far.y.toFixed(1)} s.
        </p>
      </div>
    </section>
  )
}

// ---- tables & context ------------------------------------------------------
function Th({ children, left }: { children: ReactNode; left?: boolean }) {
  return <th className={cn('whitespace-nowrap border-b border-border px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.05em] text-ink-3 first:pl-6 last:pr-6', left ? 'text-left' : 'text-right')}>{children}</th>
}

function Td({ children, left, last, className }: { children: ReactNode; left?: boolean; last?: boolean; className?: string }) {
  return <td className={cn('whitespace-nowrap px-4 py-[11px] first:pl-6 last:pr-6', !last && 'border-b border-muted', left ? 'text-left' : 'text-right', className)}>{children}</td>
}

function AllMeasures({ d }: { d: DemandResults }) {
  const g = d.overall
  const cols = (['fixed_time', 'queue_heuristic', 'rl_ppo'] as ControllerId[]).filter((c) => g[c])
  const rows = DETAIL.filter((k) => g[BASE]?.metrics[k])
  return (
    <section className={cn(card, 'overflow-hidden')}>
      <div className="flex flex-col gap-1 px-6 pb-4 pt-5">
        <H2>All measures, all strategies</H2>
        <Cap>Change vs fixed-time beside each value. Colour and a mark appear only when the 95% interval excludes zero.</Cap>
      </div>
      <div className="overflow-x-auto">
        <table className="tnum w-full min-w-[820px] border-collapse">
          <thead>
            <tr className="bg-subtle">
              <Th left>Measure</Th>
              {cols.map((c) => (
                <Th key={c}>
                  <span className={cn('inline-flex items-center gap-1.5', c === FS && 'text-ink')}>
                    <Swatch c={c} />
                    {title(c)}
                    {results.controllers[c].status === 'experimental' && ' · experimental'}
                  </span>
                </Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((k, i) => {
              const m = METRICS[k]
              const last = i === rows.length - 1
              const perRun = k === 'teleports' || k === 'collisions'
              return (
                <tr key={k}>
                  <Td left last={last} className="font-medium">
                    {m.label} {perRun && <span className="font-normal text-ink-3">per run</span>}
                  </Td>
                  {cols.map((c) => {
                    const s = g[c]?.metrics[k]
                    return (
                      <Td key={c} last={last} className={c === FS ? 'font-semibold' : c === 'rl_ppo' ? 'text-ink-2' : ''}>
                        {fmtVal(m, s?.mean)}
                        {c !== BASE && <Delta m={m} s={s} mark className="ml-1.5 font-medium" />}
                      </Td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Compared() {
  const sf = results.safety
  const rows: { c: ControllerId; badge: ReactNode }[] = [
    { c: 'fixed_time', badge: <Pill tone="unclear">Baseline</Pill> },
    {
      c: 'queue_heuristic',
      badge: (
        <Pill tone="unclear">
          <BadgeCheck size={13} strokeWidth={2.5} />
          <span className="text-ink">Verified</span>
        </Pill>
      ),
    },
    {
      c: 'rl_ppo',
      badge: (
        <Pill tone="warn">
          <FlaskConical size={13} strokeWidth={2.5} />
          Experimental
        </Pill>
      ),
    },
  ]
  const limits: [string, number][] = [
    ['Min green', sf.minGreen],
    ['Yellow', sf.yellow],
    ['All-red', sf.allRed],
    ['Max green', sf.maxGreen],
    ['Decides every', sf.decision],
  ]
  return (
    <div className={cn(card, 'flex min-w-0 flex-[999_1_560px] flex-col gap-4 p-6')}>
      <H2>What was compared</H2>
      <div className="flex flex-col">
        {rows.map(({ c, badge }, i) => (
          <div key={c} className={cn('flex items-start gap-3.5 py-3', i < rows.length - 1 && 'border-b border-muted')}>
            <span className="mt-[5px]">
              <Swatch c={c} size={10} />
            </span>
            <div className="min-w-0 flex-1">
              <div className={cn('font-semibold', c === 'rl_ppo' && 'text-ink-2')}>{title(c)}</div>
              <Cap>{results.controllers[c].detail}</Cap>
            </div>
            {badge}
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-3 rounded-[10px] bg-subtle p-4 shadow-[inset_0_0_0_1px_var(--color-track)]">
        <div className="flex items-center gap-2 font-semibold">
          <ShieldCheck size={15} className="text-ink-2" /> Same safety layer for every strategy
        </div>
        <div className="tnum grid grid-cols-[repeat(auto-fit,minmax(96px,1fr))] gap-2">
          {limits.map(([l, v]) => (
            <div key={l} className="rounded-lg bg-surface px-3 py-2.5 shadow-[inset_0_0_0_1px_var(--color-border)]">
              <div className="text-[11px] text-ink-3">{l}</div>
              <div className="text-[16px] font-semibold">{v} s</div>
            </div>
          ))}
        </div>
        <div className="text-[12px] text-ink-3">Minimum green includes the pedestrian walk and clearance. No controller can skip yellow or all-red.</div>
      </div>
    </div>
  )
}

function Method({ d, live }: { d: DemandResults; live: boolean }) {
  const pats = patternsIn(d).length
  const rows: [string, ReactNode][] = [
    ['Network', results.network.description.replace('2 x 2', '2 × 2')],
    ['Demand', `×${d.intensity} (${d.intensity > 1 ? 'over-saturated' : 'standard peak'})`],
    [
      'Runs',
      `${pats} ${pats === 1 ? 'pattern' : 'patterns'} × ${d.seeds.length} ${live ? 'fresh random' : 'held-out'} seeds (${d.seeds[0]}–${d.seeds[d.seeds.length - 1]}), ${d.episodes} episodes${d.wallSeconds != null ? `, ${Math.round(d.wallSeconds)} s wall time` : ''}`,
    ],
    ['Pairing', 'Same seed = identical vehicles for every strategy'],
    ['Verdict', 'Paired 95% interval of the difference excludes zero, 3+ paired runs'],
    [
      'Source',
      live ? (
        'Run just now on this server through /api/rl'
      ) : (
        <span className="font-mono text-[11.5px] text-ink-2">
          {results.source.files[d.intensity > 1 ? 1 : 0]}
          <br />
          {results.source.commit} · {results.source.ref}
        </span>
      ),
    ],
  ]
  return (
    <div className={cn(card, 'flex min-w-0 flex-[1_1_340px] flex-col gap-3.5 bg-[#fbfbfc] p-6')}>
      <H2 className="text-[15px]">Method</H2>
      <dl className="tnum m-0 grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-2.5 text-[12px] leading-[1.5]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-ink-3">{k}</dt>
            <dd className="m-0 min-w-0 text-pretty [overflow-wrap:anywhere]">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

// ---- page ------------------------------------------------------------------
function Toggle<T extends string>({ label, value, onChange, options }: { label: string; value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[] }) {
  return (
    <div className="flex flex-col items-end gap-1.5">
      <Eyebrow>{label}</Eyebrow>
      <div role="group" aria-label={label} className="inline-flex gap-0.5 rounded-lg bg-track p-0.5">
        {options.map((o) => (
          <button
            key={o.value}
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
            className={cn(
              'inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-[13px] font-medium transition-[background-color,color,box-shadow] duration-150 ease-out',
              value === o.value ? 'bg-surface text-ink shadow-thumb' : 'text-ink-2 hover:text-ink',
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export function ResultsView() {
  const [level, setLevel] = useState<DemandLevel>('standard')
  const [k, setK] = useState<MetricKey>('mean_wait_s')
  const [info, setInfo] = useState<RlInfo | null>(null)
  useEffect(() => {
    api.rlInfo().then(setInfo).catch(() => setInfo(null))
  }, [])
  const liveJob = useLiveJob(info)
  const { job } = liveJob
  const live = useMemo(() => (job?.status === 'done' && job.result ? fromLive(job.result) : null), [job])
  const [source, setSource] = useState<'recorded' | 'live'>(live ? 'live' : 'recorded')
  const doneId = job?.status === 'done' ? job.id : null
  useEffect(() => {
    if (doneId) setSource('live')
  }, [doneId])
  const isLive = source === 'live' && live != null
  const liveLevel: DemandLevel = live && live.intensity > 1 ? 'stress' : 'standard'
  const recorded = results.demand[isLive ? liveLevel : level]
  const d = isLive ? live : recorded
  const st = useMemo(() => story(d), [d])
  const rec = useMemo(() => (isLive ? story(recorded) : null), [isLive, recorded])
  const running = job?.status === 'queued' || job?.status === 'running'
  const rerun = () => {
    const r = job?.result?.request
    if (r) liveJob.start(r.scenarios, r.seeds.length, r.intensity)
  }
  return (
    <div className="scroll-y min-w-0 flex-1 bg-canvas">
      <PageMasthead
        word="results"
        kicker={isLive ? 'Live SUMO comparison' : `Recorded SUMO evaluation · ${st.runs} paired runs`}
        title="FlowState vs fixed-time signals"
        right={
          <div className="flex flex-wrap justify-end gap-5">
            {live && (
              <Toggle
                label="Source"
                value={source}
                onChange={setSource}
                options={[
                  { value: 'recorded', label: 'Recorded evaluation' },
                  {
                    value: 'live',
                    label: (
                      <>
                        <span className="h-1.5 w-1.5 rounded-full bg-flow" />
                        Your live run
                      </>
                    ),
                  },
                ]}
              />
            )}
            {!isLive && (
              <Toggle
                label="Demand level"
                value={level}
                onChange={setLevel}
                options={[
                  { value: 'standard', label: 'Standard peak' },
                  { value: 'stress', label: 'Over-saturated' },
                ]}
              />
            )}
          </div>
        }
      >
        <Summary d={d} st={st} live={isLive} />
      </PageMasthead>
      <main className="mx-auto flex max-w-[1312px] flex-col gap-12 px-6 pb-[72px] pt-10 min-[1100px]:px-8">
        {isLive && job ? <Receipt job={job} onBack={() => setSource('recorded')} onRerun={rerun} /> : null}

        <div className={cn(running && 'opacity-[0.72] transition-opacity')}>
          <Hero key={`${isLive ? 'live' : level}`} st={st} live={isLive} rec={rec} />
        </div>

        {info && !isLive && (
          <div className="-mt-6">
            <LiveStrip info={info} level={level} live={liveJob} />
          </div>
        )}

        {isLive && (
          <>
            <RecordedVsLive d={d} rec={recorded} recRuns={story(recorded).runs} />
            {d.seeds.length < 10 && (
              <section className="flex items-start gap-3 rounded-[10px] bg-warn-soft px-4 py-3.5 text-[#7a2e0e] shadow-[inset_0_0_0_1px_var(--color-warn-line)]">
                <Info size={16} className="mt-0.5 shrink-0 text-warn" />
                <div>
                  <b className="font-semibold">A smaller sample than the recording.</b> With {d.seeds.length} seeds per pattern the intervals are wider, so more measures may read “No clear difference”.
                  That is the verdict rule working, not a failure.
                </div>
              </section>
            )}
          </>
        )}

        <div className={cn('flex flex-col gap-12', running && 'opacity-[0.72] transition-opacity')}>
          <section className="flex flex-col gap-4">
            <div className="flex flex-wrap items-baseline justify-between gap-4">
              <H2>Headline measures</H2>
              <SignalKey />
            </div>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(232px,1fr))] gap-4">
              {HERO.map((key) => (
                <Tile key={key} k={key} d={d} />
              ))}
            </div>
          </section>

          <section className={cn(card, 'flex flex-col gap-1 px-6 pb-3 pt-6')}>
            <div className="flex flex-wrap items-start justify-between gap-4 pb-4">
              <div className="flex flex-col gap-1">
                <H2>Where it works, and where it doesn’t</H2>
                <Cap>
                  Change in {METRICS[k].label.toLowerCase()} by traffic pattern. Dot = mean change over {d.seeds.length} paired runs; bar = 95% interval.
                </Cap>
              </div>
              <Toggle label="Measure" value={k} onChange={setK} options={PATTERN_METRICS.map((x) => ({ value: x, label: METRICS[x].label }))} />
            </div>
            <PatternChart d={d} k={k} />
          </section>

          <HeadToHead d={d} />
          <AllMeasures d={d} />

          <section className="flex flex-wrap items-stretch gap-4">
            <Compared />
            <Method d={d} live={isLive} />
          </section>
        </div>
      </main>
    </div>
  )
}
