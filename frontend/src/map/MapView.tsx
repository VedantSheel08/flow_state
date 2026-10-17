import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { apiUrl } from '../lib/endpoints'
import { frames, type PersonView, type VehicleView } from '../lib/frames'
import { perf } from '../lib/perf'
import { HOME_JUNCTION, useStore } from '../lib/store'
import type { NetworkGeom, Tracked } from '../lib/types'
import { Camera } from './camera'
import { FocusBar } from './FocusBar'
import { distToPolyline, pointInPolygon } from './geom'
import { MapControls } from './MapControls'
import { Minimap } from './Minimap'
import { cycleJunction, focusScale, stepJunction, type Dir, type NavJunction } from './nav'
import { render, type RenderState } from './renderer'
import { buildScene, type Scene } from './scene'
import { CODE_TO_TYPE, DIMS } from './vehicles'

export interface MapApi {
  zoom: (f: number) => void
  fit: () => void
  home: () => void
  focus: (x: number, y: number, scale?: number) => void
  scale: () => number
  step: (dir: Dir) => void
  cycle: (delta: 1 | -1) => void
  toggleOverview: () => void
}

export const mapApi: { current: MapApi | null } = { current: null }

/** Camera target for the current mode: the focused intersection or the whole road network. */
function targetView(scene: Scene, cam: Camera): { x: number; y: number; s: number } {
  const st = useStore.getState()
  if (st.camMode === 'network') {
    const b = scene.roadBounds
    return { x: (b[0] + b[2]) / 2, y: (b[1] + b[3]) / 2, s: cam.fitScale(b, 36) }
  }
  const j = scene.junctions.find((q) => q.id === st.focusJunction) ?? scene.junctions.find((q) => q.id === HOME_JUNCTION) ?? scene.junctions[0]
  const [x, y] = j ? [j.x, j.y] : scene.focus
  return { x, y, s: focusScale(cam.width, cam.height) }
}

// Vehicle classes with time-based animation (beacons, hazard flashers) that need redraws while paused.
const ANIMATED_CODES = new Set([CODE_TO_TYPE.indexOf('ambulance'), CODE_TO_TYPE.indexOf('police'), CODE_TO_TYPE.indexOf('wreck')])

export const MapView = memo(function MapView({ net }: { net: NetworkGeom }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const scene: Scene = useMemo(() => buildScene(net), [net])
  const camRef = useRef(new Camera())
  const [cursor, setCursor] = useState('grab')
  const [scaleBar, setScaleBar] = useState({ px: 100, label: '20 m' })
  const route = useRef<{ id: string; edges: string[] } | null>(null)
  const hoverEdge = useRef<string | null>(null)
  // set whenever something not captured by sim time / camera motion changes the picture
  const dirty = useRef(true)
  // true once the user pans/zooms away from the requested camera view (mirrored into the store for the UI)
  const detached = useRef(false)
  const setDetached = (v: boolean) => {
    detached.current = v
    if (useStore.getState().camDetached !== v) useStore.getState().set({ camDetached: v })
  }
  const navList: NavJunction[] = useMemo(() => scene.junctions.map((j) => ({ id: j.id, name: j.name, x: j.x, y: j.y, control: j.control })), [scene])

  // camera API for controls / other panels
  useEffect(() => {
    const cam = camRef.current
    const st = () => useStore.getState()
    mapApi.current = {
      zoom: (f) => {
        cam.zoomAt(f, cam.width / 2, cam.height / 2)
      },
      fit: () => st().showNetwork(),
      home: () => st().focusOn(HOME_JUNCTION),
      focus: (x, y, s) => {
        cam.glide(x, y, s ?? Math.max(cam.tscale, 8))
      },
      scale: () => cam.scale,
      step: (dir) => {
        const s = st()
        if (s.camMode === 'network') return s.focusOn(s.focusJunction)
        const next = stepJunction(navList, s.focusJunction, dir)
        if (next) s.focusOn(next)
      },
      cycle: (delta) => {
        const next = cycleJunction(navList, st().focusJunction, delta)
        if (next) st().focusOn(next)
      },
      toggleOverview: () => {
        const s = st()
        if (s.camMode === 'network') s.focusOn(s.focusJunction)
        else s.showNetwork()
      },
    }
    return () => {
      mapApi.current = null
    }
  }, [scene, navList])

  // initial view: the focused intersection, at detail scale
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
      const t = targetView(scene, cam)
      cam.jump(t.x, t.y, t.s)
      setDetached(false)
    }
  }, [scene])

  // camera requests from the store (intersection focus / network overview)
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.camNonce === prev.camNonce) return
        const t = targetView(scene, camRef.current)
        setDetached(false)
        camRef.current.glide(t.x, t.y, t.s)
      }),
    [scene],
  )

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
    const busLines = net.city.busLines.map((l) => ({ id: l.id, color: l.color }))
    let lastScaleBar = 0
    let lastErr = 0
    let framedScale = 0 // target scale for the previous viewport size
    let lastT = NaN
    const unsubscribe = useStore.subscribe(() => {
      dirty.current = true
    })
    const resize = () => {
      dirty.current = true
      const el = wrapRef.current!
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      canvas.width = Math.round(el.clientWidth * dpr)
      canvas.height = Math.round(el.clientHeight * dpr)
      canvas.style.width = `${el.clientWidth}px`
      canvas.style.height = `${el.clientHeight}px`
      cam.setViewport(el.clientWidth, el.clientHeight)
      const [x0, y0, x1, y1] = scene.roadBounds
      cam.minScale = Math.min(0.45, cam.width / (x1 - x0 + 200), cam.height / (y1 - y0 + 200))
      // keep the requested view framed while panels open/close, preserving any extra zoom
      if (!detached.current && !cam.gliding && !useStore.getState().follow) {
        const t = targetView(scene, cam)
        cam.jump(t.x, t.y, t.s * (cam.scale / (framedScale || t.s)))
        framedScale = t.s
      }
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(wrapRef.current!)
    const loop = (now: number) => {
      perf.frameDt.push(now - last)
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const st = useStore.getState()
      const t = frames.tick(now)
      if (st.view !== 'simulate') {
        raf = requestAnimationFrame(loop)
        return
      }
      const ti = performance.now()
      frames.vehiclesAt(t, vehicles)
      if (st.layers.pedestrians) frames.personsAt(t, persons)
      else persons.length = 0
      perf.interpMs.push(performance.now() - ti)
      drawn = vehicles
      perf.vehicles.push(vehicles.length)
      const sel = st.selection
      if (st.follow && sel?.kind === 'vehicle') {
        const v = vehicles.find((q) => q.id === sel.id)
        if (v) {
          cam.cancelGlide()
          cam.tx = v.x
          cam.ty = v.y
        }
      }
      const moving = cam.update(dt)
      if (!cam.gliding) {
        // detached once the view has wandered off the requested target (zooming in place is fine)
        const tg = targetView(scene, cam)
        const off = Math.hypot(cam.x - tg.x, cam.y - tg.y) * cam.scale
        setDetached(st.follow || off > Math.min(cam.width, cam.height) * 0.3 || (st.camMode === 'network' && cam.scale > tg.s * 2.5))
        framedScale = tg.s
      }
      trackedMap.clear()
      for (const tr of st.stats?.tracked ?? []) trackedMap.set(tr.id, tr)
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
        busLines,
        now,
        pick: !!st.pick,
      }
      // Only draw when the picture can have changed: new sim time, camera motion,
      // UI state, or something animating in place (beacons, accident zones).
      const animated = rs.incidents.some((i) => !i.cleared) || vehicles.some((v) => ANIMATED_CODES.has(v.code))
      const changed = dirty.current || moving || t !== lastT || animated
      dirty.current = false
      lastT = t
      if (changed) {
        try {
          const tr = performance.now()
          const visible = render(ctx, scene, cam, rs, canvas.width / Math.max(1, cam.width))
          perf.renderMs.push(performance.now() - tr)
          perf.visible.push(visible)
        } catch (err) {
          // never let one bad frame stop the loop
          if (now - lastErr > 5000) {
            lastErr = now
            console.error('render error', err)
          }
        }
      }
      if (now - lastScaleBar > 250) {
        lastScaleBar = now
        const target = 90 / cam.scale
        const nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000].find((n) => n >= target * 0.6) ?? 1000
        setScaleBar((p) => (p.label === `${nice} m` && Math.abs(p.px - nice * cam.scale) < 0.5 ? p : { px: nice * cam.scale, label: `${nice} m` }))
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      unsubscribe()
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
      fetch(apiUrl(`/api/runs/current/vehicles/${encodeURIComponent(selection.id)}`))
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
        const he = pickEdge(scene, wx, wy)
        if (he !== hoverEdge.current) dirty.current = true
        hoverEdge.current = he
        setCursor(hoverEdge.current ? 'pointer' : 'crosshair')
      } else {
        const [sx, sy] = local(e)
        const [wx, wy] = cam.toWorld(sx, sy)
        const hit = pickVehicle(wx, wy, cam.scale) || pickJunction(scene, wx, wy, cam.scale)
        setCursor(hit ? 'pointer' : 'grab')
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
      const j = pickJunction(scene, wx, wy, cam.scale)
      if (j) {
        st.select({ kind: 'junction', id: j.id })
        // clicking an intersection in the overview (or a neighbour) flies to it
        if (st.camMode === 'network' || st.focusJunction !== j.id) st.focusOn(j.id)
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
    <div ref={wrapRef} className="@container absolute inset-0 overflow-hidden bg-[#E9EBE7]">
      <canvas ref={canvasRef} className="block touch-none select-none" style={{ cursor }} data-testid="map-canvas" />
      <FocusBar scene={scene} junctions={navList} />
      <MapControls />
      <Minimap scene={scene} camRef={camRef} scaleBar={scaleBar} />
    </div>
  )
})

// the vehicles drawn in the latest frame; hit-testing uses exactly what is on screen
let drawn: VehicleView[] = []

function pickVehicle(wx: number, wy: number, scale: number): string | null {
  let best: string | null = null
  let bestD = Infinity
  const slack = Math.max(0.6, 6 / scale)
  for (const v of drawn) {
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

function pickJunction(scene: Scene, wx: number, wy: number, scale: number) {
  return scene.junctions.find((q) => pointInPolygon(wx, wy, q.poly) || Math.hypot(q.x - wx, q.y - wy) < Math.max(14, 10 / scale)) ?? null
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
