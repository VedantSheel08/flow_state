import { describe, expect, it } from 'vitest'
import {
  chartValue,
  displayPrecision,
  frameAt,
  framePair,
  signed,
  waitFinding,
} from '../lib/comparison'
import type { Frame } from '../lib/types'

const frames: Frame[] = [1, 2, 3].map((t) => ({ t, v: [], s: [], k: [t * 10] }))
describe('measurement timing', () => {
  it('selects the frame at its SUMO timestamp, not its array index', () => {
    expect(frameAt(frames, 1)?.k[0]).toBe(10)
    expect(frameAt(frames, 2)?.k[0]).toBe(20)
    expect(frameAt(frames, 2.9)?.t).toBe(2)
  })
  it('does not invent an observation before the first frame', () => {
    expect(frameAt(frames, 0)).toBeUndefined()
    expect(frameAt([], 1)).toBeUndefined()
  })
  it('holds the last measured frame when the paired run finishes earlier', () => {
    expect(frameAt(frames, 1500)).toBe(frames[2])
  })
  it('interpolates between the surrounding measured timestamps', () => {
    const pair = framePair({ frames }, 1.5)
    expect(pair.frame?.t).toBe(1)
    expect(pair.next?.t).toBe(2)
    expect(pair.fraction).toBe(0.5)
    expect(framePair({ frames }, 1500).fraction).toBe(0)
  })
})
describe('chart measurement scopes', () => {
  const frames = [1, 450, 900, 1200].map((t, i) => ({
    t,
    v: [],
    s: [],
    k: [0, i * 10, i * 2, i * 100],
  }))
  const run = { frames }
  it('uses actual queue and current wait without converting one into the other', () => {
    expect(chartValue(run, frames[1], 'queue')).toBe(10)
    expect(chartValue(run, frames[1], 'wait')).toBe(2)
  })
  it('computes a measured cumulative hourly rate over elapsed demand time', () => {
    expect(chartValue(run, frames[1], 'throughput')).toBe(800)
  })
  it('excludes drain-period arrivals from demand-window throughput', () => {
    expect(chartValue(run, frames[3], 'throughput')).toBe(800)
    expect(chartValue(run, frames[0], 'throughput')).toBe(0)
  })
})
describe('honest result wording', () => {
  it('preserves small measured differences instead of claiming equality', () => {
    expect(displayPrecision(-0.04, 1)).toBe(2)
    expect(displayPrecision(0, 1)).toBe(1)
    expect(displayPrecision(-16.99, 1)).toBe(1)
  })
  it('distinguishes improvement, regression, and exact equality', () => {
    expect(waitFinding(-16.99)).toBe('Less waiting in this run')
    expect(waitFinding(1.68)).toBe('More waiting in this run')
    expect(waitFinding(0)).toBe('No difference in this run')
  })
  it('never displays negative zero or an invented percentage', () => {
    expect(signed(-0.001)).toBe('0.0')
    expect(signed(1.68, 2)).toBe('+1.68')
    expect(signed(-16.99, 2)).toBe('-16.99')
  })
})
