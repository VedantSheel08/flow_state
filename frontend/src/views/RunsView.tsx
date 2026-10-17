import { Play, Repeat, SlidersHorizontal } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { PageMasthead } from '../components/PageHeader'
import { ICONS } from '../components/ScenarioRail'
import { Button, Empty, Tip } from '../components/ui'
import { InteractiveLogsTable, type LogEntry } from '../components/ui/interactive-logs-table-shadcnui'
import { launch } from '../lib/actions'
import { api } from '../lib/api'
import { clock, num, secs } from '../lib/format'
import { useStore } from '../lib/store'
import type { RunRecord } from '../lib/types'

type Run = RunRecord & { congestionPreset?: { label?: string } }

/** Did the run reach the end of its scenario clock? */
function outcome(r: Run): LogEntry['level'] {
  const t = r.summary.t
  const dur = Number(r.params.duration ?? 0) * 60
  if (t == null) return { key: 'nodata', label: 'No data', tone: 'muted' }
  if (!dur || t >= dur - 2) return { key: 'complete', label: 'Complete', tone: 'neutral' }
  return { key: 'ended', label: 'Ended early', tone: 'warn' }
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11.5px] font-medium text-ink-3">{label}</span>
      <span className="tnum text-[15px] font-semibold text-ink">{children}</span>
    </div>
  )
}

function RunDetails({ r, onSettings }: { r: Run; onSettings: () => void }) {
  const Icon = ICONS[r.scenario]
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
        <div className="flex min-w-[260px] flex-[2_1_360px] flex-col gap-2.5">
          {r.briefing.map((b, i) => (
            <div key={i} className="text-[13px] leading-[1.5]">
              <span className="font-semibold text-ink">{b.label}</span>
              <span className="text-ink-2">
                {' '}
                {b.value}
                {b.detail ? `. ${b.detail}` : ''}
              </span>
            </div>
          ))}
        </div>
        <div className="grid flex-[1_1_300px] grid-cols-[repeat(auto-fit,minmax(88px,1fr))] gap-4">
          <Stat label="Simulated">{r.summary.t != null ? clock(r.summary.t) : '—'}</Stat>
          <Stat label="Trips">{num(r.summary.completed)}</Stat>
          <Stat label="Delay / trip">{secs(r.summary.timeLoss)}</Stat>
          <Stat label="Seed">{r.seed}</Stat>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Tip label="Load these settings into the configuration panel">
          <Button size="sm" variant="secondary" onClick={onSettings}>
            <SlidersHorizontal size={13} /> Load settings
          </Button>
        </Tip>
        <Tip label="Regenerate the identical traffic in SUMO">
          <Button size="sm" variant="primary" onClick={() => launch({ scenario: r.scenario, params: r.params, seed: r.seed })}>
            <Repeat size={13} /> Reproduce
          </Button>
        </Tip>
        <span className="tnum ml-auto flex items-center gap-1.5 text-[12px] text-ink-3">
          {Icon && <Icon size={13} />}
          {new Date(r.created * 1000).toLocaleString([], { dateStyle: 'full', timeStyle: 'medium' })}
        </span>
      </div>
    </div>
  )
}

export function RunsView() {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const set = useStore((s) => s.set)
  const scenarios = useStore((s) => s.scenarios)
  useEffect(() => {
    api
      .runs()
      .then((r) => setRuns(r as Run[]))
      .catch(() => setRuns([]))
  }, [])

  const logs = useMemo<LogEntry[]>(
    () =>
      (runs ?? []).map((r) => {
        const settings = () => {
          if (!scenarios.find((s) => s.id === r.scenario)) return
          set({ scenarioId: r.scenario, seed: String(r.seed), view: 'simulate', drafts: { ...useStore.getState().drafts, [r.scenario]: r.params } })
        }
        const b = r.briefing[0]
        return {
          id: r.id,
          time: new Date(r.created * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }),
          level: outcome(r),
          service: r.title,
          message: b ? `${b.label}: ${b.value}${b.detail ? `. ${b.detail}` : ''}` : 'No briefing recorded',
          status: r.signalMode === 'actuated' ? 'Actuated' : 'Fixed-time',
          duration: r.summary.t != null ? clock(r.summary.t) : '—',
          tags: [`seed ${r.seed}`, `${r.params.duration ?? '?'} min`, r.congestionPreset?.label, r.network].filter((t): t is string => !!t),
          search: `${r.seed} ${r.briefing.map((x) => `${x.value} ${x.detail ?? ''}`).join(' ')}`,
          details: <RunDetails r={r} onSettings={settings} />,
        }
      }),
    [runs, scenarios, set],
  )

  return (
    <div className="scroll-y min-w-0 flex-1 bg-canvas">
      <PageMasthead word="history" kicker={runs && runs.length > 0 ? `Archive · ${runs.length} ${runs.length === 1 ? 'run' : 'runs'} stored` : 'Archive'} title="Run history">
        Every run keeps its scenario, settings and seed. Open one to see its briefing, or reproduce it to regenerate the identical traffic in SUMO.
      </PageMasthead>
      <div className="mx-auto flex max-w-[1312px] flex-col gap-8 px-6 pb-16 pt-8 min-[1100px]:px-8">
        {runs === null && (
          <div className="asphalt flex flex-col gap-3 rounded-[18px] p-5" aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-11 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />
            ))}
          </div>
        )}
        {runs && !runs.length && (
          <div className="rounded-xl border border-transparent bg-surface shadow-card">
            <Empty title="No runs yet">
              Each Generate &amp; Run is saved here with its seed, so you can replay it exactly.
              <div className="mt-4">
                <Button variant="primary" onClick={() => set({ view: 'simulate' })}>
                  <Play size={13} fill="currentColor" /> Open Simulation
                </Button>
              </div>
            </Empty>
          </div>
        )}
        {runs && runs.length > 0 && (
          <InteractiveLogsTable
            logs={logs}
            title="Generated runs"
            noun="runs"
            placeholder="Search by scenario, seed, street or event…"
            filterTitles={{ level: 'Outcome', service: 'Scenario', status: 'Signals' }}
            className="asphalt rounded-[18px] shadow-[0_1px_2px_rgba(12,19,34,0.12),0_18px_44px_-14px_rgba(12,19,34,0.45)]"
          />
        )}
      </div>
    </div>
  )
}
