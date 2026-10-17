import { describe, expect, it } from 'vitest'
import type { Overlay } from '../lib/types'
import { edgeIndicator, focusTarget, phaseAt, type PlacedOverlay } from '../map/overlays'

const R = { x0: 100, y0: 50, x1: 900, y1: 650 } // centre (500, 350)

const ov = (id: string, kind: Overlay['kind'], tStart: number | null, tEnd: number | null, extra: Partial<Overlay> = {}): Overlay => ({
  id,
  kind,
  tone: 'incident',
  label: id,
  edges: ['e'],
  tStart,
  tEnd,
  ...extra,
})
const at = (o: Overlay, x = 0, y = 0): PlacedOverlay => ({ o, x, y, segs: [] })

describe('edgeIndicator', () => {
  it('is null for a point inside the safe area', () => {
    expect(edgeIndicator(500, 350, R)).toBeNull()
    expect(edgeIndicator(100, 650, R)).toBeNull()
  })

  it('clamps to the edge the point lies beyond', () => {
    const right = edgeIndicator(2000, 350, R)!
    expect(right.x).toBeCloseTo(900)
    expect(right.y).toBeCloseTo(350)
    expect(right.angle).toBeCloseTo(0)
    const up = edgeIndicator(500, -1000, R)!
    expect(up.y).toBeCloseTo(50)
    expect(up.angle).toBeCloseTo(-Math.PI / 2)
  })

  it('keeps diagonal indicators on the boundary, in the direction of the point', () => {
    const d = edgeIndicator(1500, 1350, R)! // 45° down-right from the centre
    expect(d.y).toBeCloseTo(650) // the bottom edge is nearer than the right one
    expect(d.x).toBeCloseTo(800)
    expect(d.angle).toBeCloseTo(Math.PI / 4)
  })
})

describe('overlay timing', () => {
  it('is planned before its start, active in its window, done after', () => {
    const o = ov('crash', 'point', 120, 400)
    expect(phaseAt(o, 0)).toBe('planned')
    expect(phaseAt(o, 120)).toBe('active')
    expect(phaseAt(o, 401)).toBe('done')
    expect(phaseAt(ov('zone', 'zone', null, null), 9999)).toBe('active')
  })

  it('jumps to the latest live scene, else the next planned one', () => {
    const a = at(ov('call1', 'point', 60, 500))
    const b = at(ov('call2', 'point', 200, 700))
    const school = at(ov('school', 'zone', 0, null, { tone: 'demand' }))
    expect(focusTarget([a, b, school], 10)?.o.id).toBe('call1')
    expect(focusTarget([a, b, school], 100)?.o.id).toBe('call1')
    expect(focusTarget([a, b, school], 250)?.o.id).toBe('call2')
    expect(focusTarget([school], 250)).toBeNull()
    expect(focusTarget([at(ov('route', 'route', 0, null))], 10)).toBeNull()
  })
})
