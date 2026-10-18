import type { Frame, Veh } from './types'

/** Positions between two consecutive measured frames (1 s apart). Vehicles present in only one frame are drawn as-is. */
export function interpolate(a: Frame, b: Frame | undefined, f: number): Veh[] {
  if (!b || f <= 0) return a.v
  const next = new Map<number, Veh>()
  for (const v of b.v) next.set(v[0], v)
  const out: Veh[] = []
  for (const v of a.v) {
    const w = next.get(v[0])
    if (!w) { out.push(v); continue }
    let da = w[3] - v[3]
    if (da > 180) da -= 360
    if (da < -180) da += 360
    out.push([v[0], v[1] + (w[1] - v[1]) * f, v[2] + (w[2] - v[2]) * f, v[3] + da * f, v[4], v[5] + (w[5] - v[5]) * f, w[6]])
  }
  return out
}

/** Index of the last observation at or before t (observations are sorted by t). */
export function obsAt<T extends { t: number }>(obs: T[], t: number): T | undefined {
  let lo = 0, hi = obs.length - 1, best = -1
  while (lo <= hi) {
    const m = (lo + hi) >> 1
    if (obs[m].t <= t) { best = m; lo = m + 1 } else hi = m - 1
  }
  return best >= 0 ? obs[best] : undefined
}
