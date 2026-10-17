// Traffic intensity presets. Each preset is expressed purely in the backend's
// own scenario parameters:
//   demand  — background trips per hour entering or starting in the district (set at run start)
//   traffic — live multiplier on all background demand (SUMO spawns trips as Poisson arrivals at demand × traffic)
// Scenario components (commuter peak, event surges, weather) still shape demand on top.
// Each scenario's starting settings (lib/scenarioPresets) sit between its defaults and the preset.

import { scenarioParams } from './scenarioPresets'
import type { CongestionPreset, ParamSpec, ScenarioSpec } from './types'

export type IntensityId = 'light' | 'normal' | 'rush' | 'severe' | 'gridlock'

export interface IntensityPreset {
  id: IntensityId
  label: string
  hint: string
}

export const INTENSITY: IntensityPreset[] = [
  { id: 'light', label: 'Light', hint: 'Half the scenario’s base demand' },
  { id: 'normal', label: 'Normal', hint: 'The scenario’s calibrated base demand' },
  { id: 'rush', label: 'Rush Hour', hint: '1.5× base demand' },
  { id: 'severe', label: 'Severe', hint: 'Maximum base demand the backend accepts' },
  { id: 'gridlock', label: 'Gridlock', hint: 'Maximum demand with the traffic multiplier at its maximum' },
]

function clampStep(spec: ParamSpec, v: number): number {
  const step = spec.step ?? 1
  const lo = spec.min ?? -Infinity
  const hi = spec.max ?? Infinity
  return Math.min(hi, Math.max(lo, Math.round(v / step) * step))
}

/** Parameter values for a preset, or null when the scenario lacks a demand parameter. */
export function presetParams(spec: ScenarioSpec, id: IntensityId): Record<string, number> | null {
  const demand = spec.params.find((p) => p.key === 'demand')
  if (!demand || typeof demand.default !== 'number') return null
  const traffic = spec.params.find((p) => p.key === 'traffic')
  const base = demand.default
  const dMax = demand.max ?? base
  const tMax = traffic?.max ?? 1
  const out: Record<string, number> = {}
  const d = { light: base * 0.5, normal: base, rush: base * 1.5, severe: dMax, gridlock: dMax }[id]
  out.demand = clampStep(demand, d)
  if (traffic) out.traffic = id === 'gridlock' ? clampStep(traffic, tMax) : 1
  return out
}

/** Which preset the given parameter values correspond to, if any. */
export function matchPreset(spec: ScenarioSpec, values: Record<string, unknown>): IntensityId | null {
  for (const p of INTENSITY) {
    const pp = presetParams(spec, p.id)
    if (pp && Object.entries(pp).every(([k, v]) => Number(values[k]) === v)) return p.id
  }
  return null
}

/** Nominal background arrivals per hour before scenario components shape them. */
export function nominalRate(values: Record<string, unknown>): number {
  return Number(values.demand ?? 0) * Number(values.traffic ?? 1)
}

// ---- backend congestion presets (GET /api/congestion-presets) --------------
// The backend resolves a preset into scenario parameters, and explicit run
// params override it. So when a preset is selected, only send the values the
// user changed by hand, never the scenario defaults that would mask the preset.

/** Parameters a backend preset supplies for a scenario (rush_hour also gets the commuter `peak`). */
export function presetKeys(preset: CongestionPreset, scenario: string): string[] {
  const keys = Object.keys(preset.params)
  if (scenario === 'rush_hour' && preset.rushHourPeak != null) keys.push('peak')
  return keys
}

function presetValues(preset: CongestionPreset, scenario: string): Record<string, number> {
  const out = { ...preset.params }
  if (scenario === 'rush_hour' && preset.rushHourPeak != null) out.peak = preset.rushHourPeak
  return out
}

/** What the run will use: scenario defaults and starting settings, then the preset, then the user's own edits. */
export function effectiveValues(spec: ScenarioSpec, draft: Record<string, unknown> | undefined, preset: CongestionPreset | null): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of spec.params) out[p.key] = p.default
  Object.assign(out, scenarioParams(spec))
  if (preset) for (const [k, v] of Object.entries(presetValues(preset, spec.id))) if (k in out) out[k] = v
  if (draft) for (const [k, v] of Object.entries(draft)) out[k] = v
  return out
}

/** Body fields for POST /api/runs. */
export function runRequest(spec: ScenarioSpec, draft: Record<string, unknown> | undefined, preset: CongestionPreset | null): { params: Record<string, unknown>; congestion: string | null } {
  const values = effectiveValues(spec, draft, preset)
  if (!preset) return { params: values, congestion: null }
  const owned = new Set(presetKeys(preset, spec.id))
  const params = Object.fromEntries(Object.entries(values).filter(([k]) => !owned.has(k) || (draft !== undefined && k in draft)))
  return { params, congestion: preset.id }
}

/** Preset-supplied values the user has overridden by hand. */
export function overriddenKeys(spec: ScenarioSpec, draft: Record<string, unknown> | undefined, preset: CongestionPreset | null): string[] {
  if (!preset || !draft) return []
  return presetKeys(preset, spec.id).filter((k) => k in draft)
}

/** Compact pill label; the backend's full label stays in the tooltip. */
export const PRESET_SHORT: Record<string, string> = { light: 'Light', normal: 'Normal', rush_hour: 'Rush Hour', severe: 'Severe', gridlock_stress: 'Gridlock' }
