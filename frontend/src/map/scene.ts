// Converts the SUMO network geometry into drawable Path2D layers (world metres).
// Built once per network; the renderer only sets a transform and fills/strokes.

import type { NetworkGeom } from '../lib/types'
import { addPolygon, addPolyline, along, convexHull, hash, offset, polyLength, rect, slice, strip, toPts, type Pt } from './geom'

export interface SignalHead {
  tls: string
  links: number[]
  x: number
  y: number
  dx: number
  dy: number
  w: number
}

export interface LaneHit {
  id: string
  edge: string
  pts: Pt[]
  w: number
  role: string
}

export interface StreetLabel {
  text: string
  x: number
  y: number
  angle: number
  half: number
  cls: string
}

export interface Scene {
  id: string
  bounds: [number, number, number, number]
  roadBounds: [number, number, number, number]
  focus: [number, number]
  blocks: Map<string, Path2D>
  parking: Path2D
  parkingLines: Path2D
  parkPaths: Path2D
  water: Path2D
  fields: Path2D
  track: Path2D
  stadiumRing: Path2D
  stadiumField: Path2D
  pitchLines: Path2D
  helipad: Path2D
  playground: Path2D
  sidewalks: Path2D
  walkAreas: Path2D
  asphalt: Path2D
  junctionAsphalt: Path2D
  bikePaint: Path2D
  bikeBuffer: Path2D
  bikePosts: Pt[]
  yellow: Path2D
  medianHatch: Path2D
  medianCurb: Path2D
  medianGrass: Path2D
  medianTrees: [number, number, number][]
  whiteDash: Path2D
  whiteSolid: Path2D
  stopBars: Path2D
  zebra: Path2D
  arrowStroke: Path2D
  arrowFill: Path2D
  bikeSymbols: Path2D
  busBox: Path2D
  shelters: Path2D
  shelterRoof: Path2D
  buildingShadows: Path2D
  roofs: { path: Path2D; fill: string }[]
  roofLines: Path2D
  roofUnits: Path2D
  treeShadows: Path2D
  treeCanopy: Path2D
  treeLight: Path2D
  signals: SignalHead[]
  mastArms: Path2D
  poles: Path2D
  stopSigns: { x: number; y: number }[]
  lanes: Map<string, { pts: Pt[]; w: number; edge: string; role: string }>
  edgeCenter: Map<string, { pts: Pt[]; w: number }>
  hitLanes: LaneHit[]
  junctions: { id: string; name: string; x: number; y: number; poly: Pt[]; control: string }[]
  labels: StreetLabel[]
  pois: { id: string; kind: string; name: string; x: number; y: number }[]
}

const ROOF: Record<string, string[]> = {
  residential: ['#E9E2D8', '#E2DBD0', '#EEE8DF', '#DDD5C9', '#E6DED2'],
  commercial: ['#DFE3E8', '#D8DDE3', '#E6E9ED', '#D2D8DF', '#E1E4E8'],
  hospital: ['#F1F2F4'],
  police: ['#E8EBF0'],
  school: ['#F1ECE4'],
}

function arrow(fill: Path2D, stroke: Path2D, x: number, y: number, dx: number, dy: number, dirs: string) {
  // local frame: u along lane (tail at 0, tip ≈ 5 m), v to the left
  const nx = -dy
  const ny = dx
  const P = (u: number, v: number): Pt => [x + dx * u + nx * v, y + dy * u + ny * v]
  const has = (c: string) => dirs.includes(c)
  const straight = has('s')
  const left = has('l') || has('L')
  const right = has('r') || has('R')
  if (!straight && !left && !right) return
  const shaftEnd = straight ? 3.3 : 2.1
  stroke.moveTo(...P(0, 0))
  stroke.lineTo(...P(shaftEnd, 0))
  if (straight) addPolygon(fill, [P(3.1, 0.52), P(5.0, 0), P(3.1, -0.52)])
  for (const [on, sgn] of [
    [left, 1],
    [right, -1],
  ] as const) {
    if (!on) continue
    const from = P(straight ? 1.6 : shaftEnd, 0)
    const c = P(3.0, 0)
    const to = P(3.0, 0.75 * sgn)
    stroke.moveTo(from[0], from[1])
    stroke.quadraticCurveTo(c[0], c[1], to[0], to[1])
    addPolygon(fill, [P(2.45, 0.7 * sgn), P(3.0, 1.75 * sgn), P(3.55, 0.7 * sgn)])
  }
}

function bikeGlyph(path: Path2D, x: number, y: number, dx: number, dy: number) {
  const nx = -dy
  const ny = dx
  const P = (u: number, v: number): Pt => [x + dx * u + nx * v, y + dy * u + ny * v]
  // two wheels (circles approximated) + frame, drawn in the bike lane
  for (const u of [-0.55, 0.55]) {
    const c = P(u, 0)
    path.moveTo(c[0] + 0.32, c[1])
    path.arc(c[0], c[1], 0.32, 0, Math.PI * 2)
  }
  addPolyline(path, [P(-0.55, 0), P(-0.1, 0), P(0.3, 0.0), P(0.55, 0)])
  addPolyline(path, [P(-0.1, 0), P(0.05, 0.0), P(0.35, 0)])
  // arrow ahead
  addPolyline(path, [P(1.4, 0), P(2.6, 0)])
  addPolyline(path, [P(2.2, 0.3), P(2.6, 0), P(2.2, -0.3)])
}

export function buildScene(net: NetworkGeom): Scene {
  const s: Scene = {
    id: net.id,
    bounds: net.bounds,
    roadBounds: net.roadBounds,
    focus: net.focus,
    blocks: new Map(),
    parking: new Path2D(),
    parkingLines: new Path2D(),
    parkPaths: new Path2D(),
    water: new Path2D(),
    fields: new Path2D(),
    track: new Path2D(),
    stadiumRing: new Path2D(),
    stadiumField: new Path2D(),
    pitchLines: new Path2D(),
    helipad: new Path2D(),
    playground: new Path2D(),
    sidewalks: new Path2D(),
    walkAreas: new Path2D(),
    asphalt: new Path2D(),
    junctionAsphalt: new Path2D(),
    bikePaint: new Path2D(),
    bikeBuffer: new Path2D(),
    bikePosts: [],
    yellow: new Path2D(),
    medianHatch: new Path2D(),
    medianCurb: new Path2D(),
    medianGrass: new Path2D(),
    medianTrees: [],
    whiteDash: new Path2D(),
    whiteSolid: new Path2D(),
    stopBars: new Path2D(),
    zebra: new Path2D(),
    arrowStroke: new Path2D(),
    arrowFill: new Path2D(),
    bikeSymbols: new Path2D(),
    busBox: new Path2D(),
    shelters: new Path2D(),
    shelterRoof: new Path2D(),
    buildingShadows: new Path2D(),
    roofs: [],
    roofLines: new Path2D(),
    roofUnits: new Path2D(),
    treeShadows: new Path2D(),
    treeCanopy: new Path2D(),
    treeLight: new Path2D(),
    signals: [],
    mastArms: new Path2D(),
    poles: new Path2D(),
    stopSigns: [],
    lanes: new Map(),
    edgeCenter: new Map(),
    hitLanes: [],
    junctions: [],
    labels: [],
    pois: [],
  }
  const city = net.city

  // ---- land use -------------------------------------------------------
  for (const b of city.blocks) {
    const key = b.use
    if (!s.blocks.has(key)) s.blocks.set(key, new Path2D())
    addPolygon(s.blocks.get(key)!, b.shape as Pt[])
  }
  for (const f of city.features as Record<string, unknown>[]) {
    const kind = f.kind as string
    if (kind === 'parking') {
      const sh = f.shape as Pt[]
      addPolygon(s.parking, sh)
      const xs = sh.map((p) => p[0])
      const ys = sh.map((p) => p[1])
      const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
      // stall rows (2.6 m stalls, 5.5 m deep, 7 m aisles)
      for (let y = y0 + 1; y + 5.5 < y1; y += 18) {
        for (let x = x0 + 1.5; x < x1 - 1.5; x += 2.6) {
          s.parkingLines.moveTo(x, y)
          s.parkingLines.lineTo(x, y + 5.2)
          if (y + 12.5 + 5.2 < y1) {
            s.parkingLines.moveTo(x, y + 12.5)
            s.parkingLines.lineTo(x, y + 12.5 + 5.2)
          }
        }
      }
    } else if (kind === 'park') {
      /* drawn as block use */
    } else if (kind === 'path') {
      addPolyline(s.parkPaths, f.points as Pt[])
    } else if (kind === 'pond') {
      s.water.ellipse(f.x as number, f.y as number, f.rx as number, f.ry as number, 0, 0, Math.PI * 2)
    } else if (kind === 'stadium') {
      const [x, y, rx, ry] = [f.x, f.y, f.rx, f.ry] as number[]
      s.stadiumRing.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2)
      s.stadiumField.rect(x - rx * 0.5, y - ry * 0.42, rx, ry * 0.84)
      s.pitchLines.rect(x - rx * 0.46, y - ry * 0.38, rx * 0.92, ry * 0.76)
      s.pitchLines.moveTo(x, y - ry * 0.38)
      s.pitchLines.lineTo(x, y + ry * 0.38)
      s.pitchLines.moveTo(x + ry * 0.14, y)
      s.pitchLines.arc(x, y, ry * 0.14, 0, Math.PI * 2)
    } else if (kind === 'track') {
      const [x, y, rx, ry] = [f.x, f.y, f.rx, f.ry] as number[]
      s.track.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2)
      s.fields.ellipse(x, y, rx - 5, ry - 5, 0, 0, Math.PI * 2)
    } else if (kind === 'helipad') {
      const [x, y, r] = [f.x, f.y, f.r] as number[]
      s.helipad.moveTo(x + r, y)
      s.helipad.arc(x, y, r, 0, Math.PI * 2)
    } else if (kind === 'playground') {
      addPolygon(s.playground, f.shape as Pt[])
    }
  }

  // ---- buildings ------------------------------------------------------
  const roofPaths = new Map<string, Path2D>()
  for (const b of city.buildings) {
    const sh = b.shape as Pt[]
    const k = 0.3
    const moved = sh.map(([x, y]) => [x + b.h * k, y - b.h * k] as Pt)
    addPolygon(s.buildingShadows, convexHull([...sh, ...moved]))
    const palette = ROOF[b.use] ?? ROOF.commercial
    const fill = palette[Math.floor(b.tone * palette.length) % palette.length]
    if (!roofPaths.has(fill)) roofPaths.set(fill, new Path2D())
    addPolygon(roofPaths.get(fill)!, sh)
    // parapet + rooftop plant on taller buildings
    const xs = sh.map((p) => p[0])
    const ys = sh.map((p) => p[1])
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    if (b.h > 12 && x1 - x0 > 6 && y1 - y0 > 6) {
      s.roofLines.rect(x0 + 0.7, y0 + 0.7, x1 - x0 - 1.4, y1 - y0 - 1.4)
      const hseed = hash(`${x0},${y0}`)
      const n = 1 + (hseed % 3)
      for (let i = 0; i < n; i++) {
        const w = 1.2 + ((hseed >>> (i * 3)) % 4) * 0.35
        const ux = x0 + 2 + (((hseed >>> (i * 5)) % 100) / 100) * Math.max(0, x1 - x0 - 4 - w)
        const uy = y0 + 2 + (((hseed >>> (i * 7 + 2)) % 100) / 100) * Math.max(0, y1 - y0 - 4 - w)
        s.roofUnits.rect(ux, uy, w, w * 0.8)
      }
    }
  }
  s.roofs = [...roofPaths.entries()].map(([fill, path]) => ({ fill, path }))

  const addTree = (x: number, y: number, r: number) => {
    s.treeShadows.moveTo(x + r * 0.35 + r, y - r * 0.35)
    s.treeShadows.arc(x + r * 0.35, y - r * 0.35, r, 0, Math.PI * 2)
    s.treeCanopy.moveTo(x + r, y)
    s.treeCanopy.arc(x, y, r, 0, Math.PI * 2)
    s.treeLight.moveTo(x - r * 0.25 + r * 0.55, y + r * 0.25)
    s.treeLight.arc(x - r * 0.25, y + r * 0.25, r * 0.55, 0, Math.PI * 2)
  }
  for (const [x, y, r] of city.trees) addTree(x, y, r)

  // ---- roads ----------------------------------------------------------
  const streetHalf = new Map<string, number>()
  const edgeById = new Map(net.edges.map((e) => [e.id, e]))
  for (const e of net.edges) {
    const lanes = [...e.lanes].sort((a, b) => a.i - b.i)
    if (e.street) streetHalf.set(e.street, lanes.reduce((acc, l) => acc + l.w, 0))
    const carLanes = lanes.filter((l) => l.role === 'car' || l.role === 'bus')
    let centerAcc: Pt[] | null = null
    for (let k = 0; k < lanes.length; k++) {
      const ln = lanes[k]
      const pts = toPts(ln.shape)
      if (pts.length < 2) continue
      s.lanes.set(ln.id, { pts, w: ln.w, edge: e.id, role: ln.role })
      s.hitLanes.push({ id: ln.id, edge: e.id, pts, w: ln.w, role: ln.role })
      const poly = strip(pts, ln.w)
      if (ln.role === 'sidewalk') addPolygon(s.sidewalks, poly)
      else addPolygon(s.asphalt, poly)
      if (ln.role === 'bike') addPolygon(s.bikePaint, strip(pts, ln.w - 0.1))
      if (ln.role === 'car' && !centerAcc) centerAcc = pts
      const L = polyLength(pts)
      const left = offset(pts, ln.w / 2)
      const next = lanes[k + 1]
      // markings on the left boundary of this lane
      if (ln.role === 'median') {
        // raised planted median (boulevard), cut back where the turn bay opens
        const inner = slice(pts, Math.min(3, L / 4), Math.max(L - 3, (L * 3) / 4))
        addPolygon(s.medianCurb, strip(inner, ln.w - 0.5))
        addPolygon(s.medianGrass, strip(inner, ln.w - 1.0))
        for (let u = 9; u < L - 8; u += 11) {
          const a = along(pts, u)
          s.medianTrees.push([a.x, a.y, 1.6 + (hash(`${ln.id}${u}`) % 5) * 0.12])
        }
      } else if (!next) {
        if (ln.role !== 'sidewalk' && e.street) addPolyline(s.yellow, offset(pts, ln.w / 2 - 0.14))
      } else if (ln.role === 'bike' && next.role !== 'sidewalk') {
        if (e.cls === 'arterial_bike') {
          // protected lane: buffer line + flexible delineator posts
          addPolyline(s.whiteSolid, left)
          addPolyline(s.whiteSolid, offset(pts, ln.w / 2 - 0.55))
          addPolygon(s.bikeBuffer, strip(offset(pts, ln.w / 2 - 0.27), 0.5))
          for (let u = 6; u < L - 10; u += 4) {
            const a = along(offset(pts, ln.w / 2 - 0.27), u)
            s.bikePosts.push([a.x, a.y])
          }
        } else addPolyline(s.whiteSolid, left)
      } else if (ln.role === 'car' && next.role === 'median') {
        addPolyline(s.yellow, offset(pts, ln.w / 2 - 0.14))
      } else if (ln.role === 'car' && next.role === 'car') {
        const solidFromEnd = ln.control ? 22 : 0
        const bayLine = e.part === 'bay' && k + 1 === lanes.length - 1
        if (bayLine) addPolyline(s.whiteSolid, left)
        else {
          if (L - solidFromEnd > 1) addPolyline(s.whiteDash, slice(left, 0, L - solidFromEnd))
          if (solidFromEnd) addPolyline(s.whiteSolid, slice(left, Math.max(0, L - solidFromEnd), L))
        }
      }
      // stop bar, arrows, bike symbols
      if (ln.control && (ln.role === 'car' || ln.role === 'bike' || ln.role === 'bus')) {
        const end = along(pts, L - 0.35)
        addPolygon(s.stopBars, rect(end.x, end.y, end.dx, end.dy, -0.45, 0, -ln.w / 2 + 0.08, ln.w / 2 - 0.08))
        if (ln.role === 'car' && L > 22 && ln.dirs) {
          const a = along(pts, L - 9.5)
          arrow(s.arrowFill, s.arrowStroke, a.x, a.y, a.dx, a.dy, ln.dirs)
        }
      }
      if (ln.role === 'bike' && L > 30) {
        for (let u = 12; u < L - 12; u += 55) {
          const a = along(pts, u)
          bikeGlyph(s.bikeSymbols, a.x, a.y, a.dx, a.dy)
        }
      }
    }
    if (centerAcc) {
      const carW = carLanes.reduce((a, l) => a + l.w, 0)
      const firstCar = carLanes[0]
      if (firstCar) {
        const fp = toPts(firstCar.shape)
        s.edgeCenter.set(e.id, { pts: offset(fp, (carW - firstCar.w) / 2), w: carW })
      }
    }
  }

  for (const [x, y, r] of s.medianTrees) addTree(x, y, r)

  // bus stop boxes and shelters
  for (const st of city.busStops) {
    const lane = s.lanes.get(st.lane)
    const e = edgeById.get(st.edge)
    if (!lane || !e) continue
    const seg = slice(lane.pts, st.start, st.end)
    const box = strip(seg, lane.w - 0.3)
    addPolygon(s.busBox, box)
    const sw = e.lanes.find((l) => l.role === 'sidewalk')
    if (sw) {
      const swp = toPts(sw.shape)
      const mid = along(swp, (st.start + st.end) / 2)
      addPolygon(s.shelters, rect(mid.x, mid.y, mid.dx, mid.dy, -2.4, 2.4, -sw.w / 2 + 0.35, -sw.w / 2 + 1.55))
      addPolygon(s.shelterRoof, rect(mid.x, mid.y, mid.dx, mid.dy, -2.4, 2.4, -sw.w / 2 + 1.25, -sw.w / 2 + 1.55))
    }
  }

  for (const j of net.junctions) {
    if (j.type === 'internal') continue
    const poly = toPts(j.shape)
    if (poly.length >= 3) addPolygon(s.junctionAsphalt, poly)
    if (j.name) s.junctions.push({ id: j.id, name: j.name, x: j.x, y: j.y, poly, control: j.type })
  }
  for (const w of net.walkingareas) {
    const poly = toPts(w.shape)
    if (poly.length >= 3) addPolygon(s.walkAreas, poly)
  }
  // continental crosswalks: 0.6 m bars every 1.2 m along the crossing
  for (const c of net.crossings) {
    const pts = toPts(c.shape)
    if (pts.length < 2) continue
    const L = polyLength(pts)
    const n = Math.floor((L - 0.6) / 1.2)
    const start = (L - (n * 1.2 - 0.6)) / 2
    for (let i = 0; i < n; i++) {
      const u = start + i * 1.2
      const a = along(pts, u)
      addPolygon(s.zebra, rect(a.x, a.y, a.dx, a.dy, 0, 0.6, -c.w / 2 + 0.25, c.w / 2 - 0.25))
    }
  }

  // signals: overhead heads per controlled lane on a mast arm from the right curb
  const approaches = new Map<string, { lanes: typeof net.edges[number]['lanes']; e: (typeof net.edges)[number] }>()
  for (const e of net.edges) {
    const ctl = e.lanes.filter((l) => l.control === 'tls')
    if (ctl.length) approaches.set(e.id, { lanes: ctl, e })
    const stops = e.lanes.filter((l) => l.control === 'stop' && l.role === 'car')
    if (stops.length) {
      const first = [...stops].sort((a, b) => a.i - b.i)[0]
      const pts = toPts(first.shape)
      const L = polyLength(pts)
      const a = along(pts, L - 1.2)
      const sw = e.lanes.find((l) => l.role === 'sidewalk')
      const out = first.w / 2 + (sw ? sw.w * 0.55 : 1.2) + (e.lanes.find((l) => l.role === 'bike')?.w ?? 0)
      s.stopSigns.push({ x: a.x + a.dy * out, y: a.y - a.dx * out })
    }
  }
  for (const { lanes, e } of approaches.values()) {
    const sorted = [...lanes].sort((a, b) => a.i - b.i)
    const all = [...e.lanes].sort((a, b) => a.i - b.i)
    const ref = toPts(sorted[0].shape)
    const L = polyLength(ref)
    const at = along(ref, L - 0.9)
    // pole on the sidewalk, arm across to the leftmost controlled lane
    const sw = all.find((l) => l.role === 'sidewalk')
    const rightOff = -(sorted[0].w / 2) - (all.filter((l) => l.role === 'bike' && l.i < sorted[0].i).reduce((a, l) => a + l.w, 0)) - (sw ? sw.w * 0.45 : 1)
    let leftOff = 0
    for (const l of sorted) {
      const p = toPts(l.shape)
      const a2 = along(p, polyLength(p) - 0.9)
      const off = (a2.x - at.x) * -at.dy + (a2.y - at.y) * at.dx
      leftOff = Math.max(leftOff, off)
      s.signals.push({ tls: l.tls!, links: l.links, x: a2.x, y: a2.y, dx: a2.dx, dy: a2.dy, w: l.w })
    }
    const nx = -at.dy
    const ny = at.dx
    s.mastArms.moveTo(at.x + nx * rightOff, at.y + ny * rightOff)
    s.mastArms.lineTo(at.x + nx * leftOff, at.y + ny * leftOff)
    const px = at.x + nx * rightOff
    const py = at.y + ny * rightOff
    s.poles.moveTo(px + 0.28, py)
    s.poles.arc(px, py, 0.28, 0, Math.PI * 2)
  }

  // street labels: on the centre line at low zoom, on the sidewalk when close
  for (const lb of city.labels) {
    const street = city.streets.find((st) => st.name === lb.text)
    s.labels.push({ text: lb.text, x: lb.x, y: lb.y, angle: lb.angle, half: street ? (streetHalf.get(street.id) ?? 8) : 8, cls: lb.cls })
  }
  s.pois = city.pois.map((p) => ({ id: p.id, kind: p.kind, name: p.name, x: p.x, y: p.y }))
  return s
}
