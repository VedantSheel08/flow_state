import { useEffect, useRef } from 'react'
import type { Geometry, Veh } from '../lib/types'

export interface DrawData {
  vehicles: Veh[]
  signals: string[] // one state string per controlled junction, SUMO link order
  queue?: number[] // measured halting vehicles per approach
  forecast?: number[] // predicted halting vehicles per approach, 30 s ahead
  mlJunctions?: number[] // junction indices whose decision the ML changed recently
  selected?: number | null
}
export interface View {
  cx: number
  cy: number
  scale: number
}

interface Props {
  geom: Geometry
  source: () => DrawData | null
  view?: React.MutableRefObject<View | null> // shared between synchronized maps
  onPick?: (approach: number | null) => void
  label?: string
  accent?: string
}

const SLOT_M = 7.5

function sigColor(c: string): string {
  if (c === 'G' || c === 'g') return '#22c55e'
  if (c === 'y' || c === 'Y') return '#facc15'
  return '#ef4444'
}
function best(state: string, idx: number[]): string {
  let r = 'r'
  for (const k of idx) {
    const c = state[k] ?? 'r'
    if (c === 'G' || c === 'g') return 'G'
    if (c === 'y') r = 'y'
  }
  return r
}

export default function MapCanvas({
  geom,
  source,
  view,
  onPick,
  label,
  accent = '#38bdf8',
}: Props) {
  const ref = useRef<HTMLCanvasElement>(null)
  const local = useRef<View | null>(null)
  const vref = view ?? local
  const srcRef = useRef(source)
  srcRef.current = source

  useEffect(() => {
    const cv = ref.current!
    const ctx = cv.getContext('2d')!
    let raf = 0
    let drag: {
      x: number
      y: number
      cx: number
      cy: number
      moved: boolean
    } | null = null
    let fitted = false,
      lastW = 0,
      lastH = 0
    const [x0, y0, x1, y1] = geom.bounds
    const fit = () => {
      const w = cv.clientWidth,
        h = cv.clientHeight
      vref.current = {
        cx: (x0 + x1) / 2,
        cy: (y0 + y1) / 2,
        scale: Math.min(w / (x1 - x0 + 40), h / (y1 - y0 + 40)),
      }
    }
    const toScreen = (
      x: number,
      y: number,
      v: View,
      w: number,
      h: number,
    ): [number, number] => [
      w / 2 + (x - v.cx) * v.scale,
      h / 2 - (y - v.cy) * v.scale,
    ]
    const toWorld = (
      sx: number,
      sy: number,
      v: View,
      w: number,
      h: number,
    ): [number, number] => [
      v.cx + (sx - w / 2) / v.scale,
      v.cy - (sy - h / 2) / v.scale,
    ]

    const frame = () => {
      const dpr = window.devicePixelRatio || 1
      const w = cv.clientWidth,
        h = cv.clientHeight
      if (
        cv.width !== Math.round(w * dpr) ||
        cv.height !== Math.round(h * dpr)
      ) {
        cv.width = Math.round(w * dpr)
        cv.height = Math.round(h * dpr)
      }
      if (w < 10 || h < 10) {
        raf = requestAnimationFrame(frame)
        return
      } // hidden tab: wait until laid out
      if (
        !vref.current ||
        (!fitted && !view) ||
        (fitted && (w !== lastW || h !== lastH))
      )
        fit()
      fitted = true
      lastW = w
      lastH = h
      const v = vref.current!
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.fillStyle = '#13232c'
      ctx.fillRect(0, 0, w, h)
      ctx.strokeStyle = 'rgba(130,160,170,0.045)'
      ctx.lineWidth = 1
      ctx.beginPath()
      for (let x = 0; x < w; x += 28) {
        ctx.moveTo(x, 0)
        ctx.lineTo(x, h)
      }
      for (let y = 0; y < h; y += 28) {
        ctx.moveTo(0, y)
        ctx.lineTo(w, y)
      }
      ctx.stroke()
      const d = srcRef.current()
      // roads
      ctx.lineCap = 'butt'
      for (const j of geom.junctions) {
        ctx.beginPath()
        j.s.forEach(([x, y], i) => {
          const [sx, sy] = toScreen(x, y, v, w, h)
          if (i) ctx.lineTo(sx, sy)
          else ctx.moveTo(sx, sy)
        })
        ctx.closePath()
        ctx.fillStyle = '#35454e'
        ctx.fill()
      }
      for (const ln of geom.lanes) {
        ctx.beginPath()
        ln.s.forEach(([x, y], i) => {
          const [sx, sy] = toScreen(x, y, v, w, h)
          if (i) ctx.lineTo(sx, sy)
          else ctx.moveTo(sx, sy)
        })
        ctx.strokeStyle = ln.i ? '#35454e' : '#3d4e57'
        ctx.lineWidth = Math.max(1, ln.w * v.scale * 0.95)
        ctx.stroke()
      }
      if (d) {
        // measured queues (solid) and 30 s forecasts (dashed outline), drawn back from the stop line
        geom.approaches.forEach((a, i) => {
          const dx = a.a[0] - a.b[0],
            dy = a.a[1] - a.b[1]
          const L = Math.hypot(dx, dy) || 1
          const ux = dx / L,
            uy = dy / L
          const line = (m: number) => {
            const len = Math.min(L, (m / 2) * SLOT_M)
            const [sx, sy] = toScreen(
              a.b[0] + ux * 3.4 - uy * 4.2,
              a.b[1] + uy * 3.4 + ux * 4.2,
              v,
              w,
              h,
            )
            const [ex, ey] = toScreen(
              a.b[0] + ux * (3.4 + len) - uy * 4.2,
              a.b[1] + uy * (3.4 + len) + ux * 4.2,
              v,
              w,
              h,
            )
            ctx.beginPath()
            ctx.moveTo(sx, sy)
            ctx.lineTo(ex, ey)
            ctx.stroke()
          }
          if (d.queue && d.queue[i] > 0) {
            ctx.setLineDash([])
            ctx.lineWidth = Math.max(3, 2.2 * v.scale)
            ctx.strokeStyle =
              d.queue[i] >= a.cap * 0.5
                ? 'rgba(224,111,100,0.7)'
                : 'rgba(233,168,93,0.5)'
            line(d.queue[i])
          }
          if (d.forecast && d.forecast[i] > 0.3) {
            ctx.setLineDash([5, 4])
            ctx.lineWidth = Math.max(2, 1.2 * v.scale)
            ctx.strokeStyle =
              d.forecast[i] >= a.cap * 0.5 ? '#87d8c8' : '#65afa1'
            line(d.forecast[i])
            ctx.setLineDash([])
          }
          if (d.selected === i) {
            const [sx, sy] = toScreen(
              a.b[0] + ux * 30 - uy * 4.2,
              a.b[1] + uy * 30 + ux * 4.2,
              v,
              w,
              h,
            )
            ctx.strokeStyle = '#fff'
            ctx.lineWidth = 2
            ctx.beginPath()
            ctx.arc(sx, sy, 10, 0, Math.PI * 2)
            ctx.stroke()
          }
        })
        // vehicles: SUMO position = front bumper, angle = degrees clockwise from north
        for (const veh of d.vehicles) {
          const x = veh[1] / 10,
            y = veh[2] / 10,
            ang = (veh[3] * Math.PI) / 180
          const len = veh[4] === 1 ? 8 : veh[4] === 2 ? 6 : 4.5
          const [sx, sy] = toScreen(x, y, v, w, h)
          ctx.save()
          ctx.translate(sx, sy)
          ctx.rotate(ang)
          ctx.fillStyle =
            veh[4] === 2
              ? '#ffffff'
              : veh[6]
                ? '#e06f64'
                : veh[5] < 30
                  ? '#e9a85d'
                  : veh[4] === 1
                    ? '#a6bacb'
                    : accent
          const bw = Math.max(3, 1.8 * v.scale),
            bl = Math.max(len * 1.4, len * v.scale) // stay visible when zoomed out
          ctx.fillRect(-bw / 2, 0, bw, bl)
          if (veh[4] === 2) {
            ctx.fillStyle = '#ef4444'
            ctx.fillRect(-bw / 2, bl * 0.15, bw, bl * 0.2)
          }
          ctx.restore()
        }
        // signal heads
        const ji = new Map(geom.controlled.map((j, i) => [j, i]))
        for (const hd of geom.heads) {
          const st = d.signals[ji.get(hd.j) ?? -1]
          if (!st) continue
          const [sx, sy] = toScreen(hd.x, hd.y, v, w, h)
          ctx.fillStyle = sigColor(best(st, hd.k))
          ctx.beginPath()
          ctx.arc(sx, sy, Math.max(2.5, 0.9 * v.scale), 0, Math.PI * 2)
          ctx.fill()
        }
        // ML intervention rings
        if (d.mlJunctions?.length) {
          for (const k of d.mlJunctions) {
            const jn = geom.junctions.find((j) => j.id === geom.controlled[k])
            if (!jn) continue
            const [sx, sy] = toScreen(jn.c[0], jn.c[1], v, w, h)
            ctx.strokeStyle = '#39bca6'
            ctx.lineWidth = 2
            ctx.beginPath()
            ctx.arc(sx, sy, 16 * Math.max(1, v.scale / 1.2), 0, Math.PI * 2)
            ctx.stroke()
          }
        }
      }
      for (const j of geom.junctions) {
        if (!j.tls) continue
        const [sx, sy] = toScreen(j.c[0], j.c[1], v, w, h)
        ctx.fillStyle = '#a0b0b8'
        ctx.font = '10px system-ui'
        ctx.fillText(j.id, sx + 14, sy - 14)
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const v = vref.current!
      const r = cv.getBoundingClientRect()
      const [wx, wy] = toWorld(
        e.clientX - r.left,
        e.clientY - r.top,
        v,
        cv.clientWidth,
        cv.clientHeight,
      )
      const k = Math.exp(-e.deltaY * 0.0015)
      const scale = Math.min(40, Math.max(0.3, v.scale * k))
      vref.current = {
        scale,
        cx: wx - (wx - v.cx) * (v.scale / scale),
        cy: wy - (wy - v.cy) * (v.scale / scale),
      }
    }
    const onDown = (e: PointerEvent) => {
      const v = vref.current!
      drag = { x: e.clientX, y: e.clientY, cx: v.cx, cy: v.cy, moved: false }
    }
    const onMove = (e: PointerEvent) => {
      if (!drag) return
      const v = vref.current!
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3)
        drag.moved = true
      vref.current = {
        ...v,
        cx: drag.cx - (e.clientX - drag.x) / v.scale,
        cy: drag.cy + (e.clientY - drag.y) / v.scale,
      }
    }
    const onUp = (e: PointerEvent) => {
      if (drag && !drag.moved && onPick) {
        const r = cv.getBoundingClientRect()
        const [wx, wy] = toWorld(
          e.clientX - r.left,
          e.clientY - r.top,
          vref.current!,
          cv.clientWidth,
          cv.clientHeight,
        )
        let bestI: number | null = null,
          bestD = 25
        geom.approaches.forEach((a, i) => {
          const mx = a.b[0] + (a.a[0] - a.b[0]) * 0.15,
            my = a.b[1] + (a.a[1] - a.b[1]) * 0.15
          const dd = Math.hypot(mx - wx, my - wy)
          if (dd < bestD) {
            bestD = dd
            bestI = i
          }
        })
        onPick(bestI)
      }
      drag = null
    }
    const onDbl = () => fit()
    cv.addEventListener('wheel', onWheel, { passive: false })
    cv.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    cv.addEventListener('dblclick', onDbl)
    return () => {
      cancelAnimationFrame(raf)
      cv.removeEventListener('wheel', onWheel)
      cv.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      cv.removeEventListener('dblclick', onDbl)
    }
  }, [geom, vref, onPick, accent])

  return (
    <canvas
      ref={ref}
      className="map"
      role="img"
      aria-label={label ?? 'Live SUMO traffic simulation'}
      title="Scroll to zoom, drag to pan, double-click to fit, click an approach to inspect it"
    />
  )
}
