// Screen-space weather over the map for the Severe Weather scenario: rain
// streaks, drifting snow or a fog wash. Purely visual; SUMO models the effect
// on drivers. Freezes while the run is paused and stays static under
// prefers-reduced-motion.

import { useEffect, useRef } from 'react'
import { useStore } from '../lib/store'

interface Drop {
  x: number
  y: number
  v: number // px per second
  s: number // length (rain) or radius (snow, fog)
  p: number // phase for drift
}

const LOOK: Record<string, { n: number; tint: string }> = {
  light_rain: { n: 140, tint: 'rgba(52,72,102,0.06)' },
  heavy_rain: { n: 420, tint: 'rgba(40,58,88,0.13)' },
  snow: { n: 260, tint: 'rgba(236,242,248,0.16)' },
  fog: { n: 7, tint: 'rgba(232,236,240,0.18)' },
}

/** The run's weather condition, from its overlays or the live scenario status. */
export function useWeather(): string | null {
  const planned = useStore((s) => s.run?.overlays?.find((o) => o.kind === 'weather')?.condition ?? null)
  const live = useStore((s) => (s.stats?.scenario?.weather as string | undefined) ?? null)
  return planned ?? live
}

export function WeatherLayer() {
  const cond = useWeather()
  const moving = useStore((s) => s.view === 'simulate' && s.status.state === 'running' && !s.status.paused)
  if (!cond || !LOOK[cond] || cond === 'clear') return null
  return <WeatherCanvas cond={cond} moving={moving} />
}

function spawn(cond: string, W: number, H: number, anywhere: boolean): Drop {
  const r = Math.random
  if (cond === 'snow') return { x: r() * W, y: anywhere ? r() * H : -6, v: 28 + r() * 40, s: 1 + r() * 1.8, p: r() * 6.28 }
  if (cond === 'fog') return { x: r() * W, y: r() * H, v: 6 + r() * 10, s: 180 + r() * 220, p: r() * 6.28 }
  const heavy = cond === 'heavy_rain'
  return { x: r() * (W + 200) - 100, y: anywhere ? r() * H : -30, v: (heavy ? 900 : 650) + r() * 300, s: (heavy ? 14 : 9) + r() * 8, p: 0 }
}

function WeatherCanvas({ cond, moving }: { cond: string; moving: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current!
    const ctx = canvas.getContext('2d')!
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const look = LOOK[cond]
    let W = 0
    let H = 0
    let drops: Drop[] = []
    const resize = () => {
      const el = canvas.parentElement!
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      W = el.clientWidth
      H = el.clientHeight
      canvas.width = Math.round(W * dpr)
      canvas.height = Math.round(H * dpr)
      canvas.style.width = `${W}px`
      canvas.style.height = `${H}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      // density scales with the map area so a small window is not a blizzard
      const n = Math.round(look.n * Math.min(1.6, Math.max(0.35, (W * H) / (1400 * 850))))
      drops = Array.from({ length: cond === 'fog' ? look.n : n }, () => spawn(cond, W, H, true))
      draw(0)
    }
    const draw = (dt: number) => {
      ctx.clearRect(0, 0, W, H)
      ctx.fillStyle = look.tint
      ctx.fillRect(0, 0, W, H)
      if (reduced) return
      if (cond === 'fog') {
        for (const d of drops) {
          d.x += d.v * dt
          if (d.x - d.s > W) d.x = -d.s
          const g = ctx.createRadialGradient(d.x, d.y, 0, d.x, d.y, d.s)
          g.addColorStop(0, 'rgba(245,247,250,0.32)')
          g.addColorStop(1, 'rgba(245,247,250,0)')
          ctx.fillStyle = g
          ctx.fillRect(d.x - d.s, d.y - d.s, d.s * 2, d.s * 2)
        }
        // thicker towards the edges: visibility falls off with distance
        const v = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.2, W / 2, H / 2, Math.hypot(W, H) * 0.6)
        v.addColorStop(0, 'rgba(240,243,246,0)')
        v.addColorStop(1, 'rgba(240,243,246,0.55)')
        ctx.fillStyle = v
        ctx.fillRect(0, 0, W, H)
        return
      }
      if (cond === 'snow') {
        ctx.fillStyle = 'rgba(255,255,255,0.92)'
        ctx.shadowColor = 'rgba(15,23,42,0.25)'
        ctx.shadowBlur = 2
        for (const d of drops) {
          d.p += dt
          d.y += d.v * dt
          d.x += Math.sin(d.p * 1.3) * 14 * dt
          if (d.y > H + 6) Object.assign(d, spawn(cond, W, H, false))
          ctx.beginPath()
          ctx.arc(d.x, d.y, d.s, 0, Math.PI * 2)
          ctx.fill()
        }
        ctx.shadowBlur = 0
        return
      }
      // rain: streaks falling slightly to the right
      ctx.strokeStyle = cond === 'heavy_rain' ? 'rgba(62,88,128,0.55)' : 'rgba(76,102,138,0.42)'
      ctx.lineWidth = 1.2
      ctx.beginPath()
      for (const d of drops) {
        d.y += d.v * dt
        d.x += d.v * 0.12 * dt
        if (d.y > H + 30) Object.assign(d, spawn(cond, W, H, false))
        ctx.moveTo(d.x, d.y)
        ctx.lineTo(d.x - d.s * 0.12, d.y - d.s)
      }
      ctx.stroke()
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas.parentElement!)
    let raf = 0
    let last = performance.now()
    const loop = (now: number) => {
      draw(Math.min(0.05, (now - last) / 1000))
      last = now
      raf = requestAnimationFrame(loop)
    }
    if (moving && !reduced) raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [cond, moving])
  return <canvas ref={ref} aria-hidden className="pointer-events-none absolute inset-0 z-[5]" data-testid="weather-layer" />
}
