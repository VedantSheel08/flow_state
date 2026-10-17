// Procedural top-down vehicle artwork.
//
// Each vehicle class is painted in vector form at real-world dimensions (the
// same lengths/widths SUMO simulates) into an off-screen sprite, cached per
// (type, colour, resolution bucket). Lights (brake, indicators, beacons) are
// drawn live on top from SUMO's signal bitmask.

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

/** Car body outline: rear at x=0, front at x=L, centred on y=0 (y down = vehicle right). */
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

function bodyFill(c: Ctx, W: number, color: string) {
  const g = c.createLinearGradient(0, -W / 2, 0, W / 2)
  g.addColorStop(0, shade(color, -0.28))
  g.addColorStop(0.18, shade(color, -0.04))
  g.addColorStop(0.5, shade(color, luminance(color) > 0.8 ? 0.02 : 0.1))
  g.addColorStop(0.82, shade(color, -0.04))
  g.addColorStop(1, shade(color, -0.28))
  return g
}

function glass(c: Ctx, x0: number, x1: number) {
  const g = c.createLinearGradient(x0, 0, x1, 0)
  g.addColorStop(0, '#1A222D')
  g.addColorStop(0.6, '#2A3747')
  g.addColorStop(1, '#3B4B5E')
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

function mirrors(c: Ctx, x: number, W: number, color: string) {
  c.fillStyle = shade(color, -0.2)
  for (const s of [-1, 1]) {
    c.beginPath()
    c.ellipse(x, s * (W / 2 + 0.09), 0.1, 0.13, 0, 0, Math.PI * 2)
    c.fill()
  }
}

function lamps(c: Ctx, L: number, W: number) {
  c.fillStyle = '#F7F3DF'
  for (const s of [-1, 1]) {
    rrect(c, L - 0.12, s * (W / 2 - 0.22) - 0.17, 0.1, 0.34, 0.04)
    c.fill()
  }
  c.fillStyle = '#7A1C1C'
  for (const s of [-1, 1]) {
    rrect(c, 0.02, s * (W / 2 - 0.2) - 0.16, 0.08, 0.32, 0.03)
    c.fill()
  }
}

function car(c: Ctx, type: VType, L: number, W: number, color: string) {
  const hw = W / 2
  const boxy = type === 'suv'
  bodyPath(c, L, W, boxy ? 0.42 : 0.55, boxy ? 0.32 : 0.42, boxy ? 0.03 : 0.07)
  c.fillStyle = bodyFill(c, W, color)
  c.fill()
  c.strokeStyle = shade(color, -0.4)
  c.lineWidth = 0.035
  c.stroke()
  // proportions (fractions of length from the rear)
  const P = {
    sedan: { rear: [0.15, 0.3], roof: [0.3, 0.6], ws: [0.6, 0.72] },
    hatch: { rear: [0.05, 0.2], roof: [0.2, 0.6], ws: [0.6, 0.72] },
    suv: { rear: [0.04, 0.13], roof: [0.13, 0.64], ws: [0.64, 0.74] },
    police: { rear: [0.15, 0.3], roof: [0.3, 0.6], ws: [0.6, 0.72] },
    suspect: { rear: [0.15, 0.3], roof: [0.3, 0.6], ws: [0.6, 0.72] },
    wreck: { rear: [0.15, 0.3], roof: [0.3, 0.6], ws: [0.6, 0.72] },
  }[type as 'sedan'] ?? { rear: [0.15, 0.3], roof: [0.3, 0.6], ws: [0.6, 0.72] }
  const roofW = hw - (boxy ? 0.12 : 0.2)
  // windscreen & rear window
  windowShape(c, P.ws[0] * L, P.ws[1] * L, roofW - 0.02, roofW - 0.16)
  c.fillStyle = glass(c, P.ws[0] * L, P.ws[1] * L)
  c.fill()
  windowShape(c, P.rear[0] * L, P.rear[1] * L, roofW - 0.14, roofW - 0.04)
  c.fillStyle = glass(c, P.rear[1] * L, P.rear[0] * L)
  c.fill()
  // side glass strips
  c.fillStyle = '#26303C'
  for (const s of [-1, 1]) {
    c.beginPath()
    c.moveTo(P.rear[1] * L, s * roofW)
    c.lineTo(P.ws[0] * L, s * roofW)
    c.lineTo(P.ws[0] * L - 0.05, s * (roofW - 0.09))
    c.lineTo(P.rear[1] * L + 0.05, s * (roofW - 0.09))
    c.closePath()
    c.fill()
  }
  // roof panel
  rrect(c, P.rear[1] * L, -(roofW - 0.09), (P.ws[0] - P.rear[1]) * L, (roofW - 0.09) * 2, 0.18)
  const rg = c.createLinearGradient(0, -roofW, 0, roofW)
  rg.addColorStop(0, shade(color, -0.12))
  rg.addColorStop(0.5, shade(color, luminance(color) > 0.8 ? 0.04 : 0.16))
  rg.addColorStop(1, shade(color, -0.12))
  c.fillStyle = rg
  c.fill()
  if (boxy) {
    c.strokeStyle = shade(color, -0.35)
    c.lineWidth = 0.05
    for (const s of [-1, 1]) {
      c.beginPath()
      c.moveTo(P.roof[0] * L + 0.25, s * (roofW - 0.2))
      c.lineTo(P.ws[0] * L - 0.2, s * (roofW - 0.2))
      c.stroke()
    }
  }
  // hood crease
  c.strokeStyle = shade(color, -0.18)
  c.lineWidth = 0.03
  c.beginPath()
  c.moveTo(P.ws[1] * L + 0.25, -hw * 0.45)
  c.quadraticCurveTo(L - 0.25, 0, P.ws[1] * L + 0.25, hw * 0.45)
  c.stroke()
  mirrors(c, P.ws[0] * L + 0.05, W, color)
  lamps(c, L, W)
}

function pickup(c: Ctx, L: number, W: number, color: string) {
  const hw = W / 2
  bodyPath(c, L, W, 0.45, 0.2, 0.04)
  c.fillStyle = bodyFill(c, W, color)
  c.fill()
  c.strokeStyle = shade(color, -0.4)
  c.lineWidth = 0.035
  c.stroke()
  // open bed
  const bed0 = 0.12
  const bed1 = L * 0.42
  rrect(c, bed0, -hw + 0.13, bed1 - bed0, W - 0.26, 0.06)
  c.fillStyle = '#2F343B'
  c.fill()
  c.strokeStyle = '#3E444C'
  c.lineWidth = 0.03
  for (let x = bed0 + 0.35; x < bed1 - 0.1; x += 0.35) {
    c.beginPath()
    c.moveTo(x, -hw + 0.2)
    c.lineTo(x, hw - 0.2)
    c.stroke()
  }
  // cab
  const roofW = hw - 0.16
  windowShape(c, L * 0.45, L * 0.48, roofW - 0.05, roofW - 0.05, 0.05)
  c.fillStyle = '#26303C'
  c.fill()
  rrect(c, L * 0.48, -roofW + 0.06, L * 0.18, (roofW - 0.06) * 2, 0.15)
  c.fillStyle = shade(color, luminance(color) > 0.8 ? 0.03 : 0.12)
  c.fill()
  windowShape(c, L * 0.66, L * 0.76, roofW - 0.03, roofW - 0.16)
  c.fillStyle = glass(c, L * 0.66, L * 0.76)
  c.fill()
  mirrors(c, L * 0.67, W, color)
  lamps(c, L, W)
}

function van(c: Ctx, L: number, W: number, color: string) {
  const hw = W / 2
  bodyPath(c, L, W, 0.5, 0.18, 0.05)
  c.fillStyle = bodyFill(c, W, color)
  c.fill()
  c.strokeStyle = shade(color, -0.4)
  c.lineWidth = 0.035
  c.stroke()
  rrect(c, 0.12, -hw + 0.12, L * 0.74, W - 0.24, 0.12)
  c.fillStyle = shade(color, luminance(color) > 0.8 ? 0.03 : 0.08)
  c.fill()
  c.strokeStyle = shade(color, -0.12)
  c.lineWidth = 0.03
  for (let x = 0.6; x < L * 0.74; x += 0.55) {
    c.beginPath()
    c.moveTo(x, -hw + 0.2)
    c.lineTo(x, hw - 0.2)
    c.stroke()
  }
  windowShape(c, L * 0.77, L * 0.86, hw - 0.18, hw - 0.3)
  c.fillStyle = glass(c, L * 0.77, L * 0.86)
  c.fill()
  mirrors(c, L * 0.8, W, color)
  lamps(c, L, W)
}

function truck(c: Ctx, L: number, W: number, color: string, cargo: string) {
  const hw = W / 2
  const cabL = 2.3
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
  lamps(c, L, W)
}

function bus(c: Ctx, L: number, W: number, line: string) {
  const hw = W / 2
  rrect(c, 0, -hw, L, W, [0.3, 0.5, 0.5, 0.3])
  c.fillStyle = line
  c.fill()
  // light roof panel inset from the livery sides
  rrect(c, 0.25, -hw + 0.32, L - 0.75, W - 0.64, 0.25)
  const g = c.createLinearGradient(0, -hw, 0, hw)
  g.addColorStop(0, '#DADDE1')
  g.addColorStop(0.5, '#F4F5F6')
  g.addColorStop(1, '#DADDE1')
  c.fillStyle = g
  c.fill()
  // side windows strip seen at the roof edge
  c.fillStyle = '#2A3542'
  for (const s of [-1, 1]) c.fillRect(1.2, s > 0 ? hw - 0.32 : -hw + 0.22, L - 2.6, 0.1)
  // front windscreen wrap and rear window
  windowShape(c, L - 0.55, L - 0.05, hw - 0.1, hw - 0.18, 0.1)
  c.fillStyle = glass(c, L - 0.55, L - 0.05)
  c.fill()
  c.fillStyle = '#2A3542'
  c.fillRect(0.06, -hw + 0.45, 0.14, W - 0.9)
  // roof equipment: AC unit with fans, two hatches, rear engine housing
  rrect(c, L * 0.5, -0.72, 2.6, 1.44, 0.14)
  c.fillStyle = '#B9C0C8'
  c.fill()
  c.fillStyle = '#8E979F'
  for (let i = 0; i < 3; i++) {
    c.beginPath()
    c.arc(L * 0.5 + 0.48 + i * 0.82, 0, 0.27, 0, Math.PI * 2)
    c.fill()
  }
  for (const x of [L * 0.22, L * 0.78]) {
    rrect(c, x, -0.4, 0.8, 0.8, 0.06)
    c.fillStyle = '#C9CED4'
    c.fill()
  }
  rrect(c, 0.3, -0.85, 1.3, 1.7, 0.12)
  c.fillStyle = '#C3C9CF'
  c.fill()
  c.strokeStyle = shade(line, -0.35)
  c.lineWidth = 0.05
  rrect(c, 0, -hw, L, W, [0.3, 0.5, 0.5, 0.3])
  c.stroke()
  lamps(c, L, W)
}

function ambulance(c: Ctx, L: number, W: number) {
  const hw = W / 2
  const cabL = 1.9
  rrect(c, 0, -hw, L - cabL + 0.1, W, 0.12)
  const g = c.createLinearGradient(0, -hw, 0, hw)
  g.addColorStop(0, '#D4D7DB')
  g.addColorStop(0.15, '#F8F8F8')
  g.addColorStop(0.85, '#F8F8F8')
  g.addColorStop(1, '#D4D7DB')
  c.fillStyle = g
  c.fill()
  c.strokeStyle = '#A5ABB2'
  c.lineWidth = 0.04
  c.stroke()
  // red side stripes + chevrons at the rear
  c.fillStyle = '#C62828'
  c.fillRect(0.05, -hw + 0.02, L - cabL, 0.18)
  c.fillRect(0.05, hw - 0.2, L - cabL, 0.18)
  for (let i = 0; i < 4; i++) {
    c.beginPath()
    const y = -hw + 0.3 + i * ((W - 0.6) / 4)
    c.moveTo(0.02, y)
    c.lineTo(0.3, y + 0.2)
    c.lineTo(0.02, y + 0.4)
    c.lineTo(0.02, y)
    c.fillStyle = i % 2 ? '#C62828' : '#FACC15'
    c.fill()
  }
  // star of life on the roof
  const cx = (L - cabL) * 0.5
  c.fillStyle = '#1D4ED8'
  for (let k = 0; k < 3; k++) {
    c.save()
    c.translate(cx, 0)
    c.rotate((k * Math.PI) / 3)
    c.fillRect(-0.55, -0.16, 1.1, 0.32)
    c.restore()
  }
  c.fillStyle = '#FFFFFF'
  c.fillRect(cx - 0.03, -0.35, 0.06, 0.7)
  // cab
  const x0 = L - cabL
  c.save()
  c.translate(x0, 0)
  bodyPath(c, cabL, W - 0.1, 0.45, 0.05, 0.04)
  c.fillStyle = bodyFill(c, W, '#F5F5F5')
  c.fill()
  c.strokeStyle = '#A5ABB2'
  c.stroke()
  windowShape(c, cabL * 0.45, cabL * 0.72, hw - 0.18, hw - 0.3)
  c.fillStyle = glass(c, cabL * 0.45, cabL * 0.72)
  c.fill()
  c.restore()
  mirrors(c, x0 + cabL * 0.5, W, '#E5E7EB')
  lamps(c, L, W)
}

function bike(c: Ctx, L: number, jersey: string) {
  // light outline so cyclists read against dark asphalt
  c.fillStyle = 'rgba(255,255,255,0.85)'
  c.beginPath()
  c.ellipse(L / 2, 0, L / 2 + 0.12, 0.42, 0, 0, Math.PI * 2)
  c.fill()
  // wheels and frame
  c.strokeStyle = '#1F2937'
  c.lineCap = 'round'
  c.lineWidth = 0.07
  c.beginPath()
  c.moveTo(0.02, 0)
  c.lineTo(0.62, 0)
  c.moveTo(L - 0.62, 0)
  c.lineTo(L - 0.02, 0)
  c.stroke()
  c.lineWidth = 0.045
  c.strokeStyle = '#4B5563'
  c.beginPath()
  c.moveTo(0.35, 0)
  c.lineTo(L - 0.35, 0)
  c.stroke()
  // handlebar
  c.strokeStyle = '#111827'
  c.lineWidth = 0.05
  c.beginPath()
  c.moveTo(L - 0.42, -0.3)
  c.lineTo(L - 0.42, 0.3)
  c.stroke()
  // rider: arms, torso (shoulders), helmet
  c.strokeStyle = shade(jersey, -0.15)
  c.lineWidth = 0.09
  c.beginPath()
  c.moveTo(L * 0.5, -0.19)
  c.lineTo(L - 0.44, -0.27)
  c.moveTo(L * 0.5, 0.19)
  c.lineTo(L - 0.44, 0.27)
  c.stroke()
  c.beginPath()
  c.ellipse(L * 0.45, 0, 0.2, 0.24, 0, 0, Math.PI * 2)
  c.fillStyle = jersey
  c.fill()
  c.beginPath()
  c.ellipse(L * 0.56, 0, 0.13, 0.11, 0, 0, Math.PI * 2)
  c.fillStyle = '#F9FAFB'
  c.fill()
  c.strokeStyle = '#6B7280'
  c.lineWidth = 0.025
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

export interface Sprite {
  canvas: HTMLCanvasElement
  ox: number // sprite px from left edge to the front bumper
  oy: number // sprite px from top edge to the centre line
  ppm: number
}

const cache = new Map<string, Sprite>()
const PAD = 0.7

export function spriteFor(type: VType, color: string, ppm: number, extra = ''): Sprite {
  const key = `${type}|${color}|${ppm}|${extra}`
  const hit = cache.get(key)
  if (hit) return hit
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
  // contact shadow
  if (type !== 'bike') {
    c.save()
    c.shadowColor = 'rgba(15,23,42,0.35)'
    c.shadowBlur = 0.35 * ppm
    c.shadowOffsetX = 0.12 * ppm
    c.shadowOffsetY = 0.16 * ppm
    rrect(c, 0.05, -W / 2 + 0.05, L - 0.1, W - 0.1, 0.4)
    c.fillStyle = 'rgba(15,23,42,0.25)'
    c.fill()
    c.restore()
  } else {
    c.fillStyle = 'rgba(15,23,42,0.18)'
    c.beginPath()
    c.ellipse(L / 2 + 0.08, 0.1, L / 2, 0.24, 0, 0, Math.PI * 2)
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
    case 'bus':
      bus(c, L, W, extra || '#C8102E')
      break
    case 'ambulance':
      ambulance(c, L, W)
      break
    case 'bike':
      bike(c, L, color)
      break
    case 'police': {
      car(c, 'police', L, W, '#F5F6F7')
      // black hood and trunk ("black and white"), roof number
      c.fillStyle = 'rgba(17,24,39,0.92)'
      c.beginPath()
      c.moveTo(L * 0.73, -W / 2 + 0.06)
      c.lineTo(L - 0.25, -W / 2 + 0.06)
      c.quadraticCurveTo(L - 0.02, -W / 2 + 0.1, L - 0.02, -W / 2 + 0.5)
      c.lineTo(L - 0.02, W / 2 - 0.5)
      c.quadraticCurveTo(L - 0.02, W / 2 - 0.1, L - 0.25, W / 2 - 0.06)
      c.lineTo(L * 0.73, W / 2 - 0.06)
      c.closePath()
      c.fill()
      rrect(c, 0.05, -W / 2 + 0.08, L * 0.14, W - 0.16, 0.25)
      c.fill()
      c.fillStyle = '#111827'
      c.font = `700 0.42px Inter, sans-serif`
      c.textAlign = 'center'
      c.textBaseline = 'middle'
      c.save()
      c.translate(L * 0.4, 0)
      c.rotate(Math.PI / 2)
      c.fillText('52', 0, 0)
      c.restore()
      break
    }
    case 'wreck':
      car(c, 'wreck', L, W, color)
      damage(c, L, W)
      break
    default:
      car(c, type, L, W, color)
  }
  const sprite = { canvas, ox: (PAD + L) * ppm, oy: (PAD + W / 2) * ppm, ppm }
  cache.set(key, sprite)
  return sprite
}

export function spriteBucket(pxPerM: number): number {
  const b = [6, 12, 24, 40]
  for (const v of b) if (pxPerM <= v) return v
  return 56
}
