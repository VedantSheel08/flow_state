import { INTENSITY, PRESET_SHORT, effectiveValues, matchPreset, nominalRate, overriddenKeys, presetKeys, presetParams } from '../lib/intensity'
import { cn, num } from '../lib/format'
import { useStore } from '../lib/store'
import type { CongestionPreset, ScenarioSpec } from '../lib/types'
import { Badge, Tip } from './ui'

// Bars that fill up with the preset, so the scale reads at a glance.
function Level({ n, active, hot }: { n: number; active: boolean; hot: boolean }) {
  return (
    <span className="flex h-3 items-end gap-[2px]" aria-hidden>
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className={cn('w-[3px] rounded-[1px] transition-colors', i <= n ? (active ? (hot ? 'bg-[#F97066]' : 'bg-white') : hot ? 'bg-bad/70' : 'bg-ink-2') : active ? 'bg-white/25' : 'bg-black/10')}
          style={{ height: `${4 + i * 2}px` }}
        />
      ))}
    </span>
  )
}

function Pill({ label, tip, level, hot, active, onClick }: { label: string; tip: string; level: number; hot: boolean; active: boolean; onClick: () => void }) {
  return (
    <Tip side="top" label={tip}>
      <button
        role="radio"
        aria-checked={active}
        onClick={onClick}
        className={cn(
          'flex h-[52px] flex-col items-center justify-center gap-1 rounded-lg px-0.5 text-center text-[10.5px] font-semibold leading-[1.1] transition-all duration-150',
          active ? (hot ? 'bg-[#7A271A] text-white shadow-xs' : 'bg-ink text-white shadow-xs') : 'text-ink-2 hover:bg-white hover:text-ink hover:shadow-xs',
        )}
      >
        <Level n={level} active={active} hot={hot} />
        <span>{label}</span>
      </button>
    </Tip>
  )
}

function presetTip(p: CongestionPreset, scenario: string): string {
  const parts = [`${p.label}: demand ${num(p.params.demand)}/h × traffic ${Number(p.params.traffic ?? 1).toFixed(2)}`]
  if (scenario === 'rush_hour' && p.rushHourPeak != null) parts.push(`peak ${p.rushHourPeak}×`)
  parts.push(`warm-up ${p.warmup} s`)
  if (p.stressTest) parts.push('stress test')
  return parts.join(' · ')
}

/**
 * Traffic intensity for the next run.
 *
 * With a backend that serves GET /api/congestion-presets, the pills are those
 * presets and the run is started with `congestion: <id>`; the backend owns the
 * mapping. Older backends fall back to writing the existing `demand`/`traffic`
 * parameters directly. Either way, no traffic is generated client-side.
 */
export function IntensitySelector({ spec, running }: { spec: ScenarioSpec; running: boolean }) {
  const presets = useStore((s) => s.congestionPresets)
  const selected = useStore((s) => s.congestion[spec.id] ?? null)
  const draft = useStore((s) => s.drafts[spec.id])
  const setCongestion = useStore((s) => s.setCongestion)
  const setDraft = useStore((s) => s.setDraft)
  const backend = !!presets && presets.length > 0
  const preset = backend ? (presets!.find((p) => p.id === selected) ?? null) : null
  const values = effectiveValues(spec, draft, preset)
  if (!backend && !presetParams(spec, 'normal')) return null
  const rate = nominalRate(values)
  const overridden = overriddenKeys(spec, draft, preset).filter((k) => k !== 'duration')
  const legacy = backend ? null : matchPreset(spec, values)

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[12.5px] font-medium text-ink">Traffic intensity</span>
        <span className="tnum text-[11.5px] text-ink-3">
          {backend ? (preset ? '' : 'Scenario default · ') : legacy ? '' : 'Custom · '}
          {num(rate)} trips/h
        </span>
      </div>
      <div className="grid grid-cols-5 gap-1 rounded-xl border border-black/[0.06] bg-black/[0.03] p-1" role="radiogroup" aria-label="Traffic intensity">
        {backend
          ? presets!.slice(0, 5).map((p, i) => (
              <Pill
                key={p.id}
                label={PRESET_SHORT[p.id] ?? p.label}
                tip={presetTip(p, spec.id)}
                level={i}
                hot={p.stressTest}
                active={selected === p.id}
                // clicking the active preset returns to the scenario's own defaults
                onClick={() => setCongestion(spec.id, selected === p.id ? null : p.id, presetKeys(p, spec.id).filter((k) => k !== 'duration'))}
              />
            ))
          : INTENSITY.map((p, i) => {
              const pp = presetParams(spec, p.id)!
              return (
                <Pill
                  key={p.id}
                  label={p.label}
                  tip={`${p.hint} · demand ${num(pp.demand)}/h${pp.traffic !== undefined ? ` × traffic ${pp.traffic.toFixed(2)}` : ''}`}
                  level={i}
                  hot={i >= 3}
                  active={legacy === p.id}
                  onClick={() => {
                    for (const [k, v] of Object.entries(pp)) setDraft(spec.id, k, v)
                  }}
                />
              )
            })}
      </div>
      {preset && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <Badge>Backend preset v{preset.version}</Badge>
          <Badge>Warm-up {preset.warmup} s</Badge>
          {preset.stressTest && <Badge tone="bad">Stress test</Badge>}
          {!preset.calibrated && <Badge tone="warn">Not calibrated</Badge>}
          {!preset.safetyValidated && <Badge tone="warn">Safety not validated</Badge>}
        </div>
      )}
      <p className="mt-1.5 text-[11px] leading-snug text-ink-3">
        {preset
          ? `${preset.description ?? 'Modeled load; measure actual departures and queues.'}${overridden.length ? ` Your edits to ${overridden.join(', ')} override the preset.` : ''}`
          : backend
            ? 'Pick a modeled load from the simulation server, or keep the scenario’s own demand.'
            : `Sets SUMO’s background demand${spec.params.some((q) => q.key === 'traffic') ? ' and traffic multiplier' : ''} for the next run (this server has no preset API).`}
        {running ? ' The running simulation keeps its demand; use Live control → Traffic level to change it now.' : ''}
      </p>
    </div>
  )
}
