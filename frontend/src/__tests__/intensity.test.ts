import { describe, expect, it } from 'vitest'
import { FrameBuffer, type VehicleView } from '../lib/frames'
import { effectiveValues, matchPreset, nominalRate, overriddenKeys, presetKeys, presetParams, runRequest } from '../lib/intensity'
import type { CongestionPreset, FrameMessage, ParamSpec, ScenarioSpec } from '../lib/types'

const param = (key: string, d: number, min: number, max: number, step: number, live = false): ParamSpec => ({ key, label: key, type: key === 'traffic' ? 'float' : 'int', default: d, min, max, step, live, group: 'Simulation' })

// mirrors the backend's rush_hour spec (demand 400–3200 step 100, traffic 0.4–1.6 step 0.05)
const spec: ScenarioSpec = {
  id: 'rush_hour',
  title: 'Rush Hour',
  tagline: '',
  description: '',
  category: '',
  networks: ['district'],
  measures: [],
  assumptions: [],
  params: [param('demand', 1600, 400, 3200, 100), param('traffic', 1, 0.4, 1.6, 0.05, true)],
}

describe('traffic intensity presets', () => {
  it('map onto the backend demand and traffic parameters within their ranges', () => {
    expect(presetParams(spec, 'light')).toEqual({ demand: 800, traffic: 1 })
    expect(presetParams(spec, 'normal')).toEqual({ demand: 1600, traffic: 1 })
    expect(presetParams(spec, 'rush')).toEqual({ demand: 2400, traffic: 1 })
    expect(presetParams(spec, 'severe')).toEqual({ demand: 3200, traffic: 1 })
    expect(presetParams(spec, 'gridlock')).toEqual({ demand: 3200, traffic: 1.6 })
  })

  it('respects each scenario’s own default and the step size', () => {
    const school = { ...spec, params: [param('demand', 1100, 400, 3200, 100), param('traffic', 1, 0.4, 1.6, 0.05, true)] }
    expect(presetParams(school, 'light')!.demand).toBe(600) // 550 rounds to the 100 step
    expect(presetParams(school, 'rush')!.demand).toBe(1700) // 1650 rounds up
  })

  it('recognises the active preset and reports custom values as none', () => {
    expect(matchPreset(spec, { demand: 1600, traffic: 1 })).toBe('normal')
    expect(matchPreset(spec, { demand: 3200, traffic: 1.6 })).toBe('gridlock')
    expect(matchPreset(spec, { demand: 2000, traffic: 1 })).toBeNull()
    expect(nominalRate({ demand: 3200, traffic: 1.6 })).toBe(5120)
  })

  it('is unavailable for scenarios without a demand parameter', () => {
    expect(presetParams({ ...spec, params: [] }, 'normal')).toBeNull()
  })
})

describe('frame buffer object reuse', () => {
  const frame = (t: number, v: FrameMessage['v']): FrameMessage => ({ type: 'frame', run: 'r', t, v, p: [], tls: {} })

  it('reuses vehicle view objects between frames without leaking stale entries', () => {
    const fb = new FrameBuffer()
    fb.push(frame(0, [['a', 0, 0, 0, 90, 10, 0], ['b', 0, 0, 10, 90, 10, 0]]))
    fb.push(frame(1, [['a', 0, 10, 0, 90, 10, 0]]))
    const out: VehicleView[] = []
    fb.vehiclesAt(1, out)
    const first = out[0]
    expect(out.map((v) => v.id)).toEqual(['a'])
    fb.vehiclesAt(0.5, out)
    // 'b' left between the frames and fades out at its last SUMO position
    expect(out.map((v) => v.id).sort()).toEqual(['a', 'b'])
    expect(out[0]).toBe(first) // same object, updated in place
    expect(out.find((v) => v.id === 'b')!.y).toBe(10)
  })
})

describe('backend congestion presets (POST /api/runs with congestion)', () => {
  // shape of GET /api/congestion-presets, version 1
  const rush: CongestionPreset = {
    id: 'rush_hour',
    label: 'Rush Hour',
    version: 1,
    params: { duration: 5, demand: 2800, traffic: 1 },
    warmup: 180,
    rushHourPeak: 2,
    stressTest: false,
    calibrated: false,
    safetyValidated: false,
  }
  const withPeak: ScenarioSpec = { ...spec, params: [...spec.params, { ...param('peak', 2, 1.2, 3, 0.1), type: 'float', group: 'Scenario' }, { ...param('duration', 15, 5, 60, 5) }] }

  it('sends the preset id and leaves preset-owned parameters out of params', () => {
    const req = runRequest(withPeak, undefined, rush)
    expect(req.congestion).toBe('rush_hour')
    // scenario defaults for demand/traffic/peak/duration would override the preset, so they are not sent
    expect(req.params).not.toHaveProperty('demand')
    expect(req.params).not.toHaveProperty('traffic')
    expect(req.params).not.toHaveProperty('peak')
    expect(req.params).not.toHaveProperty('duration')
  })

  it('still sends values the user edited by hand, which the backend applies over the preset', () => {
    const req = runRequest(withPeak, { duration: 15, demand: 2000 }, rush)
    expect(req.params).toMatchObject({ duration: 15, demand: 2000 })
    expect(overriddenKeys(withPeak, { demand: 2000 }, rush)).toEqual(['demand'])
  })

  it('shows the effective values: defaults, then preset, then edits', () => {
    expect(effectiveValues(withPeak, undefined, rush)).toMatchObject({ demand: 2800, traffic: 1, peak: 2, duration: 5 })
    expect(effectiveValues(withPeak, { traffic: 1.2 }, rush)).toMatchObject({ demand: 2800, traffic: 1.2 })
    // peak is a rush_hour-only knob
    expect(presetKeys(rush, 'accident')).toEqual(['duration', 'demand', 'traffic'])
  })

  it('without a preset sends the full parameter set and no congestion field', () => {
    const req = runRequest(withPeak, { demand: 900 }, null)
    expect(req.congestion).toBeNull()
    expect(req.params).toMatchObject({ demand: 900, traffic: 1, peak: 2, duration: 15 })
  })
})
