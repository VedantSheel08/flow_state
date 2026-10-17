import { Ambulance, Bike, BusFront, CloudRain, Construction, Dices, Gauge, PanelLeftClose, PanelLeftOpen, Play, School, Shuffle, Siren, SlidersHorizontal, Ticket, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useMemo } from 'react'
import { launch } from '../lib/actions'
import { cn, num } from '../lib/format'
import { effectiveValues, nominalRate } from '../lib/intensity'
import { useStore } from '../lib/store'
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

/** Floating scenario picker over the map; configuration lives in ConfigDialog. */
export function ScenarioRail() {
  const scenarios = useStore((s) => s.scenarios)
  const scenarioId = useStore((s) => s.scenarioId)
  const drafts = useStore((s) => s.drafts)
  const set = useStore((s) => s.set)
  const seed = useStore((s) => s.seed)
  const status = useStore((s) => s.status)
  const conn = useStore((s) => s.connection)
  const open = useStore((s) => s.panels.left)
  const togglePanel = useStore((s) => s.togglePanel)
  const spec = scenarios.find((s) => s.id === scenarioId)
  const presets = useStore((s) => s.congestionPresets)
  const congestionId = useStore((s) => (spec ? (s.congestion[spec.id] ?? null) : null))
  const preset = presets?.find((p) => p.id === congestionId) ?? null
  // what the run will actually use: scenario defaults, then the selected preset, then the user's edits
  const values = useMemo(() => (spec ? effectiveValues(spec, drafts[spec.id], preset) : {}), [spec, drafts, preset])
  const busy = status.state === 'starting'
  const edited = Object.keys(drafts[scenarioId] ?? {}).length > 0
  const rate = nominalRate(values)
  const configure = () => set({ configOpen: true })

  if (!open) {
    return (
      <aside className="glass anim-fade absolute bottom-[80px] left-3 top-3 z-20 flex w-[52px] flex-col items-center rounded-xl py-2" aria-label="Scenarios (collapsed)">
        <Tip label="Show scenarios ( [ )" side="right">
          <Button size="icon" variant="ghost" aria-label="Show scenario panel" onClick={() => togglePanel('left', true)}>
            <PanelLeftOpen size={16} />
          </Button>
        </Tip>
        <div className="my-2 h-px w-7 bg-black/[0.08]" />
        <ul className="scroll-y flex min-h-0 w-full flex-1 flex-col items-center gap-1 [scrollbar-gutter:auto]! [scrollbar-width:none]!">
          {scenarios.map((s) => {
            const Icon = ICONS[s.id] ?? Gauge
            const active = s.id === scenarioId
            return (
              <li key={s.id}>
                <Tip label={`${s.title} · ${s.tagline}`} side="right">
                  <button
                    aria-label={s.title}
                    onClick={() => set({ scenarioId: s.id, pick: null })}
                    className={cn('flex h-9 w-9 items-center justify-center rounded-lg transition-colors', active ? 'bg-ink text-white shadow-xs' : 'text-ink-3 hover:bg-black/[0.05] hover:text-ink')}
                  >
                    <Icon size={16} strokeWidth={1.8} />
                  </button>
                </Tip>
              </li>
            )
          })}
        </ul>
        <div className="my-2 h-px w-7 bg-black/[0.08]" />
        <Tip label="Configure scenario" side="right">
          <Button size="icon" variant="ghost" className="relative" aria-label="Configure scenario" disabled={!spec} onClick={configure}>
            <SlidersHorizontal size={15} />
            {edited && <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-accent" />}
          </Button>
        </Tip>
        <Tip label={spec ? `Generate & run · ${spec.title}` : 'Generate & run'} side="right">
          <Button variant="primary" size="icon" className="mt-1.5 h-9 w-9 rounded-lg" aria-label="Generate and run" disabled={!spec || busy || conn !== 'open'} onClick={() => launch()}>
            <Play size={14} fill="currentColor" />
          </Button>
        </Tip>
      </aside>
    )
  }

  return (
    <aside className="glass anim-fade absolute bottom-[80px] left-3 top-3 z-20 flex w-[248px] flex-col overflow-hidden rounded-xl" aria-label="Scenarios">
      <div className="flex items-center justify-between py-1.5 pl-3.5 pr-1.5">
        <h2 className="label-caps">Scenarios</h2>
        <Tip label="Collapse panel ( [ )" side="right">
          <Button size="icon-sm" variant="ghost" aria-label="Collapse scenario panel" onClick={() => togglePanel('left', false)}>
            <PanelLeftClose size={15} />
          </Button>
        </Tip>
      </div>
      <ul className="scroll-y min-h-0 flex-1 px-1.5 pb-1.5">
        {scenarios.map((s) => {
          const Icon = ICONS[s.id] ?? Gauge
          const active = s.id === scenarioId
          return (
            <li key={s.id}>
              <button
                onClick={() => set({ scenarioId: s.id, pick: null })}
                className={cn('group flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors', active ? 'bg-white shadow-xs' : 'hover:bg-black/[0.04]')}
              >
                <span
                  className={cn(
                    'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border transition-colors',
                    active ? 'border-transparent bg-ink text-white shadow-xs' : 'border-black/[0.08] bg-white/70 text-ink-3 group-hover:text-ink-2',
                  )}
                >
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

      <div className="space-y-2.5 border-t border-black/[0.07] bg-white/60 px-3 py-3">
        {spec && (
          <button
            onClick={configure}
            className="group flex w-full items-center gap-2.5 rounded-lg border border-accent/30 bg-accent-soft px-2.5 py-2 text-left transition-colors hover:border-accent/60"
          >
            <SlidersHorizontal size={15} className="shrink-0 text-accent-strong" />
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5 text-[12.5px] font-semibold text-accent-strong">
                Configure scenario
                {edited && <span className="h-1.5 w-1.5 rounded-full bg-accent" title="Edited" />}
              </span>
              <span className="tnum block truncate text-[11px] text-ink-2">
                {rate > 0 && `${num(rate, 0)} trips/h · `}
                {values.duration as number} min · {String(values.signals) === 'actuated' ? 'actuated' : 'fixed-time'}
              </span>
            </span>
          </button>
        )}
        <div className="flex items-center gap-2">
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
        <p className="text-center text-[11px] leading-snug text-ink-3">{seed ? 'Same seed and settings reproduce the run.' : 'A new seed draws a new variation.'}</p>
      </div>
    </aside>
  )
}
