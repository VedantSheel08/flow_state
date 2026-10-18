import { describe, expect, it } from 'vitest'
import { at, interpolate, statusSince } from '../lib/interp'
import type { Frame } from '../lib/types'

const f = (t: number, v: Frame['v']): Frame => ({ t, v, s: [], k: [] })

describe('interpolate', () => {
  it('blends matching vehicles and turns the short way round', () => {
    const out = interpolate(f(0, [[1, 0, 0, 350, 0, 100, 0], [2, 5, 5, 90, 0, 0, 1]]), f(1, [[1, 100, 200, 10, 0, 120, 0]]), 0.5)
    expect(out[0].slice(1, 4)).toEqual([50, 100, 360])
    expect(out[1][1]).toBe(5) // a vehicle that left the network is drawn where it was measured
  })
})

describe('at', () => {
  it('finds the last row at or before t', () => {
    const rows = [{ t: 0 }, { t: 5 }, { t: 10 }]
    expect(at(rows, 7)?.t).toBe(5)
    expect(at(rows, -1)).toBeUndefined()
  })
})

describe('statusSince', () => {
  it('measures how long the current status has lasted', () => {
    const seg: [number, number, number][] = [[10, 20, 0], [21, 40, 2], [41, 50, 0]]
    expect(statusSince(seg, 30)).toEqual({ status: 2, seconds: 10 })
    expect(statusSince(seg, 5)).toBeNull()
  })
})
