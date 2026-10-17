import { Ambulance, Bike, BusFront, ChevronDown, CloudRain, Construction, Dices, Gauge, Play, RotateCcw, School, Shuffle, Siren, Ticket, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { launch } from '../lib/actions'
import { cn } from '../lib/format'
import { paramsFor, useStore } from '../lib/store'
import { ParamField } from './ParamField'
import { Button, Tip } from './ui'

export const ICONS: Record<string, LucideIcon> = {
  rush_hour: Gauge,
  accident: TriangleAlert,
  ambulance: Ambulance,
  pursuit: Siren,
  bike_lanes: Bike,
  construction: Construction,
  stadium: Ticket,
  school: School,
  transit: BusFront,
  weather: CloudRain,
  disruption: Shuffle,
}

export function ScenarioRail() {
  const scenarios = useStore((s) => s.scenarios)
  const scenarioId = useStore((s) => s.scenarioId)
  const drafts = useStore((s) => s.drafts)
  const set = useStore((s) => s.set)
  const setDraft = useStore((s) => s.setDraft)
  const resetDraft = useStore((s) => s.resetDraft)
  const seed = useStore((s) => s.seed)
  const status = useStore((s) => s.status)
  const conn = useStore((s) => s.connection)
  const networks = useStore((s) => s.networks)
  const [showSim, setShowSim] = useState(false)
  const spec = scenarios.find((s) => s.id === scenarioId)
  const values = useMemo(() => (spec ? paramsFor(spec, drafts[spec.id]) : {}), [spec, drafts])
  const scenarioParams = spec?.params.filter((p) => p.group !== 'Simulation') ?? []
  const simParams = spec?.params.filter((p) => p.group === 'Simulation') ?? []
  const busy = status.state === 'starting'
  const net = networks[(values.network as string) ?? 'district'] ?? networks.district
  const edited = Object.keys(drafts[scenarioId] ?? {}).length > 0

  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-r border-border bg-surface">
      <div className="scroll-y min-h-0 flex-1">
        <div className="px-4 pb-2 pt-3.5">
          <h2 className="label-caps">Scenarios</h2>
        </div>
        <ul className="px-2 pb-2">
          {scenarios.map((s) => {
            const Icon = ICONS[s.id] ?? Gauge
            const active = s.id === scenarioId
            return (
              <li key={s.id}>
                <button
                  onClick={() => set({ scenarioId: s.id, pick: null })}
                  className={cn('group flex w-full items-center gap-2.5 rounded-md px-2 py-[7px] text-left transition-colors', active ? 'bg-muted' : 'hover:bg-subtle')}
                >
                  <span className={cn('flex h-7 w-7 shrink-0 items-center justify-center rounded-md border', active ? 'border-border-strong bg-surface text-ink shadow-xs' : 'border-transparent text-ink-3 group-hover:text-ink-2')}>
                    <Icon size={15} strokeWidth={1.8} />
                  </span>
                  <span className="min-w-0">
                    <span className={cn('block truncate text-[13px] leading-tight', active ? 'font-semibold text-ink' : 'font-medium text-ink-2')}>{s.title}</span>
                    <span className="block truncate text-[11.5px] leading-tight text-ink-3">{s.tagline}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>

        {spec && (
          <div className="border-t border-border">
            <div className="px-4 pb-3 pt-3.5">
              <div className="flex items-center justify-between">
                <h2 className="label-caps">Configure</h2>
                {edited && (
                  <button className="flex items-center gap-1 text-[11.5px] font-medium text-ink-3 hover:text-ink" onClick={() => resetDraft(spec.id)}>
                    <RotateCcw size={11} /> Defaults
                  </button>
                )}
              </div>
              <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">{spec.description}</p>
            </div>
            <div className="space-y-3.5 px-4 pb-4">
              {scenarioParams.map((p) => (
                <ParamField key={p.key} spec={p} value={values[p.key]} onChange={(v) => setDraft(spec.id, p.key, v)} net={net} />
              ))}
            </div>
            <div className="border-t border-border">
              <button className="flex w-full items-center justify-between px-4 py-2.5 text-left hover:bg-subtle" onClick={() => setShowSim((v) => !v)}>
                <span className="label-caps">Simulation settings</span>
                <span className="flex items-center gap-2 text-[11.5px] text-ink-3">
                  {values.duration as number} min · {String(values.signals) === 'actuated' ? 'actuated' : 'fixed-time'}
                  <ChevronDown size={14} className={cn('transition-transform', showSim && 'rotate-180')} />
                </span>
              </button>
              {showSim && (
                <div className="anim-in space-y-3.5 px-4 pb-4 pt-1">
                  {simParams.map((p) => (
                    <ParamField key={p.key} spec={p} value={values[p.key]} onChange={(v) => setDraft(spec.id, p.key, v)} net={net} />
                  ))}
                </div>
              )}
            </div>
            {spec.assumptions.length > 0 && (
              <div className="border-t border-border px-4 py-3">
                <div className="label-caps mb-1">Model assumptions</div>
                <ul className="space-y-1 text-[11.5px] leading-snug text-ink-3">
                  {spec.assumptions.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="border-t border-border bg-surface px-4 py-3">
        <div className="mb-2.5 flex items-center gap-2">
          <label htmlFor="seed" className="text-[12px] font-medium text-ink-2">
            Seed
          </label>
          <input
            id="seed"
            value={seed}
            onChange={(e) => set({ seed: e.target.value.replace(/[^0-9]/g, '').slice(0, 9) })}
            placeholder="Random"
            className="tnum h-7 min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-2 text-[12.5px] text-ink shadow-xs outline-none placeholder:text-ink-3 focus:border-accent"
          />
          <Tip label="Draw a seed">
            <Button size="icon-sm" variant="secondary" aria-label="Draw a random seed" onClick={() => set({ seed: String(Math.floor(Math.random() * 99999) + 1) })}>
              <Dices size={14} />
            </Button>
          </Tip>
        </div>
        <Button variant="primary" className="h-9 w-full" disabled={!spec || busy || conn !== 'open'} onClick={() => launch()}>
          <Play size={14} fill="currentColor" />
          {busy ? 'Generating…' : 'Generate & Run'}
        </Button>
        <p className="mt-2 text-center text-[11px] leading-snug text-ink-3">{seed ? 'Same seed and settings reproduce the run exactly.' : 'A new seed draws a new variation.'}</p>
      </div>
    </aside>
  )
}
