// Floating camera navigator: Intersection / Network mode switch, the focused
// intersection with its live signal compass, prev/next stepping and a
// geographic picker laid out like the street grid.

import { ChevronDown, ChevronLeft, ChevronRight, Crosshair, LocateFixed, Map as MapIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Tip } from '../components/ui'
import { cn } from '../lib/format'
import { useStore } from '../lib/store'
import { approachSignals, type Approach } from './approach'
import { mapApi } from './MapView'
import { CONTROL_LABEL, junctionGrid, readingOrder, shortName, streetPart, type NavJunction } from './nav'
import type { Scene } from './scene'

const LIGHT: Record<string, string> = { G: '#16A34A', g: '#4ADE80', y: '#F5B700', Y: '#F5B700', u: '#F5B700', r: '#EF4444', R: '#EF4444', s: '#EF4444', o: '#CBD2DA', O: '#CBD2DA' }

/** Four bars around a centre point, one per approach, lit with the live signal state. */
export function SignalCompass({ states, control, size = 22 }: { states: Partial<Record<Approach, string>>; control: string; size?: number }) {
  const signalised = control === 'traffic_light'
  const c = size / 2
  const off = size * 0.33
  const len = size * 0.3
  const th = size * 0.14
  const bars: [Approach, number, number, number, number][] = [
    ['N', c - len / 2, c - off - th / 2, len, th],
    ['S', c - len / 2, c + off - th / 2, len, th],
    ['W', c - off - th / 2, c - len / 2, th, len],
    ['E', c + off - th / 2, c - len / 2, th, len],
  ]
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="shrink-0">
      <rect x={0.5} y={0.5} width={size - 1} height={size - 1} rx={size * 0.28} fill="#F4F6F8" stroke="#E2E6EB" />
      <rect x={c - size * 0.11} y={c - size * 0.11} width={size * 0.22} height={size * 0.22} rx={1} fill={signalised ? '#9AA4B1' : '#C62828'} />
      {bars.map(([k, x, y, w, h]) => (
        <rect key={k} x={x} y={y} width={w} height={h} rx={th / 2} fill={signalised ? (LIGHT[states[k] ?? 'o'] ?? LIGHT.o) : '#D5DAE0'} />
      ))}
    </svg>
  )
}

const NO_STATES: Partial<Record<Approach, string>> = {}

function useApproachStates(scene: Scene, junction: string, enabled: boolean) {
  const [states, setStates] = useState<Partial<Record<Approach, string>>>({})
  useEffect(() => {
    if (!enabled) return
    const tick = () => {
      const next = approachSignals(scene, junction)
      setStates((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next))
    }
    tick()
    const iv = setInterval(tick, 200)
    return () => clearInterval(iv)
  }, [scene, junction, enabled])
  return enabled ? states : NO_STATES
}

export function FocusBar({ scene, junctions }: { scene: Scene; junctions: NavJunction[] }) {
  const camMode = useStore((s) => s.camMode)
  const focusId = useStore((s) => s.focusJunction)
  const focusOn = useStore((s) => s.focusOn)
  const showNetwork = useStore((s) => s.showNetwork)
  const pick = useStore((s) => s.pick)
  const detached = useStore((s) => s.camDetached)
  const following = useStore((s) => (s.follow && s.selection?.kind === 'vehicle' ? s.selection.id : null))
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const j = junctions.find((q) => q.id === focusId)
  const order = useMemo(() => readingOrder(junctions), [junctions])
  const index = order.findIndex((q) => q.id === focusId)
  const states = useApproachStates(scene, focusId, camMode === 'intersection' && j?.control === 'traffic_light')

  useEffect(() => {
    if (!open) return
    const close = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [open])

  if (pick) return null
  const network = camMode === 'network'
  return (
    <div ref={ref} className="absolute left-[calc(var(--rail-w,0px)+12px)] top-3 z-10 flex items-start gap-2">
      <div className="glass flex h-10 items-center gap-0.5 rounded-xl p-1" role="tablist" aria-label="Camera view">
        <ModeButton active={!network} onClick={() => focusOn(focusId)} label="Intersection" hint="Focus one intersection (F toggles)">
          <Crosshair size={14} />
        </ModeButton>
        <ModeButton active={network} onClick={showNetwork} label="Network" hint="Whole street network (F toggles)">
          <MapIcon size={14} />
        </ModeButton>
      </div>

      <div className="relative">
        <div className="glass flex h-10 items-center rounded-xl p-1">
          <Tip label="Previous intersection (←/→ ↑/↓ to move)">
            <button aria-label="Previous intersection" onClick={() => mapApi.current?.cycle(-1)} className="flex h-8 w-7 items-center justify-center rounded-lg text-ink-2 hover:bg-black/[0.05] hover:text-ink">
              <ChevronLeft size={15} />
            </button>
          </Tip>
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label="Choose intersection"
            className={cn('flex h-8 w-[200px] items-center gap-2.5 rounded-lg px-2 text-left transition-colors hover:bg-black/[0.04] @[760px]:w-[236px]', open && 'bg-black/[0.05]')}
          >
            {network ? (
              <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[6px] bg-ink text-white">
                <MapIcon size={12} />
              </span>
            ) : (
              <SignalCompass states={states} control={j?.control ?? ''} />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-semibold leading-4 tracking-[-0.01em] text-ink">{network ? 'Full network' : j ? shortName(j.name) : '—'}</span>
              <span className="block truncate text-[11px] leading-[14px] text-ink-3">
                {following
                  ? `Following vehicle ${following}`
                  : detached
                    ? 'Free camera · recenter to return'
                    : network
                      ? `${junctions.length} intersections · click one to focus`
                      : `${CONTROL_LABEL[j?.control ?? ''] ?? 'Intersection'} · ${index + 1} of ${order.length}`}
              </span>
            </span>
            <ChevronDown size={14} className={cn('shrink-0 text-ink-3 transition-transform', open && 'rotate-180')} />
          </button>
          <Tip label="Next intersection">
            <button aria-label="Next intersection" onClick={() => mapApi.current?.cycle(1)} className="flex h-8 w-7 items-center justify-center rounded-lg text-ink-2 hover:bg-black/[0.05] hover:text-ink">
              <ChevronRight size={15} />
            </button>
          </Tip>
        </div>
        {(detached || following) && (
          <button
            onClick={() => (network ? showNetwork() : focusOn(focusId))}
            className="glass anim-in absolute left-0 top-full mt-2 flex h-9 items-center gap-1.5 whitespace-nowrap rounded-xl px-3 text-[12.5px] font-medium text-accent-strong hover:bg-white"
          >
            <LocateFixed size={14} />
            Recenter
          </button>
        )}
        {open && (
          <JunctionPicker
            scene={scene}
            junctions={junctions}
            current={network ? null : focusId}
            onPick={(id) => {
              focusOn(id)
              setOpen(false)
            }}
          />
        )}
      </div>
    </div>
  )
}

function ModeButton({ active, onClick, label, hint, children }: { active: boolean; onClick: () => void; label: string; hint: string; children: React.ReactNode }) {
  return (
    <Tip label={hint}>
      <button
        role="tab"
        aria-selected={active}
        onClick={onClick}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] font-medium transition-all duration-150',
          active ? 'bg-ink text-white shadow-[0_1px_2px_rgba(16,24,40,0.25)]' : 'text-ink-2 hover:bg-black/[0.05] hover:text-ink',
        )}
      >
        {children}
        <span className="hidden @[760px]:inline">{label}</span>
      </button>
    </Tip>
  )
}

// Schematic street map: rows are east-west streets, columns north-south. Lines are
// drawn only between junctions that exist, so a street that stops reads as stopping.
const PITCH = 48
const LABEL_W = 92
const STUB = 14

const firstName = (s: string) => s.split('/')[0].trim().replace(/\s+(Street|Avenue|Road|Boulevard|Way)$/, '')

const OCTAGON = (r: number) =>
  Array.from({ length: 8 }, (_, i) => {
    const a = Math.PI / 8 + (i * Math.PI) / 4
    return `${(r * Math.cos(a)).toFixed(2)},${(r * Math.sin(a)).toFixed(2)}`
  }).join(' ')

/** Control type by shape, never by colour: dot = signal, octagon = stop, octagon + dot = all-way, diamond = priority. */
function ControlGlyph({ control, size = 20 }: { control: string; size?: number }) {
  const c = size / 2
  return (
    <svg width={size} height={size} viewBox={`${-c} ${-c} ${size} ${size}`} aria-hidden className="pointer-events-none overflow-visible">
      {control === 'traffic_light' ? (
        <circle r={4.5} fill="currentColor" />
      ) : control === 'priority' ? (
        <rect x={-3.6} y={-3.6} width={7.2} height={7.2} transform="rotate(45)" fill="var(--color-surface)" stroke="currentColor" strokeWidth={1.5} />
      ) : (
        <>
          <polygon points={OCTAGON(5.2)} fill="var(--color-surface)" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
          {control === 'allway_stop' && <circle r={1.7} fill="currentColor" />}
        </>
      )}
    </svg>
  )
}

const DIR_STEP: Record<string, [number, number]> = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] }

/** Distance (m) from a point to the nearest road centreline in the scene. */
function roadDistance(scene: Scene, x: number, y: number): number {
  let best = Infinity
  for (const { pts } of scene.edgeCenter.values()) {
    for (let i = 1; i < pts.length; i++) {
      const [ax, ay] = pts[i - 1]
      const [bx, by] = pts[i]
      const dx = bx - ax
      const dy = by - ay
      const t = dx || dy ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy))) : 0
      best = Math.min(best, Math.hypot(x - ax - t * dx, y - ay - t * dy))
    }
  }
  return best
}

/** True when a real road runs between two junctions (sampled along the straight line between them). */
const ROAD_TOL = 8
const roadBetween = (scene: Scene, a: NavJunction, b: NavJunction) =>
  [0.25, 0.5, 0.75].every((t) => roadDistance(scene, a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t) < ROAD_TOL)

function JunctionPicker({ scene, junctions, current, onPick }: { scene: Scene; junctions: NavJunction[]; current: string | null; onPick: (id: string) => void }) {
  const grid = useMemo(() => junctionGrid(junctions), [junctions])
  // a line can change name along its length (Blue Jays Way / Peter, Windsor / Widmer): list every name on it
  const lineName = (cells: NavJunction[], side: 0 | 1) => [...new Set(cells.map((q) => streetPart(q.name, side)))].join(' / ')
  const colName = (c: number) => lineName(grid.cells.filter((q) => q.c === c), 0)
  const rowName = (r: number) => lineName(grid.cells.filter((q) => q.r === r), 1)
  const [hover, setHover] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | null>(current ?? grid.cells[0]?.id ?? null)
  const [focused, setFocused] = useState(false)
  const nodes = useRef(new Map<string, HTMLButtonElement>())

  // Open with the keyboard cursor on the selected junction so arrows start from there.
  useEffect(() => {
    if (cursor) nodes.current.get(cursor)?.focus({ preventScroll: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const W = grid.cols * PITCH
  const H = grid.rows * PITCH
  const at = (n: number) => n * PITCH + PITCH / 2
  // Long street names: alternate label heights so each name gets two pitches of room.
  const stagger = Array.from({ length: grid.cols }, (_, c) => firstName(colName(c))).some((n) => n.length > 6)
  const headH = stagger ? 34 : 20

  const segments = useMemo(() => {
    const segs: [number, number, number, number][] = []
    const lines = (key: 'r' | 'c', other: 'r' | 'c', count: number, span: number, horizontal: boolean) => {
      for (let i = 0; i < count; i++) {
        const cells = grid.cells.filter((q) => q[key] === i).sort((a, b) => a[other] - b[other])
        const on = cells.map((q) => q[other])
        if (!on.length) continue
        const seg = (a: number, b: number) => segs.push(horizontal ? [a, at(i), b, at(i)] : [at(i), a, at(i), b])
        // Streets that reach the edge of the picker carry on off-map.
        if (on[0] === 0) seg(at(0) - STUB, at(0))
        if (on[on.length - 1] === span - 1) seg(at(span - 1), at(span - 1) + STUB)
        // Join consecutive junctions on this street wherever the real road runs between them,
        // so a street that passes a row with no junction on it stays continuous.
        for (let k = 1; k < on.length; k++) if (on[k] === on[k - 1] + 1 || roadBetween(scene, cells[k - 1], cells[k])) seg(at(on[k - 1]), at(on[k]))
      }
    }
    lines('r', 'c', grid.rows, grid.cols, true)
    lines('c', 'r', grid.cols, grid.rows, false)
    return segs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid, scene])

  const move = (from: (typeof grid.cells)[number], key: string) => {
    const [dr, dc] = DIR_STEP[key]
    // Nearest junction ahead in that direction, preferring one on the same street.
    const cost = (q: typeof from) => (dr ? Math.abs(q.r - from.r) + 2 * Math.abs(q.c - from.c) : Math.abs(q.c - from.c) + 2 * Math.abs(q.r - from.r))
    const ahead = grid.cells.filter((q) => (dr ? (q.r - from.r) * dr > 0 : (q.c - from.c) * dc > 0)).sort((a, b) => cost(a) - cost(b))[0]
    if (!ahead) return
    setCursor(ahead.id)
    nodes.current.get(ahead.id)?.focus()
  }

  const shownId = hover ?? (focused ? cursor : null) ?? current
  const shown = grid.cells.find((q) => q.id === shownId)

  return (
    <div className="glass anim-in absolute left-0 top-12 w-max rounded-xl px-3 pb-2.5 pt-2.5">
      <div className="mb-2 flex h-5 items-center gap-1 text-[12.5px] leading-none" aria-live="polite">
        {shown ? (
          <>
            {shown.id === current && <span className="mr-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden />}
            <span className="font-semibold tracking-[-0.01em] text-ink">{shortName(shown.name)}</span>
            <span className="text-ink-3">· {CONTROL_LABEL[shown.control] ?? shown.control}</span>
          </>
        ) : (
          <span className="text-ink-3">Pick an intersection to focus</span>
        )}
      </div>

      <div className="relative" style={{ width: LABEL_W + W + (stagger ? PITCH / 2 : 0), height: headH + H }}>
        {Array.from({ length: grid.cols }, (_, c) => {
          const full = colName(c)
          return (
            <span
              key={c}
              title={full}
              className="label-caps absolute text-ink-2! -translate-x-1/2 truncate whitespace-nowrap text-center leading-none"
              style={{ left: LABEL_W + at(c), top: stagger && c % 2 ? 16 : 0, maxWidth: stagger ? PITCH * 2 - 8 : PITCH - 2 }}
            >
              {firstName(full)}
            </span>
          )
        })}
        {Array.from({ length: grid.rows }, (_, r) => {
          const full = rowName(r)
          return (
            <span key={r} title={full} className="label-caps absolute text-ink-2! left-0 -translate-y-1/2 truncate whitespace-nowrap leading-none" style={{ top: headH + at(r), maxWidth: LABEL_W - 10 }}>
              {firstName(full)}
            </span>
          )
        })}

        <svg className="pointer-events-none absolute overflow-visible" style={{ left: LABEL_W, top: headH }} width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden>
          {segments.map(([x1, y1, x2, y2], i) => (
            <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgb(255 255 255 / 0.6)" strokeWidth={2.5} strokeLinecap="round" />
          ))}
        </svg>

        <div role="group" aria-label="Intersections, laid out as on the map" className="absolute" style={{ left: LABEL_W, top: headH, width: W, height: H }}>
          {grid.cells.map((cell) => {
            const active = cell.id === current
            return (
              <button
                key={cell.id}
                ref={(el) => {
                  if (el) nodes.current.set(cell.id, el)
                  else nodes.current.delete(cell.id)
                }}
                tabIndex={cell.id === cursor ? 0 : -1}
                aria-label={`${shortName(cell.name)}, ${(CONTROL_LABEL[cell.control] ?? cell.control).toLowerCase()}${active ? ', selected' : ''}`}
                aria-current={active || undefined}
                onClick={() => onPick(cell.id)}
                onPointerEnter={() => setHover(cell.id)}
                onPointerLeave={() => setHover((h) => (h === cell.id ? null : h))}
                onFocus={() => {
                  setCursor(cell.id)
                  setFocused(true)
                }}
                onBlur={() => setFocused(false)}
                onKeyDown={(e) => {
                  if (!(e.key in DIR_STEP)) return
                  // keep the global arrow shortcut from also moving the camera
                  e.preventDefault()
                  e.stopPropagation()
                  move(cell, e.key)
                }}
                className={cn(
                  'absolute flex h-8 w-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full outline-none transition-colors duration-100',
                  'focus-visible:ring-2 focus-visible:ring-accent/55 focus-visible:ring-offset-1 focus-visible:ring-offset-white',
                  active ? 'bg-accent-soft text-accent ring-2 ring-accent' : 'text-ink-2 hover:bg-black/[0.05] hover:text-ink focus-visible:bg-black/[0.05] focus-visible:text-ink',
                )}
                style={{ left: at(cell.c), top: at(cell.r) }}
              >
                <ControlGlyph control={cell.control} />
              </button>
            )
          })}
        </div>
      </div>

      <div className="mt-1.5 flex items-center gap-3 text-[11px] leading-none text-ink-3">
        {(
          [
            ['traffic_light', 'Signal'],
            ['priority_stop', 'Stop'],
            ['allway_stop', 'All-way'],
          ] as const
        ).map(([k, l]) => (
          <span key={k} className="flex items-center gap-0.5">
            <ControlGlyph control={k} size={14} />
            {l}
          </span>
        ))}
        <span className="ml-auto flex items-center gap-1 pl-4">
          <span className="kbd">←</span>
          <span className="kbd">→</span>
          <span className="kbd">↑</span>
          <span className="kbd">↓</span>
          <span className="ml-0.5">to move</span>
        </span>
      </div>
    </div>
  )
}
