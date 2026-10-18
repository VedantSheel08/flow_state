import type { EvState, Frame, Veh } from './types'

/** Positions between two consecutive measured frames (1 s apart). Vehicles present in only one frame are drawn as measured. */
export function interpolate(a: Frame, b: Frame | undefined, f: number): Veh[] {
  if (!b || f <= 0) return a.v
  const next = new Map<number, Veh>()
  for (const v of b.v) next.set(v[0], v)
  return a.v.map((v) => {
    const w = next.get(v[0])
    if (!w) return v
    let da = w[3] - v[3]
    if (da > 180) da -= 360
    if (da < -180) da += 360
    return [v[0], v[1] + (w[1] - v[1]) * f, v[2] + (w[2] - v[2]) * f, v[3] + da * f, v[4], v[5] + (w[5] - v[5]) * f, w[6]] as Veh
  })
}

/** Last row with t at or before `t` (rows sorted by t). */
export function at<T extends { t: number }>(rows: T[], t: number): T | undefined {
  let lo = 0, hi = rows.length - 1, best = -1
  while (lo <= hi) {
    const m = (lo + hi) >> 1
    if (rows[m].t <= t) { best = m; lo = m + 1 } else hi = m - 1
  }
  return best >= 0 ? rows[best] : undefined
}

/** How long (seconds) the ambulance has been in the status it has at time t, from story segments [t0, t1, status]. */
export function statusSince(segments: [number, number, number][], t: number): { status: number; seconds: number } | null {
  const idx = segments.findIndex((s) => s[0] <= t && t <= s[1])
  if (idx < 0) return null
  const cur = segments[idx]
  let start = cur[0]
  for (let i = idx - 1; i >= 0 && segments[i][2] === cur[2] && segments[i][1] >= start - 1; i--) start = segments[i][0]
  return { status: cur[2], seconds: t - start + 1 }
}

export const evById = (e: EvState[] | undefined, id: number) => e?.find((x) => x[0] === id)
