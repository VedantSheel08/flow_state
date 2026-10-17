// Overview inset: the street network from SUMO lane geometry, intersection
// dots and the live camera viewport. Click to fly to an intersection.

import { ChevronDown, ChevronUp } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Tip } from '../components/ui'
import { useStore } from '../lib/store'
import type { Camera } from './camera'
import type { Pt } from './geom'
import { mapApi } from './MapView'
import type { Scene } from './scene'

const W = 184
const PAD = 8

/** Fixed projection from world metres into the inset. */
function projection(scene: Scene) {
  const [x0, , x1, y1] = scene.roadBounds
  const k = (W - PAD * 2) / (x1 - x0)
  return { k, x0, y1, sx: (x: number) => PAD + (x - x0) * k, sy: (y: number) => PAD + (y1 - y) * k }
}

const pathOf = (pts: Pt[], p: ReturnType<typeof projection>) => pts.map((q, i) => `${i ? 'L' : 'M'}${p.sx(q[0]).toFixed(1)} ${p.sy(q[1]).toFixed(1)}`).join('')

export function Minimap({ scene, camRef, scaleBar }: { scene: Scene; camRef: { readonly current: Camera }; scaleBar: { px: number; label: string } }) {
  const [open, setOpen] = useState(true)
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

  return (
    <div className="glass absolute bottom-[76px] left-3 z-10 hidden overflow-hidden rounded-xl @[700px]:block">
      <div className="flex h-8 items-center justify-between gap-3 px-2.5">
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
