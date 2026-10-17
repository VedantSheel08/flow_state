// Canvas 2D renderer. World geometry is drawn as cached Path2D objects under a
// camera transform; text and badges are drawn in screen space for crispness.

import type { PersonView, VehicleView } from '../lib/frames'
import type { Layers } from '../lib/store'
import type { Incident, Tracked } from '../lib/types'
import type { Camera } from './camera'
import { along, hash, offset, polyLength, slice, strip, type Pt } from './geom'
import type { Scene } from './scene'
import { CODE_TO_TYPE, DIMS, cargoFor, paintFor, spriteBucket, spriteFor, type VType } from './vehicles'

export const C = {
  land: '#E8EAE6',
  residential: '#ECEDE8',
  commercial: '#E9EAEC',
  park: '#D5E5C8',
  school: '#ECEAE3',
  hospital: '#EBEBEE',
  police: '#E8E9EC',
  stadium: '#E3E5E8',
  sidewalk: '#DCDEE1',
  curb: '#B7BCC3',
  asphalt: '#61666E',
  asphaltJ: '#62676F',
  bike: '#6F9B7C',
  white: 'rgba(246,246,242,0.92)',
  yellow: '#E2B93B',
  shadow: 'rgba(31,41,55,0.16)',
  roofLine: '#B9BFC7',
  tree: '#A8C79A',
  treeLight: '#BCD7AE',
  treeShadow: 'rgba(35,55,35,0.13)',
  water: '#C3D9EA',
  label: '#4B5563',
}

const SIG: Record<string, string> = { G: '#22C55E', g: '#4ADE80', y: '#F5B700', Y: '#F5B700', r: '#EF4444', R: '#EF4444', u: '#F5B700', o: '#9CA3AF', O: '#9CA3AF', s: '#EF4444' }
const RANK: Record<string, number> = { G: 4, g: 3, y: 2, Y: 2, u: 2, r: 1, R: 1, s: 1, o: 0, O: 0 }

export interface RenderState {
  vehicles: VehicleView[]
  persons: PersonView[]
  tls: Record<string, string>
  layers: Layers
  selected: string | null
  selectedJunction: string | null
  hoverEdge: string | null
  pickedEdge: string | null
  tracked: Map<string, Tracked>
  incidents: Incident[]
  edges: Record<string, [number, number, number]>
  route: string[] | null
  busColors: Record<string, string>
  now: number
  pick: boolean
}

function busColor(id: string, colors: Record<string, string>): string {
  for (const [line, c] of Object.entries(colors)) if (id.startsWith(`bus${line}`)) return c
  return '#C8102E'
}

export function render(ctx: CanvasRenderingContext2D, scene: Scene, cam: Camera, st: RenderState, dpr: number) {
  const s = cam.scale
  const W = cam.width
  const H = cam.height
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = C.land
  ctx.fillRect(0, 0, W, H)
  // world transform: x right, y up
  const k = s * dpr
  ctx.setTransform(k, 0, 0, -k, (W / 2 - cam.x * s) * dpr, (H / 2 + cam.y * s) * dpr)
  const px = 1 / s // one CSS pixel in metres

  // ---- ground --------------------------------------------------------
  for (const [use, path] of scene.blocks) {
    ctx.fillStyle = (C as Record<string, string>)[use] ?? C.residential
    ctx.fill(path)
  }
  ctx.fillStyle = '#D6D9DD'
  ctx.fill(scene.parking)
  if (s > 2.5) {
    ctx.strokeStyle = 'rgba(255,255,255,0.75)'
    ctx.lineWidth = 0.12
    ctx.stroke(scene.parkingLines)
  }
  ctx.strokeStyle = '#E9E3D3'
  ctx.lineWidth = 2.2
  ctx.lineCap = 'round'
  ctx.stroke(scene.parkPaths)
  ctx.lineCap = 'butt'
  ctx.fillStyle = C.water
  ctx.fill(scene.water)
  ctx.fillStyle = '#CF967A'
  ctx.fill(scene.track)
  ctx.fillStyle = '#9CC38A'
  ctx.fill(scene.fields)
  ctx.fillStyle = '#DCDDDF'
  ctx.fill(scene.playground)
  ctx.fillStyle = '#D9DCE0'
  ctx.fill(scene.helipad)
  // stadium bowl
  ctx.fillStyle = '#E3E6EA'
  ctx.fill(scene.stadiumRing)
  ctx.strokeStyle = '#C5CAD1'
  ctx.lineWidth = 0.6
  ctx.stroke(scene.stadiumRing)
  ctx.fillStyle = '#86B874'
  ctx.fill(scene.stadiumField)
  ctx.strokeStyle = 'rgba(255,255,255,0.8)'
  ctx.lineWidth = 0.25
  ctx.stroke(scene.pitchLines)

  if (st.layers.buildings) {
    ctx.fillStyle = C.shadow
    ctx.fill(scene.buildingShadows)
  }

  // ---- streets -------------------------------------------------------
  ctx.lineJoin = 'round'
  ctx.strokeStyle = C.curb
  ctx.lineWidth = Math.max(0.3, px * 1.2)
  ctx.stroke(scene.sidewalks)
  ctx.fillStyle = C.sidewalk
  ctx.fill(scene.sidewalks)
  ctx.fillStyle = C.asphalt
  ctx.fill(scene.asphalt)
  ctx.fillStyle = C.asphaltJ
  ctx.fill(scene.junctionAsphalt)
  ctx.strokeStyle = C.curb
  ctx.stroke(scene.walkAreas)
  ctx.fillStyle = C.sidewalk
  ctx.fill(scene.walkAreas)
  ctx.fillStyle = C.bike
  ctx.fill(scene.bikePaint)
  ctx.fillStyle = 'rgba(97,102,110,1)'
  ctx.fill(scene.bikeBuffer)
  ctx.fillStyle = '#D3D6DA'
  ctx.fill(scene.medianCurb)
  ctx.fillStyle = '#B9D1A8'
  ctx.fill(scene.medianGrass)

  if (s > 0.9) {
    const mw = Math.max(0.13, px * 0.8)
    ctx.strokeStyle = C.yellow
    ctx.lineWidth = mw
    ctx.stroke(scene.yellow)
    ctx.lineWidth = Math.max(0.1, px * 0.6)
    ctx.stroke(scene.medianHatch)
    ctx.strokeStyle = C.white
    ctx.lineWidth = mw
    ctx.stroke(scene.whiteSolid)
    ctx.setLineDash([3, 9])
    ctx.stroke(scene.whiteDash)
    ctx.setLineDash([])
    ctx.fillStyle = C.white
    ctx.fill(scene.stopBars)
    ctx.fill(scene.zebra)
    if (s > 2.2) {
      ctx.lineWidth = 0.2
      ctx.stroke(scene.arrowStroke)
      ctx.fill(scene.arrowFill)
      ctx.strokeStyle = 'rgba(255,255,255,0.85)'
      ctx.setLineDash([1.2, 0.8])
      ctx.lineWidth = 0.12
      ctx.stroke(scene.busBox)
      ctx.setLineDash([])
    }
    if (s > 5) {
      ctx.strokeStyle = 'rgba(255,255,255,0.9)'
      ctx.lineWidth = 0.08
      ctx.stroke(scene.bikeSymbols)
      ctx.fillStyle = '#F8FAFC'
      for (const [x, y] of scene.bikePosts) {
        ctx.beginPath()
        ctx.arc(x, y, 0.09, 0, Math.PI * 2)
        ctx.fill()
      }
    }
  }

  // ---- traffic flow overlay -------------------------------------------
  if (st.layers.flow) {
    ctx.lineCap = 'round'
    for (const [eid, [n, , v]] of Object.entries(st.edges)) {
      const c = scene.edgeCenter.get(eid)
      if (!c || !n) continue
      const ratio = v / 13.89
      ctx.strokeStyle = ratio > 0.6 ? 'rgba(34,197,94,0.55)' : ratio > 0.3 ? 'rgba(245,158,11,0.65)' : 'rgba(239,68,68,0.7)'
      ctx.lineWidth = Math.min(c.w * 0.6, Math.max(1.2, px * 5))
      ctx.beginPath()
      ctx.moveTo(c.pts[0][0], c.pts[0][1])
      for (let i = 1; i < c.pts.length; i++) ctx.lineTo(c.pts[i][0], c.pts[i][1])
      ctx.stroke()
    }
    ctx.lineCap = 'butt'
  }

  // ---- incidents ------------------------------------------------------
  for (const inc of st.incidents) if (!inc.cleared) drawIncident(ctx, scene, inc, s, st.now)

  // ---- selected route -------------------------------------------------
  if (st.route && st.route.length) {
    ctx.strokeStyle = 'rgba(37,99,235,0.7)'
    ctx.lineWidth = Math.max(0.35, px * 2.5)
    ctx.lineCap = 'round'
    ctx.setLineDash([Math.max(1.5, px * 6), Math.max(1.5, px * 6)])
    for (const eid of st.route) {
      const c = scene.edgeCenter.get(eid)
      if (!c) continue
      ctx.beginPath()
      ctx.moveTo(c.pts[0][0], c.pts[0][1])
      for (let i = 1; i < c.pts.length; i++) ctx.lineTo(c.pts[i][0], c.pts[i][1])
      ctx.stroke()
    }
    ctx.setLineDash([])
    ctx.lineCap = 'butt'
  }

  // picked / hovered street (location picking)
  for (const [eid, col] of [
    [st.hoverEdge, 'rgba(37,99,235,0.35)'],
    [st.pickedEdge, 'rgba(37,99,235,0.6)'],
  ] as const) {
    if (!eid) continue
    const c = scene.edgeCenter.get(eid)
    if (!c) continue
    ctx.fillStyle = col
    ctx.beginPath()
    const poly = strip(c.pts, c.w)
    ctx.moveTo(poly[0][0], poly[0][1])
    for (const p of poly) ctx.lineTo(p[0], p[1])
    ctx.closePath()
    ctx.fill()
  }

  // ---- pedestrians ----------------------------------------------------
  if (st.layers.pedestrians && s > 1.6) {
    for (const p of st.persons) {
      const h = hash(p.id)
      const shirt = ['#334155', '#1E40AF', '#9F1239', '#047857', '#B45309', '#6B21A8', '#0F172A', '#64748B'][h % 8]
      const a = (p.angle * Math.PI) / 180
      const dx = Math.sin(a)
      const dy = Math.cos(a)
      ctx.fillStyle = 'rgba(255,255,255,0.9)'
      ctx.beginPath()
      ctx.arc(p.x, p.y, Math.max(0.42, px * 3.2), 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = shirt
      ctx.beginPath()
      const ps = Math.max(1, (px * 2.4) / 0.27)
      ctx.ellipse(p.x, p.y, 0.27 * ps, 0.16 * ps, Math.atan2(dx, -dy), 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = ['#3B2A20', '#1F1A17', '#6B4F3A', '#C9A27A', '#2D2D2D'][h % 5]
      ctx.beginPath()
      ctx.arc(p.x + dx * 0.04, p.y + dy * 0.04, 0.12 * ps, 0, Math.PI * 2)
      ctx.fill()
    }
  }

  // ---- buildings & trees (cast shadows over streets) --------------------
  if (st.layers.buildings) {
    for (const r of scene.roofs) {
      ctx.fillStyle = r.fill
      ctx.fill(r.path)
      ctx.strokeStyle = C.roofLine
      ctx.lineWidth = Math.max(0.15, px * 0.8)
      ctx.stroke(r.path)
    }
    if (s > 1.5) {
      ctx.strokeStyle = 'rgba(160,168,178,0.55)'
      ctx.lineWidth = Math.max(0.1, px * 0.6)
      ctx.stroke(scene.roofLines)
      ctx.fillStyle = 'rgba(205,210,216,0.7)'
      ctx.fill(scene.roofUnits)
    }
    ctx.fillStyle = 'rgba(201,214,226,0.9)'
    ctx.fill(scene.shelters)
    ctx.fillStyle = '#6B7280'
    ctx.fill(scene.shelterRoof)
  }

  // ---- vehicles -------------------------------------------------------
  drawVehicles(ctx, cam, st, dpr)

  if (st.layers.buildings) {
    ctx.setTransform(k, 0, 0, -k, (W / 2 - cam.x * s) * dpr, (H / 2 + cam.y * s) * dpr)
    if (s > 1.2) {
      ctx.fillStyle = C.treeShadow
      ctx.fill(scene.treeShadows)
    }
    ctx.globalAlpha = s > 1.2 ? 1 : 0.55
    ctx.fillStyle = C.tree
    ctx.fill(scene.treeCanopy)
    ctx.globalAlpha = 1
    if (s > 1.2) {
      ctx.fillStyle = C.treeLight
      ctx.fill(scene.treeLight)
    }
  }

  // ---- signals (overhead, above traffic) -------------------------------
  ctx.setTransform(k, 0, 0, -k, (W / 2 - cam.x * s) * dpr, (H / 2 + cam.y * s) * dpr)
  if (st.layers.signals && s > 1.4) {
    ctx.strokeStyle = '#4B525C'
    ctx.lineWidth = 0.2
    ctx.stroke(scene.mastArms)
    ctx.fillStyle = '#3F4650'
    ctx.fill(scene.poles)
    for (const h of scene.signals) {
      const state = st.tls[h.tls]
      let best = 'o'
      if (state) for (const li of h.links) if ((RANK[state[li]] ?? 0) > (RANK[best] ?? 0)) best = state[li]
      const nx = -h.dy
      const ny = h.dx
      ctx.fillStyle = '#20242B'
      ctx.beginPath()
      const hw = 0.55
      ctx.moveTo(h.x - h.dx * 0.28 + nx * hw, h.y - h.dy * 0.28 + ny * hw)
      ctx.lineTo(h.x + h.dx * 0.28 + nx * hw, h.y + h.dy * 0.28 + ny * hw)
      ctx.lineTo(h.x + h.dx * 0.28 - nx * hw, h.y + h.dy * 0.28 - ny * hw)
      ctx.lineTo(h.x - h.dx * 0.28 - nx * hw, h.y - h.dy * 0.28 - ny * hw)
      ctx.closePath()
      ctx.fill()
      const col = SIG[best] ?? '#9CA3AF'
      if (s > 4) {
        ctx.fillStyle = col.replace(')', ',0.28)').replace('rgb', 'rgba')
        ctx.globalAlpha = 0.35
        ctx.beginPath()
        ctx.arc(h.x, h.y, 0.75, 0, Math.PI * 2)
        ctx.fillStyle = col
        ctx.fill()
        ctx.globalAlpha = 1
      }
      ctx.fillStyle = col
      ctx.beginPath()
      ctx.arc(h.x, h.y, Math.max(0.3, px * 2.2), 0, Math.PI * 2)
      ctx.fill()
    }
    for (const sgn of scene.stopSigns) {
      octagon(ctx, sgn.x, sgn.y, 0.42)
      ctx.fillStyle = '#FFFFFF'
      ctx.fill()
      octagon(ctx, sgn.x, sgn.y, 0.36)
      ctx.fillStyle = '#C62828'
      ctx.fill()
    }
  }

  // junction highlight
  if (st.selectedJunction) {
    const j = scene.junctions.find((q) => q.id === st.selectedJunction)
    if (j) {
      ctx.strokeStyle = 'rgba(37,99,235,0.9)'
      ctx.lineWidth = Math.max(0.3, px * 2)
      ctx.setLineDash([px * 6, px * 4])
      ctx.beginPath()
      ctx.arc(j.x, j.y, 26, 0, Math.PI * 2)
      ctx.stroke()
      ctx.setLineDash([])
    }
  }

  // ---- screen-space overlays -------------------------------------------
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  if (st.layers.labels) drawLabels(ctx, scene, cam)
  drawTrackedTags(ctx, cam, st)
}

function octagon(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath()
  for (let i = 0; i < 8; i++) {
    const a = Math.PI / 8 + (i * Math.PI) / 4
    const px = x + Math.cos(a) * r
    const py = y + Math.sin(a) * r
    if (i === 0) ctx.moveTo(px, py)
    else ctx.lineTo(px, py)
  }
  ctx.closePath()
}

function drawVehicles(ctx: CanvasRenderingContext2D, cam: Camera, st: RenderState, dpr: number) {
  const s = cam.scale
  const ppm = spriteBucket(s * dpr)
  const blink = Math.floor(st.now / 380) % 2 === 0
  const flash = Math.floor(st.now / 160) % 2 === 0
  const margin = 20
  for (const v of st.vehicles) {
    const type: VType = CODE_TO_TYPE[v.code] ?? 'sedan'
    const [L, Wd] = DIMS[type]
    const [sx, sy] = cam.toScreen(v.x, v.y)
    if (sx < -margin * s || sy < -margin * s || sx > cam.width + margin * s || sy > cam.height + margin * s) continue
    const th = ((v.angle - 90) * Math.PI) / 180
    const cos = Math.cos(th)
    const sin = Math.sin(th)
    ctx.globalAlpha = v.alpha
    if (s < 1.6) {
      // overview: simple oriented rectangles in body colour
      ctx.setTransform(cos * dpr, sin * dpr, -sin * dpr, cos * dpr, sx * dpr, sy * dpr)
      ctx.fillStyle = type === 'bus' ? '#C8102E' : type === 'ambulance' || type === 'police' ? '#1D4ED8' : type === 'bike' ? '#0F766E' : '#334155'
      ctx.fillRect(-L * s, (-Wd / 2) * s, Math.max(2, L * s), Math.max(1.5, Wd * s))
      continue
    }
    const color = type === 'bus' ? '#F5F6F7' : paintFor(v.id, type)
    const extra = type === 'bus' ? busColor(v.id, st.busColors) : type === 'truck' ? cargoFor(v.id) : ''
    const sp = spriteFor(type, color, ppm, extra)
    const k = (s * dpr) / sp.ppm
    ctx.setTransform(cos * k, sin * k, -sin * k, cos * k, sx * dpr, sy * dpr)
    ctx.drawImage(sp.canvas, -sp.ox, -sp.oy)
    // dynamic lights in metre units (front bumper at x=0, left side at -y)
    const m = sp.ppm
    ctx.setTransform(cos * k * m, sin * k * m, -sin * k * m, cos * k * m, sx * dpr, sy * dpr)
    const sig = v.signals
    if (type !== 'bike' && type !== 'wreck') {
      if (sig & 8) {
        ctx.fillStyle = '#FF2D2D'
        ctx.shadowColor = 'rgba(255,40,40,0.9)'
        ctx.shadowBlur = 6 * dpr
        for (const side of [-1, 1]) ctx.fillRect(-L + 0.01, side * (Wd / 2 - 0.2) - 0.17, 0.12, 0.34)
        ctx.shadowBlur = 0
      }
      if (blink && sig & 3) {
        ctx.fillStyle = '#FFB020'
        for (const [bit, side] of [
          [1, 1],
          [2, -1],
        ] as const) {
          if (!(sig & bit)) continue
          ctx.fillRect(-0.16, side * (Wd / 2) - 0.14, 0.14, 0.28)
          ctx.fillRect(-L + 0.02, side * (Wd / 2) - 0.14, 0.14, 0.28)
        }
      }
    }
    const tr = st.tracked.get(v.id)
    const beacons = (type === 'ambulance' || type === 'police') && (tr ? tr.priority !== false && tr.status !== 'returning' && tr.status !== 'completed' : (sig & 2048) !== 0)
    if (beacons || type === 'wreck') {
      const bx = type === 'ambulance' ? -1.75 : -L * 0.47
      const colors = type === 'police' ? ['#2563EB', '#EF4444'] : type === 'wreck' ? ['#F59E0B', '#F59E0B'] : ['#EF4444', '#F8FAFC']
      for (const [i, side] of [-1, 1].entries()) {
        const on = type === 'wreck' ? blink : flash === (i === 0)
        ctx.fillStyle = on ? colors[i] : '#4B5563'
        if (on) {
          ctx.shadowColor = colors[i]
          ctx.shadowBlur = 10 * dpr
        }
        ctx.fillRect(bx - 0.18, side > 0 ? 0.02 : -0.5, 0.36, 0.48)
        ctx.shadowBlur = 0
      }
    }
    if (st.selected === v.id) {
      ctx.globalAlpha = 1
      ctx.strokeStyle = '#2563EB'
      ctx.lineWidth = 2.2 / (k * m) * dpr
      ctx.beginPath()
      ctx.roundRect(-L - 0.5, -Wd / 2 - 0.5, L + 1, Wd + 1, 0.6)
      ctx.stroke()
    }
  }
  ctx.globalAlpha = 1
  ctx.shadowBlur = 0
}

function drawIncident(ctx: CanvasRenderingContext2D, scene: Scene, inc: Incident, s: number, now: number) {
  const lanes = inc.lanes.map((i) => scene.lanes.get(`${inc.edge}_${i}`)).filter(Boolean) as { pts: Pt[]; w: number }[]
  if (!lanes.length) return
  const ref = lanes[0].pts
  const L = polyLength(ref)
  const cone = (x: number, y: number, r = 0.32) => {
    ctx.fillStyle = '#F97316'
    ctx.beginPath()
    ctx.arc(x, y, r, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#FFFFFF'
    ctx.beginPath()
    ctx.arc(x, y, r * 0.45, 0, Math.PI * 2)
    ctx.fill()
  }
  if (inc.kind === 'construction') {
    const u0 = Math.max(0, inc.pos)
    const u1 = Math.min(L, inc.pos + inc.length)
    for (const ln of lanes) {
      const seg = slice(ln.pts, u0, u1)
      const poly = strip(seg, ln.w - 0.2)
      ctx.fillStyle = 'rgba(249,115,22,0.18)'
      ctx.beginPath()
      poly.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
      ctx.closePath()
      ctx.fill()
      // hatch
      ctx.save()
      ctx.clip()
      ctx.strokeStyle = 'rgba(249,115,22,0.55)'
      ctx.lineWidth = 0.35
      for (let u = u0; u < u1; u += 2.5) {
        const a = along(ln.pts, u)
        ctx.beginPath()
        ctx.moveTo(a.x - a.dy * 2 - a.dx * 2, a.y + a.dx * 2 - a.dy * 2)
        ctx.lineTo(a.x + a.dy * 2 + a.dx * 2, a.y - a.dx * 2 + a.dy * 2)
        ctx.stroke()
      }
      ctx.restore()
      // barrels along both edges + taper at the start
      if (s > 2) {
        for (const side of [1, -1]) {
          const edge = offset(ln.pts, (side * (ln.w - 0.5)) / 2)
          for (let u = u0 + 4; u < u1; u += 7) {
            const a = along(edge, u)
            cone(a.x, a.y, 0.36)
          }
        }
      }
    }
    if (inc.closed) {
      const a = along(ref, u0 + 1)
      ctx.strokeStyle = '#F97316'
      ctx.lineWidth = 0.45
      const span = lanes.reduce((acc, l) => acc + l.w, 0)
      ctx.beginPath()
      ctx.moveTo(a.x - a.dy * -0.8, a.y + a.dx * -0.8)
      ctx.lineTo(a.x - a.dy * (span - 0.8), a.y + a.dx * (span - 0.8))
      ctx.setLineDash([0.9, 0.9])
      ctx.stroke()
      ctx.setLineDash([])
    }
    return
  }
  // accident: red zone around the wrecks, cone taper upstream
  const u = inc.pos
  for (const ln of lanes) {
    const seg = slice(ln.pts, Math.max(0, u - 3), Math.min(L, u + inc.length + 4))
    const poly = strip(seg, ln.w)
    ctx.fillStyle = `rgba(239,68,68,${0.12 + 0.06 * Math.sin(now / 300)})`
    ctx.beginPath()
    poly.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
    ctx.closePath()
    ctx.fill()
  }
  if (s > 1.8) {
    const outer = lanes[0]
    const taperLen = 22
    for (let d = 0; d <= taperLen; d += 2.75) {
      const t = d / taperLen
      const a = along(outer.pts, Math.max(0, u - taperLen + d - 2))
      const w = lanes.reduce((acc, l) => acc + l.w, 0)
      const v = -outer.w / 2 + 0.3 + (w - 0.6) * t
      cone(a.x - a.dy * v, a.y + a.dx * v)
    }
  }
}

function drawLabels(ctx: CanvasRenderingContext2D, scene: Scene, cam: Camera) {
  const s = cam.scale
  if (s < 0.6) return
  const close = s > 3.2
  ctx.font = `500 ${close ? 12 : 11}px Inter, system-ui, sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  const drawn: [number, number][] = []
  const byText = new Map<string, [number, number][]>()
  for (const lb of scene.labels) {
    const off = close ? lb.half + 2.2 : 0
    const wx = lb.angle === 90 ? lb.x + off : lb.x
    const wy = lb.angle === 90 ? lb.y : lb.y + off
    const [sx, sy] = cam.toScreen(wx, wy)
    if (sx < -100 || sy < -100 || sx > cam.width + 100 || sy > cam.height + 100) continue
    if (drawn.some(([x, y]) => Math.abs(x - sx) < 160 && Math.abs(y - sy) < 24)) continue
    const same = byText.get(lb.text) ?? []
    if (same.some(([x, y]) => Math.hypot(x - sx, y - sy) < (close ? 380 : 520))) continue
    same.push([sx, sy])
    byText.set(lb.text, same)
    drawn.push([sx, sy])
    ctx.save()
    ctx.translate(sx, sy)
    if (lb.angle === 90) ctx.rotate(-Math.PI / 2)
    ctx.lineWidth = 3.5
    ctx.strokeStyle = close ? 'rgba(236,237,232,0.95)' : 'rgba(97,102,110,0.95)'
    ctx.fillStyle = close ? '#4B5563' : '#F3F4F6'
    ctx.lineJoin = 'round'
    ctx.strokeText(lb.text, 0, 0)
    ctx.fillText(lb.text, 0, 0)
    ctx.restore()
  }
  // points of interest
  if (s > 0.9) {
    const icon: Record<string, [string, string]> = {
      hospital: ['H', '#DC2626'],
      police: ['P', '#1D4ED8'],
      school: ['S', '#B45309'],
      stadium: ['★', '#0F766E'],
      park: ['♣', '#15803D'],
    }
    for (const p of scene.pois) {
      const [sx, sy] = cam.toScreen(p.x, p.y)
      const [g, col] = icon[p.kind] ?? ['•', '#475569']
      ctx.fillStyle = '#FFFFFF'
      ctx.strokeStyle = 'rgba(15,23,42,0.12)'
      ctx.lineWidth = 1
      ctx.font = '600 12px Inter, system-ui, sans-serif'
      const tw = ctx.measureText(p.name).width
      ctx.beginPath()
      ctx.roundRect(sx - 11, sy - 11, tw + 34, 22, 11)
      ctx.fill()
      ctx.stroke()
      ctx.fillStyle = col
      ctx.beginPath()
      ctx.arc(sx, sy, 8, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = '#FFFFFF'
      ctx.font = '700 10px Inter, system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(g, sx, sy + 0.5)
      ctx.fillStyle = '#1F2937'
      ctx.font = '600 12px Inter, system-ui, sans-serif'
      ctx.textAlign = 'left'
      ctx.fillText(p.name, sx + 14, sy + 0.5)
      ctx.textAlign = 'center'
    }
  }
}

function drawTrackedTags(ctx: CanvasRenderingContext2D, cam: Camera, st: RenderState) {
  if (!st.tracked.size) return
  ctx.font = '600 11px Inter, system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (const v of st.vehicles) {
    const tr = st.tracked.get(v.id)
    if (!tr || tr.kind === 'bus') continue
    const [sx, sy] = cam.toScreen(v.x, v.y)
    const text = tr.label
    const w = ctx.measureText(text).width + 14
    const y = sy - Math.max(16, cam.scale * 3.5)
    const col = tr.kind === 'suspect' ? '#B91C1C' : tr.kind === 'police' ? '#1D4ED8' : '#B91C1C'
    ctx.fillStyle = '#FFFFFF'
    ctx.strokeStyle = 'rgba(15,23,42,0.15)'
    ctx.beginPath()
    ctx.roundRect(sx - w / 2, y - 10, w, 20, 6)
    ctx.fill()
    ctx.stroke()
    ctx.fillStyle = col
    ctx.beginPath()
    ctx.arc(sx - w / 2 + 8, y, 3, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#111827'
    ctx.fillText(text, sx + 3, y + 0.5)
  }
}
