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
import { CONTROL_LABEL, junctionGrid, readingOrder, shortName, type NavJunction } from './nav'
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

function JunctionPicker({ junctions, current, onPick }: { junctions: NavJunction[]; current: string | null; onPick: (id: string) => void }) {
  const grid = useMemo(() => junctionGrid(junctions), [junctions])
  const colName = (c: number) => grid.cells.find((q) => q.c === c)?.name.split('&')[0].trim() ?? ''
  const rowName = (r: number) => grid.cells.find((q) => q.r === r)?.name.split('&')[1]?.trim() ?? ''
  const short = (s: string) => s.replace(/\s+(Street|Avenue|Road|Boulevard)$/, '')
  return (
    <div className="glass anim-in absolute left-0 top-12 w-max rounded-xl p-3">
      <div className="mb-2 flex items-baseline justify-between gap-6">
        <span className="label-caps">Intersections</span>
        <span className="text-[11px] text-ink-3">Laid out as on the map</span>
      </div>
      <div className="grid gap-1" style={{ gridTemplateColumns: `64px repeat(${grid.cols}, 92px)` }}>
        <span />
        {Array.from({ length: grid.cols }, (_, c) => (
          <span key={c} className="truncate pb-0.5 text-center text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-3">
            {short(colName(c))}
          </span>
        ))}
        {Array.from({ length: grid.rows }, (_, r) => (
          <Row key={r} label={short(rowName(r))}>
            {Array.from({ length: grid.cols }, (_, c) => {
              const cell = grid.cells.find((q) => q.r === r && q.c === c)
              if (!cell) return <span key={c} />
              const active = cell.id === current
              const signal = cell.control === 'traffic_light'
              return (
                <button
                  key={c}
                  onClick={() => onPick(cell.id)}
                  title={`${cell.name} · ${CONTROL_LABEL[cell.control] ?? cell.control}`}
                  className={cn(
                    'flex h-9 items-center gap-1.5 rounded-lg border px-2 text-left text-[11.5px] font-medium transition-colors',
                    active ? 'border-ink bg-ink text-white' : 'border-black/[0.07] bg-white/70 text-ink-2 hover:border-black/15 hover:bg-white hover:text-ink',
                  )}
                >
                  <span className={cn('h-2 w-2 shrink-0 rounded-full', signal ? (active ? 'bg-[#4ADE80]' : 'bg-good') : active ? 'bg-[#FCA5A5]' : 'bg-bad')} />
                  <span className="truncate">{signal ? 'Signal' : cell.control === 'allway_stop' ? 'All-way' : 'Stop'}</span>
                </button>
              )
            })}
          </Row>
        ))}
      </div>
      <div className="mt-2.5 flex items-center gap-3 border-t border-black/[0.06] pt-2 text-[11px] text-ink-3">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-good" /> Signalised
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-bad" /> Stop-controlled
        </span>
        <span className="ml-auto">Arrow keys move between intersections</span>
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <span className="flex items-center truncate pr-1 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-3">{label}</span>
      {children}
    </>
  )
}
