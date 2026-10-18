import { describe, expect, it } from 'vitest'
import { interpolate, obsAt } from '../lib/interp'
import type { Frame } from '../lib/types'

const f = (t: number, v: Frame['v']): Frame => ({ t, v, s: [], k: [] })

describe('interpolate', () => {
  it('blends matching vehicles and keeps the shortest turn for angles', () => {
    const a = f(0, [[1, 0, 0, 350, 0, 100, 0], [2, 50, 50, 90, 0, 0, 1]])
    const b = f(1, [[1, 100, 200, 10, 0, 120, 0]])
    const out = interpolate(a, b, 0.5)
    expect(out[0][1]).toBe(50)
    expect(out[0][2]).toBe(100)
    expect(out[0][3]).toBe(360) // 350 -> 10 goes through 360, not backwards through 180
    expect(out[1]).toEqual(a.v[1]) // vehicle missing from the next frame is drawn where it was measured
  })
  it('returns measured positions when there is no next frame', () => {
    const a = f(0, [[1, 5, 5, 0, 0, 0, 0]])
    expect(interpolate(a, undefined, 0.7)).toBe(a.v)
  })
})

describe('obsAt', () => {
  it('finds the last observation at or before t', () => {
    const obs = [{ t: 0 }, { t: 5 }, { t: 10 }]
    expect(obsAt(obs, 7)?.t).toBe(5)
    expect(obsAt(obs, 10)?.t).toBe(10)
    expect(obsAt(obs, -1)).toBeUndefined()
  })
})
