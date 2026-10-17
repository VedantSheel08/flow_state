// Playout buffer for authoritative SUMO frames.
//
// The server sends a frame per simulation step (rate-limited to 20 Hz). The map
// renders slightly behind the newest frame and interpolates between the two
// frames that bracket the render time. Vehicle paths use cubic Hermite
// interpolation with SUMO's own heading and speed as end tangents, so a
// vehicle passes exactly through every SUMO position and follows curved
// turning paths instead of cutting chords. Nothing is extrapolated beyond the
// newest frame.

import type { FrameMessage, PersonTuple, VehicleTuple } from './types'

export interface Frame {
  t: number
  wall: number
  v: Map<string, VehicleTuple>
  p: Map<string, PersonTuple>
  tls: Record<string, string>
}

export interface VehicleView {
  id: string
  code: number
  x: number
  y: number
  angle: number // degrees, SUMO convention
  speed: number
  signals: number
  alpha: number
}

export interface PersonView {
  id: string
  x: number
  y: number
  angle: number
  speed: number
}

const DEG = Math.PI / 180
const TELEPORT_M = 40

function lerpAngle(a: number, b: number, u: number): number {
  const d = ((b - a + 540) % 360) - 180
  return a + d * u
}

export class FrameBuffer {
  frames: Frame[] = []
  renderT = -Infinity
  run: string | null = null
  private lastWall = 0
  speed = 1
  paused = false

  reset(run: string | null) {
    this.frames = []
    this.renderT = -Infinity
    this.run = run
  }

  push(msg: FrameMessage) {
    if (msg.run !== this.run) this.reset(msg.run)
    const last = this.frames[this.frames.length - 1]
    if (last && msg.t <= last.t) {
      if (msg.t < last.t - 1) this.reset(msg.run) // time went backwards: new run or restart
      else return
    }
    this.frames.push({
      t: msg.t,
      wall: performance.now(),
      v: new Map(msg.v.map((v) => [v[0], v])),
      p: new Map(msg.p.map((p) => [p[0], p])),
      tls: msg.tls,
    })
    if (this.frames.length > 40) this.frames.splice(0, this.frames.length - 40)
  }

  get latest(): Frame | undefined {
    return this.frames[this.frames.length - 1]
  }

  /** Advance the render clock; returns the simulated time to draw. */
  tick(now: number): number {
    const fr = this.frames
    const dtWall = this.lastWall ? Math.min(0.25, (now - this.lastWall) / 1000) : 0
    this.lastWall = now
    if (!fr.length) return this.renderT
    const newest = fr[fr.length - 1].t
    // render one typical frame interval behind the newest frame
    let interval = 0.25
    if (fr.length >= 3) {
      const a = fr[fr.length - 3]
      interval = Math.max(0.1, (newest - a.t) / 2)
    }
    const delay = interval * 1.25
    const target = newest - delay
    if (!isFinite(this.renderT) || this.renderT < newest - 4 * Math.max(1, this.speed)) this.renderT = target
    if (!this.paused) this.renderT += dtWall * this.speed
    // gently pull toward the target to absorb jitter, never run past the newest frame
    this.renderT += (target - this.renderT) * Math.min(1, dtWall * 2.5)
    if (this.renderT > newest) this.renderT = newest
    if (this.renderT < fr[0].t) this.renderT = fr[0].t
    return this.renderT
  }

  private bracket(t: number): [Frame, Frame, number] | null {
    const fr = this.frames
    if (!fr.length) return null
    if (fr.length === 1 || t <= fr[0].t) return [fr[0], fr[0], 0]
    for (let i = fr.length - 1; i > 0; i--) {
      const a = fr[i - 1]
      const b = fr[i]
      if (t >= a.t) {
        const span = b.t - a.t
        return [a, b, span > 0 ? Math.min(1, (t - a.t) / span) : 1]
      }
    }
    return [fr[0], fr[0], 0]
  }

  signalsAt(t: number): Record<string, string> {
    const br = this.bracket(t)
    if (!br) return {}
    const [a, b, u] = br
    return u >= 1 ? b.tls : a.tls
  }

  vehiclesAt(t: number, out: VehicleView[]): VehicleView[] {
    out.length = 0
    const br = this.bracket(t)
    if (!br) return out
    const [A, B, u] = br
    const span = B.t - A.t
    const h00 = 2 * u * u * u - 3 * u * u + 1
    const h10 = u * u * u - 2 * u * u + u
    const h01 = -2 * u * u * u + 3 * u * u
    const h11 = u * u * u - u * u
    for (const [id, b] of B.v) {
      const a = A.v.get(id)
      if (!a || A === B) {
        out.push({ id, code: b[1], x: b[2], y: b[3], angle: b[4], speed: b[5], signals: b[6], alpha: a ? 1 : Math.min(1, 0.25 + u) })
        continue
      }
      const dx = b[2] - a[2]
      const dy = b[3] - a[3]
      if (dx * dx + dy * dy > TELEPORT_M * TELEPORT_M) {
        const v = u < 0.5 ? a : b
        out.push({ id, code: v[1], x: v[2], y: v[3], angle: v[4], speed: v[5], signals: v[6], alpha: 1 })
        continue
      }
      // tangents from SUMO heading and speed, clamped to the chord length so
      // stop-and-go never overshoots
      const chord = Math.sqrt(dx * dx + dy * dy)
      const m0 = Math.min(a[5] * span, chord * 1.5)
      const m1 = Math.min(b[5] * span, chord * 1.5)
      const sa = Math.sin(a[4] * DEG), ca = Math.cos(a[4] * DEG)
      const sb = Math.sin(b[4] * DEG), cb = Math.cos(b[4] * DEG)
      const x = h00 * a[2] + h10 * sa * m0 + h01 * b[2] + h11 * sb * m1
      const y = h00 * a[3] + h10 * ca * m0 + h01 * b[3] + h11 * cb * m1
      out.push({
        id,
        code: b[1],
        x,
        y,
        angle: lerpAngle(a[4], b[4], u),
        speed: a[5] + (b[5] - a[5]) * u,
        signals: u < 0.5 ? a[6] : b[6],
        alpha: 1,
      })
    }
    // vehicles that left between A and B fade out at their last position
    if (A !== B && u < 0.6) {
      for (const [id, a] of A.v) {
        if (!B.v.has(id)) out.push({ id, code: a[1], x: a[2], y: a[3], angle: a[4], speed: a[5], signals: a[6], alpha: 1 - u / 0.6 })
      }
    }
    return out
  }

  personsAt(t: number, out: PersonView[]): PersonView[] {
    out.length = 0
    const br = this.bracket(t)
    if (!br) return out
    const [A, B, u] = br
    for (const [id, b] of B.p) {
      const a = A.p.get(id)
      if (!a) {
        out.push({ id, x: b[1], y: b[2], angle: b[3], speed: b[4] })
        continue
      }
      out.push({ id, x: a[1] + (b[1] - a[1]) * u, y: a[2] + (b[2] - a[2]) * u, angle: lerpAngle(a[3], b[3], u), speed: b[4] })
    }
    return out
  }

  vehicleAt(t: number, id: string): VehicleView | null {
    const tmp: VehicleView[] = []
    this.vehiclesAt(t, tmp)
    return tmp.find((v) => v.id === id) ?? null
  }
}

export const frames = new FrameBuffer()
