// Overview inset: the street network from SUMO lane geometry, intersection
// dots and the live camera viewport. Click to fly to an intersection.

import { ChevronDown, ChevronUp } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Tip } from '../components/ui'
import { cn } from '../lib/format'
import { useStore } from '../lib/store'
import type { Camera } from './camera'
import type { Pt } from './geom'
import { mapApi } from './MapView'
import type { Scene } from './scene'

const W = 184
const PAD = 8
/** Gap kept between the dragged inset and the edge of the map. */
const EDGE = 8
const POS_KEY = 'flowstate.minimap'

/** Inset position as fractions of the free space inside the map, so it stays in bounds on resize. */
type Frac = { fx: number; fy: number }

function loadFrac(): Frac | null {
  try {
    const v = JSON.parse(localStorage.getItem(POS_KEY) ?? 'null')
    return v && Number.isFinite(v.fx) && Number.isFinite(v.fy) ? v : null
  } catch {
    return null
  }
}

function saveFrac(f: Frac | null) {
  try {
    if (f) localStorage.setItem(POS_KEY, JSON.stringify(f))
    else localStorage.removeItem(POS_KEY)
  } catch {
    /* storage unavailable */
  }
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/** Fixed projection from world metres into the inset. */
function projection(scene: Scene) {
  const [x0, , x1, y1] = scene.roadBounds
  const k = (W - PAD * 2) / (x1 - x0)
  return { k, x0, y1, sx: (x: number) => PAD + (x - x0) * k, sy: (y: number) => PAD + (y1 - y) * k }
}

const pathOf = (pts: Pt[], p: ReturnType<typeof projection>) => pts.map((q, i) => `${i ? 'L' : 'M'}${p.sx(q[0]).toFixed(1)} ${p.sy(q[1]).toFixed(1)}`).join('')

export function Minimap({ scene, camRef, scaleBar }: { scene: Scene; camRef: { readonly current: Camera }; scaleBar: { px: number; label: string } }) {
  const [open, setOpen] = useState(true)
  const [frac, setFrac] = useState<Frac | null>(loadFrac)
  // free space the inset can move in: map size minus card size minus the edge gap
  const [free, setFree] = useState<{ w: number; h: number } | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ id: number; x: number; y: number; left: number; top: number } | null>(null)
  const focusId = useStore((s) => s.focusJunction)
  const camMode = useStore((s) => s.camMode)
  const focusOn = useStore((s) => s.focusOn)
  const viewRef = useRef<SVGRectElement>(null)
  const proj = useMemo(() => projection(scene), [scene])
  const { k, x0, y1, sx, sy } = proj
  const H = Math.round((y1 - scene.roadBounds[1]) * k + PAD * 2)

  const roads = useMemo(() => {
    const major: string[] = []
    const minor: string[] = []
    for (const [, c] of scene.edgeCenter) (c.cls.startsWith('arterial') ? major : minor).push(pathOf(c.pts, proj))
    return { major: major.join(''), minor: minor.join('') }
  }, [scene, proj])

  // viewport rectangle follows the camera without re-rendering React
  useEffect(() => {
    if (!open) return
    let raf = 0
    const { sx, sy } = proj
    const loop = () => {
      const cam = camRef.current
      const r = viewRef.current
      if (r) {
        const [vx0, vy0, vx1, vy1] = cam.viewRect()
        // never smaller than a few pixels so the view stays findable when zoomed in
        const cx = (sx(vx0) + sx(vx1)) / 2
        const cy = (sy(vy0) + sy(vy1)) / 2
        const hw = Math.max(5, (sx(vx1) - sx(vx0)) / 2)
        const hh = Math.max(5, (sy(vy0) - sy(vy1)) / 2)
        const ax = Math.max(0, cx - hw)
        const ay = Math.max(0, cy - hh)
        const bx = Math.min(W, cx + hw)
        const by = Math.min(H, cy + hh)
        r.setAttribute('x', String(ax))
        r.setAttribute('y', String(ay))
        r.setAttribute('width', String(Math.max(0, bx - ax)))
        r.setAttribute('height', String(Math.max(0, by - ay)))
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [camRef, proj, open, H])

  const onClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const wx = x0 + (e.clientX - rect.left - PAD) / k
    const wy = y1 - (e.clientY - rect.top - PAD) / k
    let best: { id: string; d: number } | null = null
    for (const j of scene.junctions) {
      const d = Math.hypot(j.x - wx, j.y - wy)
      if (!best || d < best.d) best = { id: j.id, d }
    }
    if (best && best.d < 60) focusOn(best.id)
    else mapApi.current?.focus(wx, wy, Math.max(camRef.current.scale, 3))
  }

  // track the map and card sizes so a dragged inset is re-clamped on resize and collapse
  useEffect(() => {
    const card = cardRef.current
    // the map wrapper; offsetParent would be null while the card is hidden on narrow maps
    const parent = card?.parentElement
    if (!card || !parent) return
    const measure = () => setFree({ w: Math.max(0, parent.clientWidth - card.offsetWidth - EDGE * 2), h: Math.max(0, parent.clientHeight - card.offsetHeight - EDGE * 2) })
    const ro = new ResizeObserver(measure)
    ro.observe(parent)
    ro.observe(card)
    measure()
    return () => ro.disconnect()
  }, [])

  const onHandleDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest('button') || !cardRef.current) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { id: e.pointerId, x: e.clientX, y: e.clientY, left: cardRef.current.offsetLeft, top: cardRef.current.offsetTop }
  }
  const onHandleMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId || !free) return
    const fx = free.w ? clamp01((d.left + e.clientX - d.x - EDGE) / free.w) : 0
    const fy = free.h ? clamp01((d.top + e.clientY - d.y - EDGE) / free.h) : 0
    setFrac({ fx, fy })
  }
  const onHandleUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id !== e.pointerId) return
    drag.current = null
    saveFrac(frac)
  }
  const resetPos = () => {
    setFrac(null)
    saveFrac(null)
  }

  const placed = frac && free
  return (
    <div
      ref={cardRef}
      className={cn('glass absolute z-[21] hidden overflow-hidden rounded-xl @[700px]:block', !placed && 'bottom-[76px] left-[calc(var(--rail-w,0px)+12px)]')}
      style={placed ? { left: EDGE + frac.fx * free.w, top: EDGE + frac.fy * free.h } : undefined}
    >
      <div
        className="flex h-8 cursor-grab touch-none select-none items-center justify-between gap-3 px-2.5 active:cursor-grabbing"
        title="Drag to move · double-click to reset"
        onPointerDown={onHandleDown}
        onPointerMove={onHandleMove}
        onPointerUp={onHandleUp}
        onPointerCancel={onHandleUp}
        onDoubleClick={(e) => !(e.target as HTMLElement).closest('button') && resetPos()}
      >
        <span className="label-caps">Overview</span>
        <span className="flex items-center gap-2">
          <span className="flex flex-col items-start leading-none">
            <span className="tnum text-[10px] font-medium text-ink-2">{scaleBar.label}</span>
            <span className="mt-[3px] block h-[4px] border-x border-b border-ink-2" style={{ width: Math.min(90, scaleBar.px) }} />
          </span>
          <Tip label={open ? 'Hide overview' : 'Show overview'} side="top">
            <button aria-label={open ? 'Hide overview' : 'Show overview'} onClick={() => setOpen((o) => !o)} className="flex h-6 w-6 items-center justify-center rounded-md text-ink-3 hover:bg-black/[0.05] hover:text-ink">
              {open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            </button>
          </Tip>
        </span>
      </div>
      {open && (
        <svg width={W} height={H} className="block cursor-pointer border-t border-black/[0.06] bg-[#EEF0EC]" onClick={onClick} role="img" aria-label="Network overview">
          <path d={roads.minor} stroke="#AEB4BC" strokeWidth={2.2} fill="none" strokeLinecap="round" />
          <path d={roads.major} stroke="#8D949E" strokeWidth={3.4} fill="none" strokeLinecap="round" />
          {scene.junctions.map((j) => {
            const active = camMode === 'intersection' && j.id === focusId
            return (
              <g key={j.id}>
                {active && <circle cx={sx(j.x)} cy={sy(j.y)} r={8} fill="rgba(37,99,235,0.18)" />}
                <circle cx={sx(j.x)} cy={sy(j.y)} r={active ? 4 : 2.6} fill={active ? '#2563EB' : j.control === 'traffic_light' ? '#475467' : '#C2410C'} stroke="#fff" strokeWidth={1.2} />
              </g>
            )
          })}
          <rect ref={viewRef} fill="rgba(37,99,235,0.07)" stroke="#2563EB" strokeWidth={1.3} rx={2} />
        </svg>
      )}
    </div>
  )
}
