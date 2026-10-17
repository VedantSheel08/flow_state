import { Play, Repeat, SlidersHorizontal } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { ICONS } from '../components/ScenarioRail'
import { Button, Empty, Tip } from '../components/ui'
import { launch } from '../lib/actions'
import { api } from '../lib/api'
import { clock, num, secs } from '../lib/format'
import { useStore } from '../lib/store'
import type { RunRecord } from '../lib/types'

export function RunsView() {
  const [runs, setRuns] = useState<RunRecord[] | null>(null)
  const set = useStore((s) => s.set)
  const scenarios = useStore((s) => s.scenarios)
  useEffect(() => {
    api.runs().then(setRuns).catch(() => setRuns([]))
  }, [])
  return (
    <div className="scroll-y min-w-0 flex-1 bg-canvas">
      <div className="mx-auto flex max-w-[1312px] flex-col gap-8 px-6 pb-16 pt-10 min-[1100px]:px-8">
        <PageHeader
          title="Run history"
          right={runs && runs.length > 0 ? <span className="tnum text-[13px] text-ink-3">{runs.length} {runs.length === 1 ? 'run' : 'runs'} stored</span> : undefined}
        >
          Every run keeps its scenario, settings and seed. Reproduce one to regenerate the identical traffic in SUMO.
        </PageHeader>
        <div className="overflow-x-auto rounded-xl border border-transparent bg-surface shadow-card">
          {runs === null && (
            <div className="flex flex-col gap-3 p-5" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-11 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />
              ))}
            </div>
          )}
          {runs && !runs.length && (
            <Empty title="No runs yet">
              Each Generate &amp; Run is saved here with its seed, so you can replay it exactly.
              <div className="mt-4">
                <Button variant="primary" onClick={() => set({ view: 'simulate' })}>
                  <Play size={13} fill="currentColor" /> Open Simulation
                </Button>
              </div>
            </Empty>
          )}
          {runs && runs.length > 0 && (
            <table className="w-full min-w-[880px] text-[13px]">
              <thead className="sticky top-0 bg-subtle">
                <tr className="text-left text-[12px] font-medium text-ink-3">
                  <th className="px-5 py-3 font-medium">Scenario</th>
                  <th className="px-3 py-3 font-medium">Generated variation</th>
                  <th className="px-3 py-3 text-right font-medium">Seed</th>
                  <th className="px-3 py-3 text-right font-medium">Simulated</th>
                  <th className="px-3 py-3 text-right font-medium">Trips</th>
                  <th className="px-3 py-3 text-right font-medium">Delay / trip</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const Icon = ICONS[r.scenario]
                  return (
                    <tr key={r.id} className="border-t border-border align-middle transition-colors duration-150 hover:bg-subtle">
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-ink-2">{Icon && <Icon size={15} strokeWidth={1.8} />}</span>
                          <span className="min-w-0">
                            <span className="block font-semibold text-ink">{r.title}</span>
                            <span className="tnum block text-[12px] text-ink-3">{new Date(r.created * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</span>
                          </span>
                        </div>
                      </td>
                      <td className="max-w-[380px] px-3 py-3.5 text-[12px] leading-[1.5] text-ink-2">
                        {r.briefing.slice(0, 2).map((b, i) => (
                          <div key={i} className="truncate">
                            <span className="text-ink-3">{b.label}: </span>
                            {b.value}
                            {b.detail ? ` — ${b.detail}` : ''}
                          </div>
                        ))}
                      </td>
                      <td className="tnum px-3 py-3.5 text-right font-semibold text-ink">{r.seed}</td>
                      <td className="tnum px-3 py-3.5 text-right text-ink-2">{r.summary.t != null ? clock(r.summary.t) : '—'}</td>
                      <td className="tnum px-3 py-3.5 text-right text-ink-2">{num(r.summary.completed)}</td>
                      <td className="tnum px-3 py-3.5 text-right text-ink-2">{secs(r.summary.timeLoss)}</td>
                      <td className="px-5 py-3.5">
                        <div className="flex justify-end gap-1.5">
                          <Tip label="Load these settings into the configuration panel">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                const spec = scenarios.find((s) => s.id === r.scenario)
                                if (!spec) return
                                set({ scenarioId: r.scenario, seed: String(r.seed), view: 'simulate', drafts: { ...useStore.getState().drafts, [r.scenario]: r.params } })
                              }}
                            >
                              <SlidersHorizontal size={13} /> Settings
                            </Button>
                          </Tip>
                          <Button size="sm" onClick={() => launch({ scenario: r.scenario, params: r.params, seed: r.seed })}>
                            <Repeat size={13} /> Reproduce
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
