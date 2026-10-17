// Scenario configuration in a modal: intensity, scenario parameters, simulation
// settings and model assumptions, so the scenario rail stays a short list.

import * as Dialog from '@radix-ui/react-dialog'
import { Gauge, Play, RotateCcw, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { launch } from '../lib/actions'
import { cn } from '../lib/format'
import { effectiveValues } from '../lib/intensity'
import { useStore } from '../lib/store'
import type { ParamSpec } from '../lib/types'
import { IntensitySelector } from './IntensitySelector'
import { ParamField } from './ParamField'
import { ICONS } from './ScenarioRail'
import { Button, Segmented } from './ui'

type Tab = 'scenario' | 'simulation' | 'assumptions'

// toggles and map pickers carry a description or a button row, so they take the full width
const wide = (p: ParamSpec) => p.type === 'bool' || p.type === 'edge'

export function ConfigDialog() {
  const scenarios = useStore((s) => s.scenarios)
  const scenarioId = useStore((s) => s.scenarioId)
  const drafts = useStore((s) => s.drafts)
  const setDraft = useStore((s) => s.setDraft)
  const resetDraft = useStore((s) => s.resetDraft)
  const set = useStore((s) => s.set)
  const open = useStore((s) => s.configOpen)
  const pick = useStore((s) => s.pick)
  const reopen = useStore((s) => s.reopenConfigAfterPick)
  const status = useStore((s) => s.status)
  const conn = useStore((s) => s.connection)
  const networks = useStore((s) => s.networks)
  const presets = useStore((s) => s.congestionPresets)
  const [tab, setTab] = useState<Tab>('scenario')
  const spec = scenarios.find((s) => s.id === scenarioId)
  const congestionId = useStore((s) => (spec ? (s.congestion[spec.id] ?? null) : null))
  const preset = presets?.find((p) => p.id === congestionId) ?? null
  const values = useMemo(() => (spec ? effectiveValues(spec, drafts[spec.id], preset) : {}), [spec, drafts, preset])
  const net = networks[(values.network as string) ?? 'district'] ?? networks.district
  const edited = Object.keys(drafts[scenarioId] ?? {}).length > 0
  const busy = status.state === 'starting'

  // a map pick needs the map: step aside while it runs, come back when it ends
  useEffect(() => {
    if (pick && open) set({ configOpen: false, reopenConfigAfterPick: true })
    else if (!pick && reopen) set({ configOpen: true, reopenConfigAfterPick: false })
  }, [pick, open, reopen, set])

  if (!spec) return null
  const Icon = ICONS[spec.id] ?? Gauge
  const scenarioParams = spec.params.filter((p) => p.group !== 'Simulation')
  const simParams = spec.params.filter((p) => p.group === 'Simulation')
  const fields = (ps: ParamSpec[]) => (
    <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
      {ps.map((p) => (
        <div key={p.key} className={cn(wide(p) && 'sm:col-span-2')}>
          <ParamField spec={p} value={values[p.key]} onChange={(v) => setDraft(spec.id, p.key, v)} net={net} />
        </div>
      ))}
    </div>
  )

  return (
    <Dialog.Root open={open} onOpenChange={(o) => set({ configOpen: o })}>
      <Dialog.Portal>
        <Dialog.Overlay className="anim-fade fixed inset-0 z-40 bg-[rgba(16,24,40,0.28)]" />
        <Dialog.Content className="anim-in fixed left-1/2 top-1/2 z-50 flex max-h-[min(84vh,760px)] w-[min(600px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-float outline-none">
          <div className="flex items-start gap-3 border-b border-border px-5 pb-3.5 pt-4">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-ink text-white shadow-xs">
              <Icon size={17} strokeWidth={1.8} />
            </span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-[15px] font-semibold text-ink">Configure · {spec.title}</Dialog.Title>
              <Dialog.Description className="mt-0.5 text-[12px] leading-relaxed text-ink-2">{spec.description}</Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button size="icon-sm" variant="ghost" aria-label="Close">
                <X size={15} />
              </Button>
            </Dialog.Close>
          </div>

          <div className="flex items-center justify-between gap-3 px-5 pt-3">
            <Segmented
              value={tab}
              onChange={setTab}
              options={[
                { value: 'scenario', label: 'Scenario' },
                { value: 'simulation', label: 'Simulation' },
                ...(spec.assumptions.length ? [{ value: 'assumptions' as Tab, label: 'Assumptions' }] : []),
              ]}
            />
            {edited && (
              <button className="flex items-center gap-1 text-[11.5px] font-medium text-ink-3 hover:text-ink" onClick={() => resetDraft(spec.id)}>
                <RotateCcw size={11} /> Reset to defaults
              </button>
            )}
          </div>

          <div className="scroll-y min-h-0 flex-1 px-5 pb-5 pt-4">
            {tab === 'scenario' && (
              <div className="space-y-5">
                <IntensitySelector spec={spec} running={status.state === 'running' || status.state === 'warming'} />
                {scenarioParams.length > 0 && fields(scenarioParams)}
              </div>
            )}
            {tab === 'simulation' && fields(simParams)}
            {tab === 'assumptions' && (
              <ul className="list-disc space-y-1.5 pl-4 text-[12.5px] leading-snug text-ink-2">
                {spec.assumptions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-border bg-subtle px-5 py-3">
            <Dialog.Close asChild>
              <Button variant="secondary">Done</Button>
            </Dialog.Close>
            <Button
              variant="primary"
              disabled={busy || conn !== 'open'}
              onClick={() => {
                set({ configOpen: false })
                launch()
              }}
            >
              <Play size={13} fill="currentColor" />
              {busy ? 'Generating…' : 'Generate & Run'}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
