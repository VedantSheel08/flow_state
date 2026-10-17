import { useEffect, useMemo, useRef, useState } from 'react'
import { frames, type PersonView, type VehicleView } from '../lib/frames'
import { useStore } from '../lib/store'
import type { NetworkGeom, Tracked } from '../lib/types'
import { Camera } from './camera'
import { distToPolyline, pointInPolygon } from './geom'
import { MapControls } from './MapControls'
import { render, type RenderState } from './renderer'
import { buildScene, type Scene } from './scene'
import { CODE_TO_TYPE, DIMS } from './vehicles'

export interface MapApi {
  zoom: (f: number) => void
  fit: () => void
  home: () => void
  focus: (x: number, y: number, scale?: number) => void
  scale: () => number
}

export const mapApi: { current: MapApi | null } = { current: null }

function defaultScale(w: number) {
  return Math.min(10, Math.max(5.5, w / 135))
}

export function MapView({ net }: { net: NetworkGeom }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const scene: Scene = useMemo(() => buildScene(net), [net])
  const camRef = useRef(new Camera())
  const [cursor, setCursor] = useState('grab')
  const [scaleBar, setScaleBar] = useState({ px: 100, label: '20 m' })
  const route = useRef<{ id: string; edges: string[] } | null>(null)
  const hoverEdge = useRef<string | null>(null)

  // camera API for controls / other panels
  useEffect(() => {
    const cam = camRef.current
    const home = () => cam.flyTo(scene.focus[0] + 25, scene.focus[1] - 12, defaultScale(cam.width))
    mapApi.current = {
      zoom: (f) => cam.zoomAt(f, cam.width / 2, cam.height / 2),
      fit: () => cam.fit(scene.roadBounds, 30),
      home,
      focus: (x, y, s) => cam.flyTo(x, y, s ?? Math.max(cam.tscale, 8)),
      scale: () => cam.scale,
    }
    return () => {
      mapApi.current = null
    }
  }, [scene])

  // initial view: one or two intersections, detailed scale
  const placed = useRef<string | null>(null)
  useEffect(() => {
    const cam = camRef.current
    const el = wrapRef.current
    if (!el) return
    cam.setViewport(el.clientWidth, el.clientHeight)
    const [x0, y0, x1, y1] = scene.roadBounds
    cam.minScale = Math.min(cam.width / (x1 - x0 + 200), cam.height / (y1 - y0 + 200))
    if (placed.current !== scene.id) {
      placed.current = scene.id
      cam.jump(scene.focus[0] + 25, scene.focus[1] - 12, defaultScale(cam.width))
    }
  }, [scene])

  // render loop
  useEffect(() => {
    const canvas = canvasRef.current!
    const ctx = canvas.getContext('2d', { alpha: false })!
    const cam = camRef.current
    let raf = 0
    let last = performance.now()
    const vehicles: VehicleView[] = []
    const persons: PersonView[] = []
    const trackedMap = new Map<string, Tracked>()
    let lastScaleBar = 0
    let lastErr = 0
    const resize = () => {
      const el = wrapRef.current!
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      canvas.width = Math.round(el.clientWidth * dpr)
      canvas.height = Math.round(el.clientHeight * dpr)
      canvas.style.width = `${el.clientWidth}px`
      canvas.style.height = `${el.clientHeight}px`
      cam.setViewport(el.clientWidth, el.clientHeight)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(wrapRef.current!)
    const loop = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const st = useStore.getState()
      const t = frames.tick(now)
      if (st.view !== 'simulate') {
        raf = requestAnimationFrame(loop)
        return
      }
      frames.vehiclesAt(t, vehicles)
      if (st.layers.pedestrians) frames.personsAt(t, persons)
      else persons.length = 0
      const sel = st.selection
      if (st.follow && sel?.kind === 'vehicle') {
        const v = vehicles.find((q) => q.id === sel.id)
        if (v) {
          cam.tx = v.x
          cam.ty = v.y
        }
      }
      cam.update(dt)
      trackedMap.clear()
      for (const tr of st.stats?.tracked ?? []) trackedMap.set(tr.id, tr)
      const busColors: Record<string, string> = {}
      for (const l of net.city.busLines) busColors[l.id] = l.color
      const rs: RenderState = {
        vehicles,
        persons,
        tls: frames.signalsAt(t),
        layers: st.layers,
        selected: sel?.kind === 'vehicle' ? sel.id : null,
        selectedJunction: sel?.kind === 'junction' ? sel.id : null,
        hoverEdge: st.pick ? hoverEdge.current : null,
        pickedEdge: sel?.kind === 'edge' ? sel.id : null,
        tracked: trackedMap,
        incidents: st.stats?.incidents ?? [],
        edges: st.stats?.edges ?? {},
        route: sel?.kind === 'vehicle' && route.current?.id === sel.id ? route.current.edges : null,
        busColors,
        now,
        pick: !!st.pick,
      }
      try {
        render(ctx, scene, cam, rs, canvas.width / Math.max(1, cam.width))
      } catch (err) {
        // never let one bad frame stop the loop
        if (now - lastErr > 5000) {
          lastErr = now
          console.error('render error', err)
        }
      }
      if (now - lastScaleBar > 250) {
        lastScaleBar = now
        const target = 110 / cam.scale
        const nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000].find((n) => n >= target * 0.6) ?? 1000
        setScaleBar((p) => (p.label === `${nice} m` && Math.abs(p.px - nice * cam.scale) < 0.5 ? p : { px: nice * cam.scale, label: `${nice} m` }))
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [scene, net])

  // fetch the selected vehicle's route for display
  const selection = useStore((s) => s.selection)
  useEffect(() => {
    if (selection?.kind !== 'vehicle') {
      route.current = null
      return
    }
    let alive = true
    const load = () =>
      fetch(`/api/runs/current/vehicles/${encodeURIComponent(selection.id)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (alive && d) route.current = { id: selection.id, edges: d.route }
        })
        .catch(() => {})
    load()
    const iv = setInterval(load, 3000)
    return () => {
      alive = false
      clearInterval(iv)
    }
  }, [selection])

  // ---- interaction ------------------------------------------------------
  useEffect(() => {
    const el = canvasRef.current!
    const cam = camRef.current
    let drag: { x: number; y: number; moved: boolean; t: number } | null = null
    let vel: [number, number, number][] = []

    const local = (e: MouseEvent | WheelEvent | PointerEvent) => {
      const r = el.getBoundingClientRect()
      return [e.clientX - r.left, e.clientY - r.top] as const
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const [sx, sy] = local(e)
      const delta = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      cam.vx = cam.vy = 0
      cam.zoomAt(Math.exp(-delta * 0.0016), sx, sy)
      useStore.getState().set({ follow: false })
    }
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return
      el.setPointerCapture(e.pointerId)
      drag = { x: e.clientX, y: e.clientY, moved: false, t: performance.now() }
      cam.vx = cam.vy = 0
      vel = []
      setCursor('grabbing')
    }
    const onMove = (e: PointerEvent) => {
      const st = useStore.getState()
      if (drag) {
        const dx = e.clientX - drag.x
        const dy = e.clientY - drag.y
        if (!drag.moved && Math.hypot(dx, dy) < 4) return
        if (!drag.moved && st.follow) st.set({ follow: false })
        drag.moved = true
        cam.panBy(dx, dy)
        const now = performance.now()
        vel.push([dx, dy, now - drag.t])
        if (vel.length > 5) vel.shift()
        drag.x = e.clientX
        drag.y = e.clientY
        drag.t = now
        return
      }
      if (st.pick) {
        const [sx, sy] = local(e)
        const [wx, wy] = cam.toWorld(sx, sy)
        hoverEdge.current = pickEdge(scene, wx, wy)
        setCursor(hoverEdge.current ? 'pointer' : 'crosshair')
      } else {
        const [sx, sy] = local(e)
        const [wx, wy] = cam.toWorld(sx, sy)
        const hitV = pickVehicle(wx, wy, cam.scale)
        setCursor(hitV ? 'pointer' : 'grab')
      }
    }
    const onUp = (e: PointerEvent) => {
      if (!drag) return
      const wasDrag = drag.moved
      drag = null
      setCursor('grab')
      if (wasDrag) {
        const sum = vel.reduce((a, v) => [a[0] + v[0], a[1] + v[1], a[2] + v[2]], [0, 0, 0])
        if (sum[2] > 0 && sum[2] < 120) {
          cam.vx = (sum[0] / sum[2]) * 1000
          cam.vy = (sum[1] / sum[2]) * 1000
        }
        return
      }
      const [sx, sy] = local(e)
      const [wx, wy] = cam.toWorld(sx, sy)
      const st = useStore.getState()
      if (st.pick) {
        const eid = pickEdge(scene, wx, wy)
        if (eid) {
          st.setDraft(st.scenarioId, st.pick, eid)
          st.set({ pick: null, selection: { kind: 'edge', id: eid } })
          hoverEdge.current = null
        }
        return
      }
      const vid = pickVehicle(wx, wy, cam.scale)
      if (vid) {
        st.select({ kind: 'vehicle', id: vid })
        return
      }
      const j = scene.junctions.find((q) => pointInPolygon(wx, wy, q.poly) || Math.hypot(q.x - wx, q.y - wy) < 14)
      if (j) {
        st.select({ kind: 'junction', id: j.id })
        return
      }
      st.select(null)
    }
    const onDbl = (e: MouseEvent) => {
      const [sx, sy] = local(e)
      cam.zoomAt(2, sx, sy)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('pointerdown', onDown)
    el.addEventListener('pointermove', onMove)
    el.addEventListener('pointerup', onUp)
    el.addEventListener('dblclick', onDbl)
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('pointermove', onMove)
      el.removeEventListener('pointerup', onUp)
      el.removeEventListener('dblclick', onDbl)
    }
  }, [scene])

  return (
    <div ref={wrapRef} className="absolute inset-0 overflow-hidden bg-[#E8EAE6]">
      <canvas ref={canvasRef} className="block touch-none select-none" style={{ cursor }} data-testid="map-canvas" />
      <MapControls />
      <div className="pointer-events-none absolute bottom-[76px] left-3 flex items-end gap-2">
        <div className="flex flex-col items-start rounded-md border border-border bg-white/90 px-2 py-1 shadow-xs">
          <span className="text-[10.5px] font-medium tabular-nums text-ink-2">{scaleBar.label}</span>
          <span className="mt-0.5 block h-[5px] border-x border-b border-ink-2" style={{ width: scaleBar.px }} />
        </div>
      </div>
    </div>
  )
}

const tmpVehicles: VehicleView[] = []

function pickVehicle(wx: number, wy: number, scale: number): string | null {
  frames.vehiclesAt(frames.renderT, tmpVehicles)
  let best: string | null = null
  let bestD = Infinity
  const slack = Math.max(0.6, 6 / scale)
  for (const v of tmpVehicles) {
    const type = CODE_TO_TYPE[v.code] ?? 'sedan'
    const [L, W] = DIMS[type]
    const a = (v.angle * Math.PI) / 180
    const dx = Math.sin(a)
    const dy = Math.cos(a)
    const rx = wx - v.x
    const ry = wy - v.y
    const u = rx * dx + ry * dy // along (front = 0, rear = -L)
    const w = -rx * dy + ry * dx
    if (u <= slack && u >= -L - slack && Math.abs(w) <= W / 2 + slack) {
      const d = Math.abs(u + L / 2) + Math.abs(w)
      if (d < bestD) {
        bestD = d
        best = v.id
      }
    }
  }
  return best
}

function pickEdge(scene: Scene, wx: number, wy: number): string | null {
  let best: string | null = null
  let bestD = Infinity
  for (const ln of scene.hitLanes) {
    if (ln.role !== 'car') continue
    const d = distToPolyline(wx, wy, ln.pts)
    if (d < ln.w / 2 + 0.5 && d < bestD) {
      bestD = d
      best = ln.edge
    }
  }
  return best
}
