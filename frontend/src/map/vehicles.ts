// Procedural top-down vehicle artwork.
//
// Each vehicle class is painted in vector form at real-world dimensions (the
// same lengths/widths SUMO simulates) into an off-screen sprite, cached per
// (type, colour, resolution bucket). Lights (brake, indicators, beacons) are
// drawn live on top from SUMO's signal bitmask.
//
// Sprite frame: rear bumper at x = 0, front bumper at x = L, y = 0 on the
// centre line, +y to the vehicle's right.

import { atlas, type AtlasRect } from './atlas'
import { hash } from './geom'

export type VType = 'sedan' | 'hatch' | 'suv' | 'pickup' | 'van' | 'truck' | 'bus' | 'bike' | 'ambulance' | 'police' | 'suspect' | 'wreck'

export const CODE_TO_TYPE: VType[] = ['sedan', 'suv', 'truck', 'bus', 'bike', 'ambulance', 'police', 'suspect', 'wreck', 'hatch', 'van', 'pickup']

export const DIMS: Record<VType, [number, number]> = {
  sedan: [4.75, 1.84],
  hatch: [4.15, 1.77],
  suv: [4.85, 1.95],
  pickup: [5.6, 2.05],
  van: [5.4, 2.02],
  truck: [8.6, 2.5],
  bus: [12.2, 2.55],
  bike: [1.75, 0.65],
  ambulance: [6.6, 2.3],
  police: [5.05, 1.95],
  suspect: [4.75, 1.84],
  wreck: [4.75, 1.84],
}

/** Emergency light bars: centre position from the rear (m), length along the vehicle and span across it. */
export const LIGHTBAR: Partial<Record<VType, { x: number; len: number; span: number; colors: [string, string] }>> = {
  police: { x: 2.5, len: 0.32, span: 1.5, colors: ['#EF4444', '#2563EB'] },
  ambulance: { x: 5.0, len: 0.3, span: 1.9, colors: ['#EF4444', '#F8FAFC'] },
}

// Real-world paint distribution (white/black/grey/silver dominate)
const PAINTS: [string, number][] = [
  ['#F3F4F5', 22], ['#E4E6E8', 5], ['#1D2127', 17], ['#33373E', 5], ['#6A7079', 12], ['#A7ADB4', 12],
  ['#24395E', 6], ['#3E6CB0', 5], ['#9E2A2F', 7], ['#6C2430', 2], ['#C7BB9E', 3], ['#3D5846', 2], ['#5A4535', 2],
]
const PAINT_TOTAL = PAINTS.reduce((a, p) => a + p[1], 0)
const JERSEYS = ['#2563EB', '#DC2626', '#F59E0B', '#0F766E', '#111827', '#7C3AED', '#E5E7EB', '#16A34A']
const CARGO = ['#F4F5F6', '#E8EAEC', '#F4F5F6', '#D9DEE3', '#F2EFE6']

export function paintFor(id: string, type: VType): string {
  const h = hash(id)
  if (type === 'bike') return JERSEYS[h % JERSEYS.length]
  if (type === 'truck' || type === 'van') return h % 3 === 0 ? PAINTS[(h >>> 3) % PAINTS.length][0] : '#F2F3F4'
  if (type === 'suspect') return '#2A2E35'
  let r = h % PAINT_TOTAL
  for (const [c, w] of PAINTS) {
    if ((r -= w) < 0) return c
  }
  return PAINTS[0][0]
}

export function cargoFor(id: string): string {
  return CARGO[hash(id + 'c') % CARGO.length]
}

/** Bus line (number + livery colour) from a SUMO vehicle id such as "bus501e3". */
export function busLineFor(id: string, lines: { id: string; color: string }[]): { id: string; color: string } {
  let best: { id: string; color: string } | null = null
  for (const l of lines) if (id.startsWith(`bus${l.id}`) && (!best || l.id.length > best.id.length)) best = l
  return best ?? { id: '', color: '#C8102E' }
}

function hexToRgb(h: string): [number, number, number] {
  const n = parseInt(h.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** Mix a colour toward black (t < 0) or white (t > 0). */
export function shade(hex: string, t: number): string {
  const [r, g, b] = hexToRgb(hex)
  const f = (c: number) => Math.round(t < 0 ? c * (1 + t) : c + (255 - c) * t)
  return `rgb(${f(r)},${f(g)},${f(b)})`
}

function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex)
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255
}

type Ctx = CanvasRenderingContext2D

function rrect(c: Ctx, x: number, y: number, w: number, h: number, r: number | [number, number, number, number]) {
  c.beginPath()
  c.roundRect(x, y, w, h, r)
}

/** Car body outline: rear at x=0, front at x=L, centred on y=0. */
function bodyPath(c: Ctx, L: number, W: number, rf: number, rr: number, taper = 0.06) {
  const hw = W / 2
  const t = W * taper
  c.beginPath()
  c.moveTo(rr, -hw)
  c.lineTo(L - rf * 1.4, -hw)
  c.bezierCurveTo(L - rf * 0.4, -hw, L, -hw + t, L, -hw + rf)
  c.lineTo(L, hw - rf)
  c.bezierCurveTo(L, hw - t, L - rf * 0.4, hw, L - rf * 1.4, hw)
  c.lineTo(rr, hw)
  c.quadraticCurveTo(0, hw, 0, hw - rr)
  c.lineTo(0, -hw + rr)
  c.quadraticCurveTo(0, -hw, rr, -hw)
  c.closePath()
}

/** Side-lit body gradient: darker flanks, bright crown. */
function bodyFill(c: Ctx, W: number, color: string) {
  const light = luminance(color) > 0.8
  const g = c.createLinearGradient(0, -W / 2, 0, W / 2)
  g.addColorStop(0, shade(color, light ? -0.22 : -0.32))
  g.addColorStop(0.16, shade(color, -0.03))
  g.addColorStop(0.45, shade(color, light ? 0.03 : 0.14))
  g.addColorStop(0.84, shade(color, -0.05))
  g.addColorStop(1, shade(color, light ? -0.25 : -0.36))
  return g
}

function glass(c: Ctx, x0: number, x1: number) {
  const g = c.createLinearGradient(x0, 0, x1, 0)
  g.addColorStop(0, '#16202C')
  g.addColorStop(0.55, '#2A3848')
  g.addColorStop(1, '#41546A')
  return g
}

/** Trapezoid window spanning x0..x1, half-widths w0 at x0 and w1 at x1. */
function windowShape(c: Ctx, x0: number, x1: number, w0: number, w1: number, r = 0.12) {
  c.beginPath()
  c.moveTo(x0, -w0 + r)
  c.quadraticCurveTo(x0, -w0, x0 + r, -w0)
  c.lineTo(x1 - r, -w1)
  c.quadraticCurveTo(x1, -w1, x1, -w1 + r)
  c.lineTo(x1, w1 - r)
  c.quadraticCurveTo(x1, w1, x1 - r, w1)
  c.lineTo(x0 + r, w0)
  c.quadraticCurveTo(x0, w0, x0, w0 - r)
  c.closePath()
}

/** Tyres peeking out under the body make silhouettes read as vehicles at a glance. */
function tyres(c: Ctx, W: number, axles: number[], len = 0.66, wid = 0.24) {
  c.fillStyle = '#15181D'
  for (const x of axles) for (const s of [-1, 1]) {
    rrect(c, x - len / 2, s > 0 ? W / 2 - wid + 0.05 : -W / 2 - 0.05, len, wid, 0.08)
    c.fill()
  }
}

function mirrors(c: Ctx, x: number, W: number, color: string) {
  c.fillStyle = shade(color, -0.25)
  for (const s of [-1, 1]) {
    c.beginPath()
    c.ellipse(x, s * (W / 2 + 0.08), 0.09, 0.14, s * 0.35, 0, Math.PI * 2)
    c.fill()
  }
}

function headlights(c: Ctx, L: number, W: number) {
  for (const s of [-1, 1]) {
    c.beginPath()
    c.moveTo(L - 0.05, s * (W / 2 - 0.2))
    c.lineTo(L - 0.3, s * (W / 2 - 0.1))
    c.lineTo(L - 0.34, s * (W / 2 - 0.5))
    c.lineTo(L - 0.06, s * (W / 2 - 0.52))
    c.closePath()
    c.fillStyle = '#EEF2F6'
    c.fill()
    c.strokeStyle = 'rgba(30,41,59,0.45)'
    c.lineWidth = 0.025
    c.stroke()
  }
}

function taillights(c: Ctx, W: number) {
  c.fillStyle = '#9F1D24'
  for (const s of [-1, 1]) {
    c.beginPath()
    c.moveTo(0.03, s * (W / 2 - 0.12))
    c.lineTo(0.22, s * (W / 2 - 0.06))
    c.lineTo(0.2, s * (W / 2 - 0.42))
    c.lineTo(0.03, s * (W / 2 - 0.46))
    c.closePath()
    c.fill()
  }
}

/** Glossy specular streak across a roof or bonnet. */
function sheen(c: Ctx, x0: number, x1: number, hw: number, strength = 0.22) {
  const g = c.createLinearGradient(0, -hw, 0, hw)
  g.addColorStop(0, 'rgba(255,255,255,0)')
  g.addColorStop(0.3, `rgba(255,255,255,${strength})`)
  g.addColorStop(0.42, 'rgba(255,255,255,0)')
  c.fillStyle = g
  c.fillRect(x0, -hw, x1 - x0, hw * 2)
}

interface CarShape {
  rear: [number, number] // rear window span (fraction of L)
  ws: [number, number] // windscreen span
  rf: number
  rr: number
  taper: number
  inset: number // roof inset from the body side
}

const SHAPES: Record<string, CarShape> = {
  sedan: { rear: [0.17, 0.31], ws: [0.58, 0.71], rf: 0.55, rr: 0.42, taper: 0.07, inset: 0.2 },
  hatch: { rear: [0.06, 0.2], ws: [0.6, 0.73], rf: 0.55, rr: 0.4, taper: 0.07, inset: 0.19 },
  suv: { rear: [0.03, 0.12], ws: [0.63, 0.74], rf: 0.4, rr: 0.28, taper: 0.03, inset: 0.13 },
  police: { rear: [0.16, 0.3], ws: [0.58, 0.71], rf: 0.5, rr: 0.4, taper: 0.06, inset: 0.19 },
}

function car(c: Ctx, type: VType, L: number, W: number, color: string) {
  const hw = W / 2
  const P = SHAPES[type] ?? SHAPES.sedan
  const boxy = type === 'suv'
  tyres(c, W, [L * 0.2, L * 0.79], boxy ? 0.74 : 0.66, boxy ? 0.27 : 0.24)
  bodyPath(c, L, W, P.rf, P.rr, P.taper)
  c.fillStyle = bodyFill(c, W, color)
  c.fill()
  c.strokeStyle = shade(color, -0.45)
  c.lineWidth = 0.035
  c.stroke()

  const roofW = hw - P.inset
  const rx0 = P.rear[1] * L
  const rx1 = P.ws[0] * L
  // greenhouse: one dark glass shape around the roof gives A/B/C pillars
  windowShape(c, P.rear[0] * L, P.ws[1] * L, roofW - 0.1, roofW - 0.16, 0.22)
  c.fillStyle = '#1E2733'
  c.fill()
  windowShape(c, P.ws[0] * L, P.ws[1] * L, roofW - 0.03, roofW - 0.17, 0.14)
  c.fillStyle = glass(c, P.ws[0] * L, P.ws[1] * L)
  c.fill()
  windowShape(c, P.rear[0] * L, P.rear[1] * L, roofW - 0.16, roofW - 0.05, 0.12)
  c.fillStyle = glass(c, P.rear[1] * L, P.rear[0] * L)
  c.fill()
  // roof panel
  rrect(c, rx0 + 0.02, -(roofW - 0.06), rx1 - rx0 - 0.04, (roofW - 0.06) * 2, boxy ? 0.14 : 0.24)
  const rg = c.createLinearGradient(0, -roofW, 0, roofW)
  rg.addColorStop(0, shade(color, -0.14))
  rg.addColorStop(0.5, shade(color, luminance(color) > 0.8 ? 0.05 : 0.18))
  rg.addColorStop(1, shade(color, -0.14))
  c.fillStyle = rg
  c.fill()
  // B-pillars
  c.fillStyle = shade(color, -0.1)
  const bx = rx0 + (rx1 - rx0) * 0.52
  for (const s of [-1, 1]) c.fillRect(bx - 0.06, s > 0 ? roofW - 0.08 : -roofW - 0.02, 0.12, 0.1)
  sheen(c, rx0 + 0.1, rx1 - 0.1, roofW - 0.08, 0.2)
  if (boxy) {
    // roof rails
    c.fillStyle = '#2B3038'
    for (const s of [-1, 1]) {
      rrect(c, rx0 + 0.05, s * (roofW - 0.2) - 0.04, rx1 - rx0 - 0.1, 0.08, 0.04)
      c.fill()
    }
  }
  // bonnet crease and boot line
  c.strokeStyle = shade(color, -0.2)
  c.lineWidth = 0.03
  c.beginPath()
  c.moveTo(P.ws[1] * L + 0.2, -hw * 0.42)
  c.quadraticCurveTo(L - 0.3, -hw * 0.3, L - 0.15, -hw * 0.3)
  c.moveTo(P.ws[1] * L + 0.2, hw * 0.42)
  c.quadraticCurveTo(L - 0.3, hw * 0.3, L - 0.15, hw * 0.3)
  c.stroke()
  sheen(c, P.ws[1] * L + 0.1, L - 0.2, hw - 0.15, 0.12)
  mirrors(c, P.ws[0] * L + 0.02, W, color)
  headlights(c, L, W)
  taillights(c, W)
}

function pickup(c: Ctx, L: number, W: number, color: string) {
  const hw = W / 2
  tyres(c, W, [L * 0.2, L * 0.8], 0.78, 0.28)
  bodyPath(c, L, W, 0.45, 0.16, 0.04)
  c.fillStyle = bodyFill(c, W, color)
  c.fill()
  c.strokeStyle = shade(color, -0.45)
  c.lineWidth = 0.035
  c.stroke()
  // open bed with ribbed floor
  const bed0 = 0.14
  const bed1 = L * 0.42
  rrect(c, bed0, -hw + 0.14, bed1 - bed0, W - 0.28, 0.06)
  c.fillStyle = '#2C3138'
  c.fill()
  c.strokeStyle = '#3D434B'
  c.lineWidth = 0.035
  for (let y = -hw + 0.32; y < hw - 0.2; y += 0.22) {
    c.beginPath()
    c.moveTo(bed0 + 0.08, y)
    c.lineTo(bed1 - 0.08, y)
    c.stroke()
  }
  // cab
  const roofW = hw - 0.15
  windowShape(c, L * 0.44, L * 0.76, roofW - 0.04, roofW - 0.14, 0.18)
  c.fillStyle = '#1E2733'
  c.fill()
  rrect(c, L * 0.475, -roofW + 0.08, L * 0.18, (roofW - 0.08) * 2, 0.16)
  c.fillStyle = shade(color, luminance(color) > 0.8 ? 0.04 : 0.14)
  c.fill()
  sheen(c, L * 0.48, L * 0.65, roofW - 0.1, 0.18)
  windowShape(c, L * 0.66, L * 0.76, roofW - 0.03, roofW - 0.16)
  c.fillStyle = glass(c, L * 0.66, L * 0.76)
  c.fill()
  mirrors(c, L * 0.67, W, color)
  headlights(c, L, W)
  taillights(c, W)
}

function van(c: Ctx, L: number, W: number, color: string) {
  const hw = W / 2
  tyres(c, W, [L * 0.17, L * 0.8], 0.72, 0.26)
  bodyPath(c, L, W, 0.5, 0.16, 0.05)
  c.fillStyle = bodyFill(c, W, color)
  c.fill()
  c.strokeStyle = shade(color, -0.4)
  c.lineWidth = 0.035
  c.stroke()
  rrect(c, 0.12, -hw + 0.12, L * 0.74, W - 0.24, 0.14)
  c.fillStyle = shade(color, luminance(color) > 0.8 ? 0.04 : 0.1)
  c.fill()
  c.strokeStyle = shade(color, -0.12)
  c.lineWidth = 0.03
  for (let x = 0.6; x < L * 0.74; x += 0.55) {
    c.beginPath()
    c.moveTo(x, -hw + 0.22)
    c.lineTo(x, hw - 0.22)
    c.stroke()
  }
  windowShape(c, L * 0.77, L * 0.87, hw - 0.16, hw - 0.3)
  c.fillStyle = glass(c, L * 0.77, L * 0.87)
  c.fill()
  mirrors(c, L * 0.8, W, color)
  headlights(c, L, W)
  taillights(c, W)
}

function truck(c: Ctx, L: number, W: number, color: string, cargo: string) {
  const hw = W / 2
  const cabL = 2.3
  tyres(c, W, [0.9, 2.15, L - 1.25], 0.95, 0.3)
  // cargo box
  rrect(c, 0, -hw, L - cabL - 0.25, W, 0.08)
  const g = c.createLinearGradient(0, -hw, 0, hw)
  g.addColorStop(0, shade(cargo, -0.16))
  g.addColorStop(0.5, shade(cargo, 0.04))
  g.addColorStop(1, shade(cargo, -0.16))
  c.fillStyle = g
  c.fill()
  c.strokeStyle = shade(cargo, -0.3)
  c.lineWidth = 0.04
  c.stroke()
  c.strokeStyle = shade(cargo, -0.1)
  c.lineWidth = 0.03
  for (let x = 0.5; x < L - cabL - 0.4; x += 0.6) {
    c.beginPath()
    c.moveTo(x, -hw + 0.08)
    c.lineTo(x, hw - 0.08)
    c.stroke()
  }
  // cab
  const x0 = L - cabL
  rrect(c, x0, -hw + 0.05, cabL, W - 0.1, [0.15, 0.4, 0.4, 0.15])
  c.fillStyle = bodyFill(c, W, color)
  c.fill()
  c.strokeStyle = shade(color, -0.4)
  c.stroke()
  rrect(c, x0 + 0.2, -hw + 0.25, cabL * 0.5, W - 0.5, 0.15)
  c.fillStyle = shade(color, luminance(color) > 0.8 ? 0.03 : 0.12)
  c.fill()
  windowShape(c, L - 0.62, L - 0.18, hw - 0.2, hw - 0.28, 0.06)
  c.fillStyle = glass(c, L - 0.62, L - 0.18)
  c.fill()
  mirrors(c, L - 0.55, W + 0.15, color)
  headlights(c, L, W)
  taillights(c, W)
}

function bus(c: Ctx, L: number, W: number, line: string, route: string) {
  const hw = W / 2
  tyres(c, W, [L * 0.2, L * 0.78], 1.0, 0.3)
  // body in line livery
  rrect(c, 0, -hw, L, W, [0.3, 0.55, 0.55, 0.3])
  c.fillStyle = line
  c.fill()
  // side window bands visible at the roof edge
  c.fillStyle = '#1C2530'
  for (const s of [-1, 1]) c.fillRect(1.3, s > 0 ? hw - 0.24 : -hw + 0.1, L - 2.9, 0.14)
  // light roof panel
  rrect(c, 0.3, -hw + 0.3, L - 0.85, W - 0.6, 0.24)
  const g = c.createLinearGradient(0, -hw, 0, hw)
  g.addColorStop(0, '#D7DBE0')
  g.addColorStop(0.5, '#F5F6F7')
  g.addColorStop(1, '#D7DBE0')
  c.fillStyle = g
  c.fill()
  // front cap in livery colour, wrap-around windscreen
  rrect(c, L - 1.1, -hw + 0.3, 0.55, W - 0.6, 0.1)
  c.fillStyle = shade(line, -0.05)
  c.fill()
  windowShape(c, L - 0.5, L - 0.05, hw - 0.08, hw - 0.18, 0.12)
  c.fillStyle = glass(c, L - 0.5, L - 0.05)
  c.fill()
  c.fillStyle = '#1C2530'
  c.fillRect(0.06, -hw + 0.45, 0.14, W - 0.9)
  // roof equipment: AC unit with fans, hatches, rear engine housing
  rrect(c, L * 0.56, -0.66, 2.3, 1.32, 0.14)
  c.fillStyle = '#B7BEC6'
  c.fill()
  c.fillStyle = '#8D969F'
  for (let i = 0; i < 3; i++) {
    c.beginPath()
    c.arc(L * 0.56 + 0.42 + i * 0.73, 0, 0.24, 0, Math.PI * 2)
    c.fill()
  }
  for (const x of [L * 0.17, L * 0.82]) {
    rrect(c, x, -0.36, 0.72, 0.72, 0.06)
    c.fillStyle = '#C7CDD3'
    c.fill()
  }
  rrect(c, 0.32, -0.82, 1.15, 1.64, 0.12)
  c.fillStyle = '#C1C7CD'
  c.fill()
  // route number painted on the roof, reading toward the front
  if (route) {
    c.save()
    c.translate(L * 0.36, 0)
    c.rotate(Math.PI / 2)
    c.fillStyle = line
    c.font = `800 ${route.length > 2 ? 0.95 : 1.15}px Inter, system-ui, sans-serif`
    c.textAlign = 'center'
    c.textBaseline = 'middle'
    c.fillText(route, 0, 0.04)
    c.restore()
  }
  c.strokeStyle = shade(line, -0.4)
  c.lineWidth = 0.05
  rrect(c, 0, -hw, L, W, [0.3, 0.55, 0.55, 0.3])
  c.stroke()
  headlights(c, L, W)
  taillights(c, W)
}

function ambulance(c: Ctx, L: number, W: number) {
  const hw = W / 2
  const cabL = 1.9
  tyres(c, W, [1.15, L - 1.05], 0.82, 0.28)
  // patient module
  rrect(c, 0, -hw, L - cabL + 0.1, W, 0.12)
  const g = c.createLinearGradient(0, -hw, 0, hw)
  g.addColorStop(0, '#D1D5DA')
  g.addColorStop(0.14, '#FAFAFA')
  g.addColorStop(0.86, '#FAFAFA')
  g.addColorStop(1, '#D1D5DA')
  c.fillStyle = g
  c.fill()
  c.strokeStyle = '#A0A7AF'
  c.lineWidth = 0.04
  c.stroke()
  // red side bands and rear chevrons
  c.fillStyle = '#D02A2A'
  c.fillRect(0.05, -hw + 0.03, L - cabL, 0.2)
  c.fillRect(0.05, hw - 0.23, L - cabL, 0.2)
  c.save()
  rrect(c, 0.02, -hw + 0.25, 0.32, W - 0.5, 0.04)
  c.clip()
  for (let i = -2; i < 8; i++) {
    c.fillStyle = i % 2 ? '#D02A2A' : '#FACC15'
    c.beginPath()
    const y = -hw + i * 0.36
    c.moveTo(0, y)
    c.lineTo(0.34, y + 0.34)
    c.lineTo(0.34, y + 0.52)
    c.lineTo(0, y + 0.18)
    c.closePath()
    c.fill()
  }
  c.restore()
  // star of life on the roof
  const cx = (L - cabL) * 0.48
  c.fillStyle = '#FFFFFF'
  c.beginPath()
  c.arc(cx, 0, 0.78, 0, Math.PI * 2)
  c.fill()
  c.fillStyle = '#1D5BD8'
  for (let k = 0; k < 3; k++) {
    c.save()
    c.translate(cx, 0)
    c.rotate((k * Math.PI) / 3)
    rrect(c, -0.62, -0.17, 1.24, 0.34, 0.04)
    c.fill()
    c.restore()
  }
  c.strokeStyle = '#FFFFFF'
  c.lineWidth = 0.07
  c.beginPath()
  c.moveTo(cx - 0.32, 0)
  c.lineTo(cx + 0.32, 0)
  c.stroke()
  // corner beacons on the module (static housings; lit live)
  c.fillStyle = '#7F1D1D'
  for (const s of [-1, 1]) {
    rrect(c, 0.12, s > 0 ? hw - 0.36 : -hw + 0.12, 0.24, 0.24, 0.05)
    c.fill()
    rrect(c, L - cabL - 0.28, s > 0 ? hw - 0.36 : -hw + 0.12, 0.24, 0.24, 0.05)
    c.fill()
  }
  // cab
  const x0 = L - cabL
  c.save()
  c.translate(x0, 0)
  bodyPath(c, cabL, W - 0.1, 0.45, 0.05, 0.04)
  c.fillStyle = bodyFill(c, W, '#F5F5F5')
  c.fill()
  c.strokeStyle = '#A0A7AF'
  c.stroke()
  windowShape(c, cabL * 0.48, cabL * 0.74, hw - 0.17, hw - 0.3)
  c.fillStyle = glass(c, cabL * 0.48, cabL * 0.74)
  c.fill()
  c.restore()
  lightbarHousing(c, LIGHTBAR.ambulance!)
  mirrors(c, x0 + cabL * 0.55, W, '#E5E7EB')
  headlights(c, L, W)
  taillights(c, W)
}

function lightbarHousing(c: Ctx, lb: { x: number; len: number; span: number }) {
  rrect(c, lb.x - lb.len / 2 - 0.03, -lb.span / 2 - 0.03, lb.len + 0.06, lb.span + 0.06, 0.1)
  c.fillStyle = '#20252C'
  c.fill()
  // dim lenses, lit live by the renderer
  c.fillStyle = '#7F1D1D'
  rrect(c, lb.x - lb.len / 2, -lb.span / 2, lb.len, lb.span / 2 - 0.04, 0.07)
  c.fill()
  c.fillStyle = lb === LIGHTBAR.police ? '#1E3A8A' : '#9CA3AF'
  rrect(c, lb.x - lb.len / 2, 0.04, lb.len, lb.span / 2 - 0.04, 0.07)
  c.fill()
}

function police(c: Ctx, L: number, W: number) {
  car(c, 'police', L, W, '#F7F8F9')
  const hw = W / 2
  // black bonnet, boot and door-bottom sweep ("black and white")
  c.fillStyle = '#12161C'
  c.beginPath()
  c.moveTo(L * 0.73, -hw + 0.04)
  c.lineTo(L - 0.3, -hw + 0.04)
  c.quadraticCurveTo(L - 0.02, -hw + 0.1, L - 0.02, -hw + 0.5)
  c.lineTo(L - 0.02, hw - 0.5)
  c.quadraticCurveTo(L - 0.02, hw - 0.1, L - 0.3, hw - 0.04)
  c.lineTo(L * 0.73, hw - 0.04)
  c.closePath()
  c.fill()
  rrect(c, 0.04, -hw + 0.06, L * 0.15, W - 0.12, [0.35, 0.05, 0.05, 0.35])
  c.fill()
  // push bar
  rrect(c, L - 0.02, -0.55, 0.14, 1.1, 0.05)
  c.fill()
  headlights(c, L, W)
  // unit number on the roof
  c.save()
  c.translate(L * 0.44, 0)
  c.rotate(Math.PI / 2)
  c.fillStyle = '#12161C'
  c.font = '800 0.5px Inter, system-ui, sans-serif'
  c.textAlign = 'center'
  c.textBaseline = 'middle'
  c.fillText('52', 0, 0.02)
  c.restore()
  lightbarHousing(c, LIGHTBAR.police!)
}

function bike(c: Ctx, L: number, jersey: string) {
  // light halo so cyclists read against dark asphalt
  c.fillStyle = 'rgba(255,255,255,0.88)'
  c.beginPath()
  c.ellipse(L / 2, 0, L / 2 + 0.14, 0.44, 0, 0, Math.PI * 2)
  c.fill()
  // tyres seen from above: thin dark slivers fore and aft
  c.fillStyle = '#111827'
  rrect(c, 0.02, -0.045, 0.66, 0.09, 0.045)
  c.fill()
  rrect(c, L - 0.68, -0.045, 0.66, 0.09, 0.045)
  c.fill()
  // frame
  c.strokeStyle = '#475569'
  c.lineCap = 'round'
  c.lineWidth = 0.05
  c.beginPath()
  c.moveTo(0.4, 0)
  c.lineTo(L - 0.4, 0)
  c.stroke()
  // handlebar
  c.strokeStyle = '#0F172A'
  c.lineWidth = 0.06
  c.beginPath()
  c.moveTo(L - 0.44, -0.32)
  c.lineTo(L - 0.44, 0.32)
  c.stroke()
  // rider: arms, shoulders, helmet with vents
  c.strokeStyle = shade(jersey, -0.2)
  c.lineWidth = 0.1
  c.beginPath()
  c.moveTo(L * 0.5, -0.2)
  c.lineTo(L - 0.46, -0.29)
  c.moveTo(L * 0.5, 0.2)
  c.lineTo(L - 0.46, 0.29)
  c.stroke()
  c.beginPath()
  c.ellipse(L * 0.44, 0, 0.21, 0.26, 0, 0, Math.PI * 2)
  c.fillStyle = jersey
  c.fill()
  c.strokeStyle = shade(jersey, -0.35)
  c.lineWidth = 0.025
  c.stroke()
  c.beginPath()
  c.ellipse(L * 0.57, 0, 0.15, 0.12, 0, 0, Math.PI * 2)
  c.fillStyle = '#F8FAFC'
  c.fill()
  c.strokeStyle = '#64748B'
  c.lineWidth = 0.025
  c.stroke()
  c.strokeStyle = '#94A3B8'
  c.lineWidth = 0.02
  c.beginPath()
  c.moveTo(L * 0.57 - 0.1, -0.04)
  c.lineTo(L * 0.57 + 0.1, -0.04)
  c.moveTo(L * 0.57 - 0.1, 0.04)
  c.lineTo(L * 0.57 + 0.1, 0.04)
  c.stroke()
}

function damage(c: Ctx, L: number, W: number) {
  // crumpled front, debris and fluid
  c.fillStyle = 'rgba(20,20,20,0.55)'
  c.beginPath()
  c.moveTo(L, -W / 2 + 0.1)
  c.lineTo(L - 0.9, -W / 2 + 0.3)
  c.lineTo(L - 0.6, 0.1)
  c.lineTo(L - 1.0, W / 2 - 0.2)
  c.lineTo(L, W / 2 - 0.3)
  c.closePath()
  c.fill()
  c.fillStyle = 'rgba(40,40,40,0.25)'
  c.beginPath()
  c.ellipse(L + 0.6, 0.2, 0.8, 0.5, 0.3, 0, Math.PI * 2)
  c.fill()
  c.fillStyle = '#9CA3AF'
  for (const [x, y] of [[L + 0.4, -0.6], [L + 0.9, 0.5], [L + 0.2, 0.9], [L + 1.1, -0.2]]) c.fillRect(x, y, 0.12, 0.08)
}

export interface Sprite extends AtlasRect {
  ox: number // sprite px from left edge to the front bumper
  oy: number // sprite px from top edge to the centre line
  ppm: number
}

const cache = new Map<string, Sprite>()
let cacheGen = -1
const PAD = 0.7

/** Place a freshly painted canvas into the shared atlas (falls back to the canvas itself if oversized). */
export function toAtlas(canvas: HTMLCanvasElement): AtlasRect {
  return atlas.add(canvas) ?? { canvas, sx: 0, sy: 0, sw: canvas.width, sh: canvas.height }
}

/** Cached atlas sprite; the cache empties whenever the atlas is reset. */
export function cached<T>(key: string, make: () => T, store: Map<string, T>): T {
  if (cacheGen !== atlas.generation) {
    cache.clear()
    for (const m of extraCaches) m.clear()
    cacheGen = atlas.generation
  }
  let hit = store.get(key)
  if (!hit) store.set(key, (hit = make()))
  return hit
}
const extraCaches: Map<string, unknown>[] = []
export function spriteCache<T>(): Map<string, T> {
  const m = new Map<string, T>()
  extraCaches.push(m as Map<string, unknown>)
  return m
}

/**
 * `extra` is the cargo colour for trucks or "colour|route" for buses.
 * `brake` bakes lit brake lamps into the sprite, so braking traffic needs no extra draw calls.
 */
export function spriteFor(type: VType, color: string, ppm: number, extra = '', brake = false): Sprite {
  return cached(`${type}|${color}|${ppm}|${extra}|${+brake}`, () => paintSprite(type, color, ppm, extra, brake), cache)
}

function paintSprite(type: VType, color: string, ppm: number, extra: string, brake: boolean): Sprite {
  const [L, W] = DIMS[type]
  const padF = type === 'wreck' ? 2.2 : PAD
  const cw = Math.ceil((L + PAD + padF) * ppm)
  const ch = Math.ceil((W + PAD * 2) * ppm)
  const canvas = document.createElement('canvas')
  canvas.width = cw
  canvas.height = ch
  const c = canvas.getContext('2d')!
  c.scale(ppm, ppm)
  c.translate(PAD, PAD + W / 2)
  // soft contact shadow, offset away from the sun (north-west)
  if (type !== 'bike') {
    c.save()
    c.shadowColor = 'rgba(15,23,42,0.38)'
    c.shadowBlur = 0.4 * ppm
    c.shadowOffsetX = 0.14 * ppm
    c.shadowOffsetY = 0.18 * ppm
    rrect(c, 0.05, -W / 2 + 0.05, L - 0.1, W - 0.1, 0.45)
    c.fillStyle = 'rgba(15,23,42,0.28)'
    c.fill()
    c.restore()
  } else {
    c.fillStyle = 'rgba(15,23,42,0.18)'
    c.beginPath()
    c.ellipse(L / 2 + 0.08, 0.1, L / 2, 0.26, 0, 0, Math.PI * 2)
    c.fill()
  }
  switch (type) {
    case 'pickup':
      pickup(c, L, W, color)
      break
    case 'van':
      van(c, L, W, color)
      break
    case 'truck':
      truck(c, L, W, color, extra || '#F2F3F4')
      break
    case 'bus': {
      const [line, route] = (extra || '#C8102E|').split('|')
      bus(c, L, W, line, route ?? '')
      break
    }
    case 'ambulance':
      ambulance(c, L, W)
      break
    case 'bike':
      bike(c, L, color)
      break
    case 'police':
      police(c, L, W)
      break
    case 'wreck':
      car(c, 'sedan', L, W, color)
      damage(c, L, W)
      break
    case 'suspect':
      car(c, 'sedan', L, W, color)
      break
    default:
      car(c, type, L, W, color)
  }
  if (brake) {
    // same geometry the renderer used to draw live: a soft halo behind each lamp, then the lit lamp
    c.fillStyle = 'rgba(255,45,45,0.32)'
    for (const side of [-1, 1]) c.fillRect(-0.22, side * (W / 2 - 0.2) - 0.3, 0.42, 0.6)
    c.fillStyle = '#FF2D2D'
    for (const side of [-1, 1]) c.fillRect(0.01, side * (W / 2 - 0.2) - 0.17, 0.12, 0.34)
  }
  return { ...toAtlas(canvas), ox: (PAD + L) * ppm, oy: (PAD + W / 2) * ppm, ppm }
}

// ---- small atlas sprites for markers, lamps and pedestrians -------------------

const markers = spriteCache<AtlasRect>()
/** Network-view marker: rounded body-colour bar with a rim, drawn scaled to the vehicle. */
export function markerSprite(fill: string, rim: string): AtlasRect {
  return cached(`${fill}|${rim}`, () => {
    const c = document.createElement('canvas')
    c.width = 40
    c.height = 20
    const g = c.getContext('2d')!
    g.fillStyle = fill
    g.strokeStyle = rim
    g.lineWidth = 2.4
    g.beginPath()
    g.roundRect(1.5, 1.5, 37, 17, 5)
    g.fill()
    g.stroke()
    return toAtlas(c)
  }, markers)
}

const dots = spriteCache<AtlasRect>()
/** Filled disc (signal lamp or its glow); drawn at any radius. */
export function dotSprite(color: string, alpha = 1): AtlasRect {
  return cached(`${color}|${alpha}`, () => {
    const c = document.createElement('canvas')
    c.width = c.height = 32
    const g = c.getContext('2d')!
    g.globalAlpha = alpha
    g.fillStyle = color
    g.beginPath()
    g.arc(16, 16, 15, 0, Math.PI * 2)
    g.fill()
    return toAtlas(c)
  }, dots)
}

const people = spriteCache<AtlasRect>()
/** Pedestrian seen from above, heading +x: light halo, shoulders across the heading, head slightly forward. */
export function personSprite(shirt: string, hair: string): AtlasRect {
  return cached(`${shirt}|${hair}`, () => {
    const c = document.createElement('canvas')
    c.width = c.height = 32
    const g = c.getContext('2d')!
    g.fillStyle = 'rgba(255,255,255,0.9)'
    g.beginPath()
    g.arc(16, 16, 16, 0, Math.PI * 2)
    g.fill()
    g.fillStyle = shirt
    g.beginPath()
    g.ellipse(16, 16, 0.44 * 16, 0.75 * 16, 0, 0, Math.PI * 2)
    g.fill()
    g.fillStyle = hair
    g.beginPath()
    g.arc(16 + 0.09 * 16, 16, 0.33 * 16, 0, Math.PI * 2)
    g.fill()
    return toAtlas(c)
  }, people)
}

export function spriteBucket(pxPerM: number): number {
  const b = [6, 12, 24, 40]
  for (const v of b) if (pxPerM <= v) return v
  return 56
}
