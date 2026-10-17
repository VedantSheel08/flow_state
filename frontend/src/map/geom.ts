export type Pt = [number, number]

export function toPts(flat: number[]): Pt[] {
  const out: Pt[] = []
  for (let i = 0; i + 1 < flat.length; i += 2) out.push([flat[i], flat[i + 1]])
  return out
}

export function polyLength(p: Pt[]): number {
  let L = 0
  for (let i = 1; i < p.length; i++) L += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1])
  return L
}

/** Position and unit direction at arc length s along a polyline. */
export function along(p: Pt[], s: number): { x: number; y: number; dx: number; dy: number } {
  let rem = Math.max(0, s)
  for (let i = 1; i < p.length; i++) {
    const [x0, y0] = p[i - 1]
    const [x1, y1] = p[i]
    const seg = Math.hypot(x1 - x0, y1 - y0)
    if (seg === 0) continue
    if (rem <= seg || i === p.length - 1) {
      const f = Math.min(1, rem / seg)
      return { x: x0 + (x1 - x0) * f, y: y0 + (y1 - y0) * f, dx: (x1 - x0) / seg, dy: (y1 - y0) / seg }
    }
    rem -= seg
  }
  const n = p.length
  if (n < 2) return { x: p[0]?.[0] ?? 0, y: p[0]?.[1] ?? 0, dx: 1, dy: 0 }
  const seg = Math.hypot(p[n - 1][0] - p[n - 2][0], p[n - 1][1] - p[n - 2][1]) || 1
  return { x: p[n - 1][0], y: p[n - 1][1], dx: (p[n - 1][0] - p[n - 2][0]) / seg, dy: (p[n - 1][1] - p[n - 2][1]) / seg }
}

/** Offset a polyline sideways; positive distance = left of travel direction (world y-up). */
export function offset(p: Pt[], d: number): Pt[] {
  if (p.length < 2 || d === 0) return p.map((q) => [q[0], q[1]] as Pt)
  const normals: Pt[] = []
  for (let i = 1; i < p.length; i++) {
    const dx = p[i][0] - p[i - 1][0]
    const dy = p[i][1] - p[i - 1][1]
    const L = Math.hypot(dx, dy) || 1
    normals.push([-dy / L, dx / L])
  }
  const out: Pt[] = []
  for (let i = 0; i < p.length; i++) {
    let nx: number, ny: number
    if (i === 0) [nx, ny] = normals[0]
    else if (i === p.length - 1) [nx, ny] = normals[normals.length - 1]
    else {
      const a = normals[i - 1]
      const b = normals[i]
      nx = a[0] + b[0]
      ny = a[1] + b[1]
      const L = Math.hypot(nx, ny) || 1
      nx /= L
      ny /= L
      const cos = nx * a[0] + ny * a[1]
      const k = 1 / Math.max(0.35, cos)
      nx *= k
      ny *= k
    }
    out.push([p[i][0] + nx * d, p[i][1] + ny * d])
  }
  return out
}

/** Sub-polyline between arc lengths s0 and s1. */
export function slice(p: Pt[], s0: number, s1: number): Pt[] {
  const out: Pt[] = []
  const a = along(p, s0)
  out.push([a.x, a.y])
  let acc = 0
  for (let i = 1; i < p.length; i++) {
    const seg = Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1])
    acc += seg
    if (acc > s0 && acc < s1) out.push([p[i][0], p[i][1]])
  }
  const b = along(p, s1)
  out.push([b.x, b.y])
  return out
}

export function addPolyline(path: Path2D, p: Pt[]) {
  if (!p.length) return
  path.moveTo(p[0][0], p[0][1])
  for (let i = 1; i < p.length; i++) path.lineTo(p[i][0], p[i][1])
}

export function addPolygon(path: Path2D, p: Pt[]) {
  addPolyline(path, p)
  path.closePath()
}

/** Polygon for a strip of width w centred on a polyline. */
export function strip(p: Pt[], w: number): Pt[] {
  const l = offset(p, w / 2)
  const r = offset(p, -w / 2)
  return [...l, ...r.reverse()]
}

/** Quad in local frame (u along, v left) of a point/direction. */
export function rect(x: number, y: number, dx: number, dy: number, u0: number, u1: number, v0: number, v1: number): Pt[] {
  const nx = -dy
  const ny = dx
  const P = (u: number, v: number): Pt => [x + dx * u + nx * v, y + dy * u + ny * v]
  return [P(u0, v0), P(u1, v0), P(u1, v1), P(u0, v1)]
}

export function pointInPolygon(x: number, y: number, poly: Pt[]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]
    const [xj, yj] = poly[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi) inside = !inside
  }
  return inside
}

export function distToPolyline(x: number, y: number, p: Pt[]): number {
  let best = Infinity
  for (let i = 1; i < p.length; i++) {
    const [x0, y0] = p[i - 1]
    const [x1, y1] = p[i]
    const dx = x1 - x0
    const dy = y1 - y0
    const L2 = dx * dx + dy * dy || 1
    const t = Math.max(0, Math.min(1, ((x - x0) * dx + (y - y0) * dy) / L2))
    best = Math.min(best, Math.hypot(x - (x0 + dx * t), y - (y0 + dy * t)))
  }
  return best
}

export function convexHull(points: Pt[]): Pt[] {
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
  const lower: Pt[] = []
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop()
    lower.push(q)
  }
  const upper: Pt[] = []
  for (let i = p.length - 1; i >= 0; i--) {
    const q = p[i]
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop()
    upper.push(q)
  }
  upper.pop()
  lower.pop()
  return lower.concat(upper)
}

export function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}
