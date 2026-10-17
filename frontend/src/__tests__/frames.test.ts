import { describe, expect, it } from 'vitest'
import { FrameBuffer, type VehicleView } from '../lib/frames'
import type { FrameMessage } from '../lib/types'
import { along, offset, pointInPolygon, strip } from '../map/geom'

const frame = (t: number, v: FrameMessage['v'], run = 'r1'): FrameMessage => ({ type: 'frame', run, t, v, p: [], tls: { j: t < 1 ? 'G' : 'r' } })

describe('FrameBuffer', () => {
  it('passes exactly through SUMO positions at frame times', () => {
    const fb = new FrameBuffer()
    fb.push(frame(0, [['a', 0, 0, 0, 90, 10, 0]]))
    fb.push(frame(0.5, [['a', 0, 5, 0, 90, 10, 0]]))
    const out: VehicleView[] = []
    fb.vehiclesAt(0, out)
    expect(out[0].x).toBeCloseTo(0)
    fb.vehiclesAt(0.5, out)
    expect(out[0].x).toBeCloseTo(5)
    fb.vehiclesAt(0.25, out)
    expect(out[0].x).toBeCloseTo(2.5, 1)
  })

  it('follows a curved path using SUMO headings instead of the chord', () => {
    const fb = new FrameBuffer()
    // quarter turn: heading east, then north
    fb.push(frame(0, [['a', 0, 0, 0, 90, 6, 0]]))
    fb.push(frame(1, [['a', 0, 4, 4, 0, 6, 0]]))
    const out: VehicleView[] = []
    fb.vehiclesAt(0.5, out)
    // the chord midpoint is (2,2); the turning arc bulges toward (+x, -y)
    expect(out[0].x).toBeGreaterThan(2)
    expect(out[0].y).toBeLessThan(2)
  })

  it('does not interpolate across teleports and resets on a new run', () => {
    const fb = new FrameBuffer()
    fb.push(frame(0, [['a', 0, 0, 0, 90, 10, 0]]))
    fb.push(frame(0.5, [['a', 0, 500, 0, 90, 10, 0]]))
    const out: VehicleView[] = []
    fb.vehiclesAt(0.2, out)
    expect(out[0].x).toBe(0)
    fb.push(frame(0, [['b', 0, 1, 1, 0, 0, 0]], 'r2'))
    expect(fb.frames).toHaveLength(1)
    expect(fb.run).toBe('r2')
  })

  it('never renders beyond the newest frame', () => {
    const fb = new FrameBuffer()
    fb.push(frame(0, []))
    fb.push(frame(0.25, []))
    fb.speed = 64
    let t = 0
    for (let i = 1; i < 20; i++) t = fb.tick(i * 100)
    expect(t).toBeLessThanOrEqual(0.25)
    expect(fb.signalsAt(0.1)).toEqual({ j: 'G' })
  })
})

describe('geometry', () => {
  it('offsets to the left of travel and builds lane strips', () => {
    const l = offset([[0, 0], [10, 0]], 1.5)
    expect(l[0]).toEqual([0, 1.5])
    const poly = strip([[0, 0], [10, 0]], 3)
    expect(pointInPolygon(5, 1.4, poly)).toBe(true)
    expect(pointInPolygon(5, 1.6, poly)).toBe(false)
    const a = along([[0, 0], [10, 0], [10, 10]], 15)
    expect(a.x).toBeCloseTo(10)
    expect(a.y).toBeCloseTo(5)
    expect(a.dy).toBeCloseTo(1)
  })
})
