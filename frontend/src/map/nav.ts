// Intersection navigation: framing, spatial stepping and the grid layout used
// by the intersection picker. Pure functions so they are easy to test.

export interface NavJunction {
  id: string
  name: string
  x: number
  y: number
  control: string
}

export type Dir = 'left' | 'right' | 'up' | 'down'

const DIRS: Record<Dir, [number, number]> = { left: [-1, 0], right: [1, 0], up: [0, 1], down: [0, -1] }

/** World span (metres) that an intersection view frames across the shorter screen side. */
export const FOCUS_SPAN = 118

export function focusScale(width: number, height: number): number {
  return Math.min(14, Math.max(3.2, Math.min(width, height) / FOCUS_SPAN))
}

/** Nearest junction in a screen direction (world y up), preferring ones on the same axis. */
export function stepJunction(list: NavJunction[], fromId: string, dir: Dir): string | null {
  const from = list.find((j) => j.id === fromId)
  if (!from) return list[0]?.id ?? null
  const [dx, dy] = DIRS[dir]
  let best: string | null = null
  let bestCost = Infinity
  for (const j of list) {
    if (j.id === fromId) continue
    const vx = j.x - from.x
    const vy = j.y - from.y
    const along = vx * dx + vy * dy
    if (along <= 1) continue
    const across = Math.abs(vx * -dy + vy * dx)
    if (across > along * 1.2) continue
    const cost = along + across * 2.5
    if (cost < bestCost) {
      bestCost = cost
      best = j.id
    }
  }
  return best
}

/** Cycle through junctions in reading order (north-west to south-east). */
export function cycleJunction(list: NavJunction[], fromId: string, delta: 1 | -1): string | null {
  if (!list.length) return null
  const order = readingOrder(list)
  const i = order.findIndex((j) => j.id === fromId)
  const n = order.length
  return order[(((i < 0 ? 0 : i + delta) % n) + n) % n].id
}

export function readingOrder(list: NavJunction[]): NavJunction[] {
  return [...list].sort((a, b) => b.y - a.y || a.x - b.x)
}

/** Arrange junctions into rows (north first) and columns (west first) by clustering coordinates. */
export function junctionGrid(list: NavJunction[], tol = 25): { rows: number; cols: number; cells: (NavJunction & { r: number; c: number })[] } {
  const cluster = (vals: number[]) => {
    const sorted = [...vals].sort((a, b) => a - b)
    const centres: number[] = []
    for (const v of sorted) if (!centres.length || v - centres[centres.length - 1] > tol) centres.push(v)
    return centres
  }
  const xs = cluster(list.map((j) => j.x))
  const ys = cluster(list.map((j) => j.y)).reverse()
  const idx = (cs: number[], v: number) => cs.reduce((bi, c, i) => (Math.abs(c - v) < Math.abs(cs[bi] - v) ? i : bi), 0)
  return {
    rows: ys.length,
    cols: xs.length,
    cells: list.map((j) => ({ ...j, r: idx(ys, j.y), c: idx(xs, j.x) })),
  }
}

/** "John Street & King Street West" -> "John & King" */
export function shortName(name: string): string {
  return name
    .replace(/\s+(Street|Avenue|Road|Boulevard|Drive|Lane)(\s+(West|East|North|South))?\b/g, '')
    .replace(/\s*&\s*/, ' & ')
}

/** Street-name part of a junction name ("John Street & King Street West", 0) -> "John". */
export function streetPart(name: string, side: 0 | 1): string {
  return shortName(name.split('&')[side]?.trim() ?? '')
}

export const CONTROL_LABEL: Record<string, string> = {
  traffic_light: 'Signalised',
  allway_stop: 'All-way stop',
  priority_stop: 'Two-way stop',
  priority: 'Priority',
}
