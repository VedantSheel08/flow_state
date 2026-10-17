import { Repeat, SlidersHorizontal } from 'lucide-react'
import { useEffect, useState } from 'react'
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
      <div className="mx-auto max-w-[1180px] px-6 py-5">
        <div className="label-caps">Run history</div>
        <h1 className="mt-0.5 text-[20px] font-semibold tracking-[-0.01em] text-ink">Generated runs</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-2">Every run is stored with its scenario, parameters and seed. Reproducing a run regenerates the identical situation in SUMO.</p>
        <div className="mt-4 overflow-hidden rounded-lg border border-border bg-surface">
          {runs && !runs.length && <Empty title="No runs yet">Runs appear here after you press Generate &amp; Run.</Empty>}
          {runs && runs.length > 0 && (
            <table className="w-full text-[12.5px]">
              <thead className="bg-subtle">
                <tr className="text-left text-[11.5px] text-ink-3">
                  <th className="px-4 py-2.5 font-normal">Scenario</th>
                  <th className="px-3 py-2.5 font-normal">Generated variation</th>
                  <th className="px-3 py-2.5 text-right font-normal">Seed</th>
                  <th className="px-3 py-2.5 text-right font-normal">Simulated</th>
                  <th className="px-3 py-2.5 text-right font-normal">Trips</th>
                  <th className="px-3 py-2.5 text-right font-normal">Delay / trip</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const Icon = ICONS[r.scenario]
                  return (
                    <tr key={r.id} className="border-t border-border align-top hover:bg-subtle">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {Icon && <Icon size={14} className="text-ink-3" />}
                          <span className="font-medium text-ink">{r.title}</span>
                        </div>
                        <div className="mt-0.5 text-[11.5px] text-ink-3">{new Date(r.created * 1000).toLocaleString()}</div>
                      </td>
                      <td className="max-w-[380px] px-3 py-3 text-[12px] text-ink-2">
                        {r.briefing.slice(0, 2).map((b, i) => (
                          <div key={i} className="truncate">
                            <span className="text-ink-3">{b.label}: </span>
                            {b.value}
                            {b.detail ? ` — ${b.detail}` : ''}
                          </div>
                        ))}
                      </td>
                      <td className="tnum px-3 py-3 text-right font-medium text-ink">{r.seed}</td>
                      <td className="tnum px-3 py-3 text-right">{r.summary.t != null ? clock(r.summary.t) : '—'}</td>
                      <td className="tnum px-3 py-3 text-right">{num(r.summary.completed)}</td>
                      <td className="tnum px-3 py-3 text-right">{secs(r.summary.timeLoss)}</td>
                      <td className="px-4 py-3">
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
