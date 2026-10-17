import { describe, expect, it } from 'vitest'
import { Camera } from '../map/camera'
import { cycleJunction, focusScale, junctionGrid, shortName, stepJunction, type NavJunction } from '../map/nav'
import { busLineFor } from '../map/vehicles'

// 4 x 4 street grid like the Riverside District (x: 0..600, y: 0..480)
const grid: NavJunction[] = []
for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) grid.push({ id: `n${c}_${r}`, name: `C${c} Street & R${r} Street`, x: c * 200, y: r * 160, control: 'traffic_light' })

describe('intersection navigation', () => {
  it('steps to the adjacent intersection in each direction', () => {
    expect(stepJunction(grid, 'n1_1', 'right')).toBe('n2_1')
    expect(stepJunction(grid, 'n1_1', 'left')).toBe('n0_1')
    expect(stepJunction(grid, 'n1_1', 'up')).toBe('n1_2')
    expect(stepJunction(grid, 'n1_1', 'down')).toBe('n1_0')
  })

  it('stays put at the edge of the network', () => {
    expect(stepJunction(grid, 'n0_1', 'left')).toBeNull()
    expect(stepJunction(grid, 'n3_3', 'up')).toBeNull()
  })

  it('cycles in reading order and wraps around', () => {
    // reading order starts at the north-west corner
    expect(cycleJunction(grid, 'n3_0', 1)).toBe('n0_3')
    expect(cycleJunction(grid, 'n0_3', -1)).toBe('n3_0')
    expect(cycleJunction(grid, 'n0_3', 1)).toBe('n1_3')
  })

  it('lays junctions out as rows (north first) and columns (west first)', () => {
    const g = junctionGrid(grid)
    expect(g.rows).toBe(4)
    expect(g.cols).toBe(4)
    const nw = g.cells.find((q) => q.id === 'n0_3')!
    const se = g.cells.find((q) => q.id === 'n3_0')!
    expect([nw.r, nw.c]).toEqual([0, 0])
    expect([se.r, se.c]).toEqual([3, 3])
  })

  it('shortens street names and frames intersections at a detailed scale', () => {
    expect(shortName('Main Street & King Street')).toBe('Main & King')
    expect(shortName('Park Avenue & Queen Street')).toBe('Park & Queen')
    expect(focusScale(1300, 850)).toBeGreaterThan(6)
    expect(focusScale(100, 100)).toBe(3.2)
  })
})

describe('camera glide', () => {
  it('arrives exactly at the target after its duration', () => {
    const cam = new Camera()
    cam.setViewport(1200, 800)
    cam.minScale = 0.5
    cam.jump(0, 0, 8)
    cam.glide(600, 480, 8, 1000)
    let mid = Infinity
    for (let i = 0; i < 70; i++) {
      cam.update(1 / 60)
      if (i === 30) mid = cam.scale
    }
    expect(cam.gliding).toBe(false)
    expect(cam.x).toBeCloseTo(600)
    expect(cam.y).toBeCloseTo(480)
    expect(cam.scale).toBeCloseTo(8)
    // long hops pull back mid-flight so both ends fit on screen
    expect(mid).toBeLessThan(3)
  })

  it('is cancelled when the user zooms', () => {
    const cam = new Camera()
    cam.setViewport(800, 600)
    cam.jump(0, 0, 5)
    cam.glide(100, 0, 5, 1000)
    cam.update(0.1)
    cam.zoomAt(2, 400, 300)
    expect(cam.gliding).toBe(false)
  })
})

describe('bus livery', () => {
  it('matches the longest line prefix of a SUMO vehicle id', () => {
    const lines = [
      { id: '501', color: '#C8102E' },
      { id: '6', color: '#1F6FB2' },
    ]
    expect(busLineFor('bus501e3', lines)).toEqual(lines[0])
    expect(busLineFor('bus6n1', lines)).toEqual(lines[1])
    expect(busLineFor('car1', lines).id).toBe('')
  })
})
