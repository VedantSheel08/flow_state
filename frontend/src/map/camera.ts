// Orthographic 2D camera with eased zoom/pan, zoom-to-cursor and inertia.
// World coordinates are SUMO metres (y up); screen coordinates are CSS pixels (y down).

export const MIN_SCALE = 0.45
export const MAX_SCALE = 48

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

  setViewport(w: number, h: number) {
    this.width = Math.max(1, w)
    this.height = Math.max(1, h)
  }

  jump(x: number, y: number, scale: number) {
    this.x = this.tx = x
    this.y = this.ty = y
    this.scale = this.tscale = this.clampScale(scale)
    this.anchor = null
  }

  flyTo(x: number, y: number, scale?: number) {
    this.tx = x
    this.ty = y
    if (scale) this.tscale = this.clampScale(scale)
    this.anchor = null
    this.vx = this.vy = 0
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

  /** Zoom by factor keeping the world point under (sx, sy) fixed. */
  zoomAt(factor: number, sx: number, sy: number) {
    const [wx, wy] = this.toWorld(sx, sy)
    this.tscale = this.clampScale(this.tscale * factor)
    this.anchor = { sx, sy, wx, wy }
  }

  panBy(dxPx: number, dyPx: number) {
    this.x -= dxPx / this.scale
    this.y += dyPx / this.scale
    this.tx = this.x
    this.ty = this.y
    this.anchor = null
  }

  fit(b: [number, number, number, number], pad = 40) {
    const [x0, y0, x1, y1] = b
    const s = Math.min((this.width - pad * 2) / (x1 - x0), (this.height - pad * 2) / (y1 - y0))
    this.flyTo((x0 + x1) / 2, (y0 + y1) / 2, s)
  }

  /** Advance easing; returns true while moving. */
  update(dt: number): boolean {
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
