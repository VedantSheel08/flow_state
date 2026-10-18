import { describe, expect, it } from 'vitest'
import { isAmbulance, normalise, sideId, spriteCode, type DemoAsset } from '../lib/flowstateDemo'

const placed = (type: number) => ({ id: 1, x: 0, y: 0, angle: 0, speed: 0, type })

describe('schema 2 (district) assets', () => {
  it('uses main sprite codes directly and recognises the ambulance', () => {
    expect(spriteCode(placed(5), true)).toBe(5)
    expect(isAmbulance(placed(5), true)).toBe(true)
    expect(isAmbulance(placed(2), true)).toBe(false)
    expect(spriteCode(placed(2), false)).toBe(5) // lab code 2 = ambulance
  })
  it('reads per-side ambulance ids', () => {
    const u = { id: 5, left: { id: 5 }, right: { id: 50 } } as never
    expect(sideId(u, 'left')).toBe(5)
    expect(sideId(u, 'right')).toBe(50)
    expect(sideId({ id: 9, left: {}, right: {} } as never, 'right')).toBe(9)
  })
  it('maps main car metrics onto the page fields without overwriting existing ones', () => {
    const asset = {
      totals: { left: { car: { waiting: 25, travelTime: 137 } }, right: { car: { waiting: 20, travelTime: 132 }, mean_wait_civilian_s: 1 } },
      aggregate: { metrics: { car_waiting: { mean_diff: -4 }, mean_travel_s: { mean_diff: 0 } } },
    } as unknown as DemoAsset
    normalise(asset)
    expect(asset.totals.left.mean_wait_civilian_s).toBe(25)
    expect(asset.totals.right.mean_wait_civilian_s).toBe(1)
    expect(asset.aggregate.metrics.mean_wait_civilian_s.mean_diff).toBe(-4)
    expect(asset.aggregate.metrics.car_travelTime).toBeUndefined()
  })
})
