import { frames } from '../lib/frames'
import type { Scene } from './scene'

export type Approach = 'N' | 'E' | 'S' | 'W'

const RANK: Record<string, number> = { G: 4, g: 3, y: 2, Y: 2, u: 2, r: 1, R: 1, s: 1, o: 0, O: 0 }

/** Live signal state per approach (the side traffic arrives from) for a junction. */
export function approachSignals(scene: Scene, junction: string): Partial<Record<Approach, string>> {
  const state = frames.signalsAt(frames.renderT)[junction]
  const out: Partial<Record<Approach, string>> = {}
  if (!state) return out
  for (const h of scene.signals) {
    if (h.tls !== junction) continue
    // a northbound head (dy > 0) controls traffic arriving from the south
    const side: Approach = Math.abs(h.dy) > Math.abs(h.dx) ? (h.dy > 0 ? 'S' : 'N') : h.dx > 0 ? 'W' : 'E'
    for (const li of h.links) {
      const c = state[li]
      if (c && (RANK[c] ?? 0) > (RANK[out[side] ?? 'o'] ?? 0)) out[side] = c
    }
  }
  return out
}
