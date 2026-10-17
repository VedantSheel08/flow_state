// Starting settings per scenario, so picking one opens on a load and setup that
// fits what it represents (a rush hour on the Rush Hour preset, an accident on
// Severe). `congestion` is a backend congestion preset id; `params` override the
// scenario's own defaults. User edits and pill choices still take precedence.

import type { CongestionPreset, ScenarioSpec } from './types'

export interface ScenarioPreset {
  congestion: string | null
  params?: Record<string, unknown>
}

export const SCENARIO_PRESETS: Record<string, ScenarioPreset> = {
  rush_hour: { congestion: 'rush_hour', params: { period: 'am' } },
  accident: { congestion: 'severe', params: { severity: 'moderate' } },
  ambulance: { congestion: 'normal' },
  pursuit: { congestion: 'normal' },
  bike_lanes: { congestion: 'normal', params: { network: 'district_bike' } },
  construction: { congestion: 'rush_hour', params: { closure: 'lane' } },
  stadium: { congestion: 'rush_hour', params: { phase: 'departure', attendance: 900 } },
  school: { congestion: 'normal', params: { pickups: 90 } },
  transit: { congestion: 'rush_hour', params: { tsp: true } },
  weather: { congestion: 'normal', params: { condition: 'heavy_rain' } },
  disruption: { congestion: 'rush_hour' },
}

/** The scenario's starting congestion preset, if the backend serves it. */
export function defaultCongestion(scenario: string, presets: CongestionPreset[] | null): string | null {
  const id = SCENARIO_PRESETS[scenario]?.congestion ?? null
  return id && presets?.some((p) => p.id === id) ? id : null
}

/** Scenario-preset parameter values, limited to parameters the spec actually has. */
export function scenarioParams(spec: ScenarioSpec): Record<string, unknown> {
  const params = SCENARIO_PRESETS[spec.id]?.params ?? {}
  return Object.fromEntries(Object.entries(params).filter(([k]) => spec.params.some((p) => p.key === k)))
}
