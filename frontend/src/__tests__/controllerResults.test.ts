import { describe, expect, it } from 'vitest'
import { METRICS, improvementPct, pairs, results, verdict, type VsBaseline } from '../lib/controllerResults'

const vs = (diff: number, lo: number, hi: number): VsBaseline => ({ diff, ci95: [lo, hi], pct: null, p: null, better: 0, worse: 0, paired: 10 })

describe('verdict', () => {
  it('is unclear when the interval crosses zero', () => {
    expect(verdict(METRICS.mean_wait_s, vs(-5, -9, 0.1))).toBe('unclear')
  })
  it('respects metric direction', () => {
    expect(verdict(METRICS.mean_wait_s, vs(-5, -9, -1))).toBe('better')
    expect(verdict(METRICS.mean_wait_s, vs(5, 1, 9))).toBe('worse')
    expect(verdict(METRICS.throughput_vph, vs(20, 5, 40))).toBe('better')
    expect(verdict(METRICS.throughput_vph, vs(-20, -40, -5))).toBe('worse')
  })
  it('is unclear without a comparison', () => {
    expect(verdict(METRICS.mean_wait_s, undefined)).toBe('unclear')
  })
})

describe('improvementPct', () => {
  it('is positive when FlowState is better, for both directions', () => {
    expect(improvementPct(METRICS.mean_wait_s, 100, vs(-10, -15, -5))).toEqual({ mid: 10, lo: 5, hi: 15 })
    expect(improvementPct(METRICS.throughput_vph, 100, vs(10, 5, 15))).toEqual({ mid: 10, lo: 5, hi: 15 })
  })
})

describe('recorded results', () => {
  it('pairs every FlowState run with a fixed-time run on the same demand', () => {
    for (const level of ['standard', 'stress'] as const) {
      const d = results.demand[level]
      const p = pairs(d, 'fixed_time', 'queue_heuristic')
      expect(p.length).toBe(d.seeds.length * results.scenarios.length)
      expect(d.overall.queue_heuristic?.metrics.mean_wait_s?.vs?.paired).toBe(p.length)
    }
  })
  it('agrees with the stored win count', () => {
    const d = results.demand.standard
    const wins = pairs(d, 'fixed_time', 'queue_heuristic').filter((x) => x.b.wait < x.a.wait).length
    expect(wins).toBe(d.overall.queue_heuristic?.metrics.mean_wait_s?.vs?.better)
  })
})
