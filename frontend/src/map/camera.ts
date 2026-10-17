// Orthographic 2D camera with eased zoom/pan, zoom-to-cursor, inertia and
// timed "glides" between views (zooms out and back in over long distances).
// World coordinates are SUMO metres (y up); screen coordinates are CSS pixels (y down).

export const MIN_SCALE = 0.45
export const MAX_SCALE = 48

interface Glide {
  t: number
  dur: number
  x0: number
  y0: number
  x1: number
  y1: number
  l0: number // ln(scale) at start
  l1: number // ln(scale) at end
  dip: number // extra ln(scale) drop at the midpoint (<= 0)
}

const easeInOut = (u: number) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2)

export class Camera {
  x = 0
  y = 0
  scale = 5 // CSS px per metre
  tx = 0
  ty = 0
  tscale = 5
  width = 1
  height = 1
  vx = 0
  vy = 0
  minScale = MIN_SCALE
  maxScale = MAX_SCALE
  private anchor: { sx: number; sy: number; wx: number; wy: number } | null = null
  private glideState: Glide | null = null

  setViewport(w: number, h: number) {
    this.width = Math.max(1, w)
    this.height = Math.max(1, h)
  }

  get gliding(): boolean {
    return this.glideState !== null
  }

  /** Scale is still changing (glide or eased zoom). */
  get zooming(): boolean {
    return this.glideState !== null || Math.abs(this.tscale - this.scale) > this.scale * 1e-4
  }

  jump(x: number, y: number, scale: number) {
    this.x = this.tx = x
    this.y = this.ty = y
    this.scale = this.tscale = this.clampScale(scale)
    this.anchor = null
    this.glideState = null
    this.vx = this.vy = 0
  }

  flyTo(x: number, y: number, scale?: number) {
    this.tx = x
    this.ty = y
    if (scale) this.tscale = this.clampScale(scale)
    this.anchor = null
    this.glideState = null
    this.vx = this.vy = 0
  }

  /**
   * Smooth, timed move to (x, y, scale). When the two views are far apart
   * relative to the viewport, the camera pulls back mid-flight so the viewer
   * keeps their bearings (a simplified van Wijk & Nuij zoom-pan).
   */
  glide(x: number, y: number, scale: number, durMs?: number) {
    const s1 = this.clampScale(scale)
    const s0 = this.scale
    const dist = Math.hypot(x - this.x, y - this.y)
    const l0 = Math.log(s0)
    const l1 = Math.log(s1)
    // scale at which both endpoints fit on screen with margin
    const fit = dist > 0 ? Math.min(this.width, this.height) / (dist * 1.35) : Infinity
    const lfit = Math.log(Math.max(this.minScale, Math.min(this.maxScale, fit)))
    const dip = Math.min(0, lfit - Math.min(l0, l1))
    const span = Math.abs(l1 - l0) + Math.abs(dip) * 2 + (dist * Math.min(s0, s1)) / Math.max(this.width, this.height)
    const dur = durMs ?? Math.min(1500, 520 + span * 260)
    this.anchor = null
    this.vx = this.vy = 0
    this.tx = x
    this.ty = y
    this.tscale = s1
    this.glideState = { t: 0, dur: dur / 1000, x0: this.x, y0: this.y, x1: x, y1: y, l0, l1, dip }
  }

  /** Stop any scripted motion (user took over). */
  cancelGlide() {
    if (!this.glideState) return
    this.glideState = null
    this.tx = this.x
    this.ty = this.y
    this.tscale = this.scale
  }

  clampScale(s: number) {
    return Math.min(this.maxScale, Math.max(this.minScale, s))
  }

  toScreen(wx: number, wy: number): [number, number] {
    return [(wx - this.x) * this.scale + this.width / 2, (this.y - wy) * this.scale + this.height / 2]
  }

  toWorld(sx: number, sy: number): [number, number] {
    return [(sx - this.width / 2) / this.scale + this.x, this.y - (sy - this.height / 2) / this.scale]
  }

  /** Visible world rectangle [x0, y0, x1, y1]. */
  viewRect(): [number, number, number, number] {
    const hw = this.width / 2 / this.scale
    const hh = this.height / 2 / this.scale
    return [this.x - hw, this.y - hh, this.x + hw, this.y + hh]
  }

  /** Zoom by factor keeping the world point under (sx, sy) fixed. */
  zoomAt(factor: number, sx: number, sy: number) {
    this.cancelGlide()
    const [wx, wy] = this.toWorld(sx, sy)
    this.tscale = this.clampScale(this.tscale * factor)
    this.anchor = { sx, sy, wx, wy }
  }

  panBy(dxPx: number, dyPx: number) {
    this.glideState = null
    this.x -= dxPx / this.scale
    this.y += dyPx / this.scale
    this.tx = this.x
    this.ty = this.y
    this.anchor = null
  }

  /** Scale that fits the bounds into the viewport. */
  fitScale(b: [number, number, number, number], pad = 40) {
    const [x0, y0, x1, y1] = b
    return Math.min((this.width - pad * 2) / (x1 - x0), (this.height - pad * 2) / (y1 - y0))
  }

  fit(b: [number, number, number, number], pad = 40) {
    const [x0, y0, x1, y1] = b
    this.flyTo((x0 + x1) / 2, (y0 + y1) / 2, this.fitScale(b, pad))
  }

  /** Advance easing; returns true while moving. */
  update(dt: number): boolean {
    const g = this.glideState
    if (g) {
      g.t = Math.min(g.dur, g.t + dt)
      const u = easeInOut(g.dur > 0 ? g.t / g.dur : 1)
      const ls = g.l0 + (g.l1 - g.l0) * u + g.dip * Math.sin(Math.PI * u)
      this.scale = Math.exp(ls)
      this.x = g.x0 + (g.x1 - g.x0) * u
      this.y = g.y0 + (g.y1 - g.y0) * u
      if (g.t >= g.dur) {
        this.glideState = null
        this.scale = this.tscale = Math.exp(g.l1)
        this.x = this.tx = g.x1
        this.y = this.ty = g.y1
      }
      return true
    }
    const k = 1 - Math.exp(-dt * 14)
    let moving = false
    if (Math.abs(this.tscale - this.scale) > this.scale * 1e-4) {
      this.scale += (this.tscale - this.scale) * k
      moving = true
    } else this.scale = this.tscale
    if (this.anchor) {
      // keep anchor world point under the cursor while the scale eases
      const a = this.anchor
      this.x = a.wx - (a.sx - this.width / 2) / this.scale
      this.y = a.wy + (a.sy - this.height / 2) / this.scale
      this.tx = this.x
      this.ty = this.y
      if (!moving) this.anchor = null
      return true
    }
    if (this.vx || this.vy) {
      this.panBy(this.vx * dt, this.vy * dt)
      const decay = Math.exp(-dt * 5)
      this.vx *= decay
      this.vy *= decay
      if (Math.hypot(this.vx, this.vy) < 8) this.vx = this.vy = 0
      return true
    }
    const dx = this.tx - this.x
    const dy = this.ty - this.y
    if (Math.abs(dx) * this.scale > 0.05 || Math.abs(dy) * this.scale > 0.05) {
      this.x += dx * k
      this.y += dy * k
      moving = true
    } else {
      this.x = this.tx
      this.y = this.ty
    }
    return moving
  }
}
