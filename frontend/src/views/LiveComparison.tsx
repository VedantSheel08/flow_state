// Live SUMO comparison: runs both controllers on fresh seeds through the backend.
// Not rendered yet; the Results tab shows a placeholder until this is wired back in.
import { ChevronDown, CircleCheck, Info, Loader2, Play, RotateCcw, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { METRICS, results, verdict, type DemandLevel, type DemandResults, type RlInfo, type RlJob } from '../lib/controllerResults'
import { cn } from '../lib/format'
import { useStore } from '../lib/store'
import { FS, HERO, card, signed } from '../lib/resultsTheme'
import { Cap, H2, Pill, Td, Th, VerdictBadge } from './resultsUi'

const startedAt = new Map<string, number>()
const SEED_OPTIONS = [3, 6]

export function useLiveJob(info: RlInfo | null) {
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

export function LiveStrip({ info, level, live }: { info: RlInfo; level: DemandLevel; live: ReturnType<typeof useLiveJob> }) {
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
          <span>
            <b className="text-[20px] font-[650] tracking-[-0.02em]">{done}</b>
            <span className="text-[14px] text-ink-3"> / {total} runs</span>
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

export function Receipt({ job, onBack, onRerun }: { job: RlJob; onBack: () => void; onRerun: () => void }) {
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

export function RecordedVsLive({ d, rec, recRuns }: { d: DemandResults; rec: DemandResults; recRuns: number }) {
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
