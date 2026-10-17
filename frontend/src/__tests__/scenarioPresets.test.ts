import { beforeEach, describe, expect, it } from 'vitest'
import { effectiveValues, runRequest } from '../lib/intensity'
import { SCENARIO_PRESETS, defaultCongestion, scenarioParams } from '../lib/scenarioPresets'
import { useStore } from '../lib/store'
import type { CongestionPreset, ParamSpec, ScenarioSpec } from '../lib/types'

const preset = (id: string, demand: number, traffic = 1): CongestionPreset => ({
  id,
  label: id,
  version: 1,
  params: { duration: 5, demand, traffic },
  warmup: 180,
  rushHourPeak: 2,
  stressTest: false,
  calibrated: false,
  safetyValidated: false,
})
const PRESETS = [preset('light', 600), preset('normal', 1600), preset('rush_hour', 2800), preset('severe', 3200, 1.6), preset('gridlock_stress', 3200, 1.6)]

const select = (key: string, d: string): ParamSpec => ({ key, label: key, type: 'select', default: d, live: false, group: 'Scenario' })
const accident: ScenarioSpec = {
  id: 'accident',
  title: 'Road Accident',
  tagline: '',
  description: '',
  category: '',
  networks: ['district'],
  measures: [],
  assumptions: [],
  params: [{ key: 'demand', label: 'demand', type: 'int', default: 1700, min: 400, max: 3200, step: 100, live: false, group: 'Simulation' }, select('severity', 'random')],
}

describe('scenario presets', () => {
  it('cover every scenario in the backend catalog', () => {
    for (const id of ['rush_hour', 'accident', 'ambulance', 'pursuit', 'bike_lanes', 'construction', 'stadium', 'school', 'transit', 'weather', 'disruption'])
      expect(SCENARIO_PRESETS[id], id).toBeDefined()
  })

  it('use a congestion preset only when the backend serves it', () => {
    expect(defaultCongestion('accident', PRESETS)).toBe('severe')
    expect(defaultCongestion('rush_hour', PRESETS)).toBe('rush_hour')
    expect(defaultCongestion('accident', [preset('normal', 1600)])).toBeNull()
    expect(defaultCongestion('accident', null)).toBeNull()
    expect(defaultCongestion('unknown', PRESETS)).toBeNull()
  })

  it('apply scenario parameters the spec has, under the user’s edits', () => {
    expect(effectiveValues(accident, undefined, null)).toMatchObject({ severity: 'moderate' })
    expect(effectiveValues(accident, { severity: 'minor' }, null)).toMatchObject({ severity: 'minor' })
    // stadium's attendance is not an accident parameter
    expect(scenarioParams({ ...accident, id: 'stadium' })).toEqual({})
    expect(runRequest(accident, undefined, PRESETS[3]).params).toMatchObject({ severity: 'moderate' })
  })
})

describe('store seeding', () => {
  beforeEach(() => useStore.setState({ scenarioId: 'rush_hour', congestion: {}, drafts: {}, congestionPresets: null }))

  it('starts each scenario on its preset once presets load', () => {
    useStore.setState({ congestionPresets: PRESETS })
    expect(useStore.getState().congestion.rush_hour).toBe('rush_hour')
    useStore.setState({ scenarioId: 'accident' })
    expect(useStore.getState().congestion.accident).toBe('severe')
  })

  it('keeps a preset the user toggled off, and Defaults restores it', () => {
    useStore.setState({ congestionPresets: PRESETS, scenarioId: 'accident' })
    useStore.getState().setCongestion('accident', null, [])
    useStore.setState({ scenarioId: 'rush_hour' })
    useStore.setState({ scenarioId: 'accident' })
    expect(useStore.getState().congestion.accident).toBeNull()
    useStore.getState().resetDraft('accident')
    expect(useStore.getState().congestion.accident).toBe('severe')
  })
})
