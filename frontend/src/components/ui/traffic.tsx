// Traffic-control display parts, drawn from patterns on 21st.dev and rebuilt in
// this app's tokens with CSS only (no new dependencies):
// - SevenSegment: "7 segment number", as on a pedestrian countdown signal
// - SlidingNumber: "Sliding Number", digits that roll when a value changes
// - MessageSign: "Marquee", as a highway variable-message sign in LED dots
// - SignalLamp: a three-lamp signal head for a status, always with its label

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from '../../lib/format'

// ---- seven-segment ---------------------------------------------------------
// One digit lives in a 12 × 20 box. Segments are hexagons so joints read as LED.
const H = (y: number, x1: number, x2: number) => `${x1},${y} ${x1 + 1},${y - 1} ${x2 - 1},${y - 1} ${x2},${y} ${x2 - 1},${y + 1} ${x1 + 1},${y + 1}`
const V = (x: number, y1: number, y2: number) => `${x},${y1} ${x + 1},${y1 + 1} ${x + 1},${y2 - 1} ${x},${y2} ${x - 1},${y2 - 1} ${x - 1},${y1 + 1}`
const SEG = {
  a: H(1.6, 2.2, 9.8),
  b: V(10.4, 2.2, 9.6),
  c: V(10.4, 10.4, 17.8),
  d: H(18.4, 2.2, 9.8),
  e: V(1.6, 10.4, 17.8),
  f: V(1.6, 2.2, 9.6),
  g: H(10, 2.2, 9.8),
}
type Seg = keyof typeof SEG
const DIGITS: Record<string, Seg[]> = {
  '0': ['a', 'b', 'c', 'd', 'e', 'f'],
  '1': ['b', 'c'],
  '2': ['a', 'b', 'g', 'e', 'd'],
  '3': ['a', 'b', 'g', 'c', 'd'],
  '4': ['f', 'g', 'b', 'c'],
  '5': ['a', 'f', 'g', 'c', 'd'],
  '6': ['a', 'f', 'g', 'e', 'd', 'c'],
  '7': ['a', 'b', 'c'],
  '8': ['a', 'b', 'c', 'd', 'e', 'f', 'g'],
  '9': ['a', 'b', 'c', 'd', 'f', 'g'],
  '-': ['g'],
  ' ': [],
}
const ALL = Object.keys(SEG) as Seg[]

/** Digits as lit LED segments, unlit ones faintly visible like a real display. */
export function SevenSegment({ value, height = 22, on = '#ffb547', off = 'rgb(255 255 255 / 0.06)', className }: { value: string; height?: number; on?: string; off?: string; className?: string }) {
  const chars = [...value]
  const w = chars.reduce((s, c) => s + (c === ':' || c === '.' ? 5 : 13), 0)
  let x = 0
  return (
    <svg role="img" aria-label={value} width={(w / 20) * height} height={height} viewBox={`0 0 ${w} 20`} className={cn('shrink-0 overflow-visible', className)} style={{ filter: `drop-shadow(0 0 3px ${on}66)` }}>
      <g transform="skewX(-6) translate(1.6 0)">
        {chars.map((c, i) => {
          const at = x
          if (c === ':' || c === '.') {
            x += 5
            return (
              <g key={i} transform={`translate(${at} 0)`}>
                {c === ':' && <rect x={1} y={5.5} width={2} height={2} rx={0.4} fill={on} />}
                <rect x={1} y={c === ':' ? 12.5 : 17.4} width={2} height={2} rx={0.4} fill={on} />
              </g>
            )
          }
          x += 13
          const lit = new Set(DIGITS[c] ?? [])
          return (
            <g key={i} transform={`translate(${at} 0)`}>
              {ALL.map((s) => (
                <polygon key={s} points={SEG[s]} fill={lit.has(s) ? on : off} />
              ))}
            </g>
          )
        })}
      </g>
    </svg>
  )
}

// ---- sliding number --------------------------------------------------------
/** A formatted value whose digits roll to their new place when it changes. */
export function SlidingNumber({ value, className }: { value: string; className?: string }) {
  const chars = [...value]
  return (
    <span className={cn('tnum inline-flex items-baseline', className)} aria-label={value}>
      {chars.map((c, i) => {
        // key from the right so units stay put while the value grows
        const key = chars.length - i
        if (c < '0' || c > '9') return <span key={`s${key}`} aria-hidden>{c}</span>
        const d = c.charCodeAt(0) - 48
        return (
          <span key={`d${key}`} aria-hidden className="relative inline-block h-[1lh] overflow-hidden">
            <span className="invisible">0</span>
            <span
              className="absolute inset-x-0 top-0 flex flex-col transition-transform duration-500 ease-[var(--ease-out-quint)] motion-reduce:transition-none"
              style={{ transform: `translateY(${-d * 10}%)` }}
            >
              {'0123456789'.split('').map((n) => (
                <span key={n} className="block h-[1lh]">
                  {n}
                </span>
              ))}
            </span>
          </span>
        )
      })}
    </span>
  )
}

// ---- message sign ----------------------------------------------------------
/**
 * A highway variable-message sign. Text is printed in amber LED dots; when the
 * messages are wider than the sign they scroll, pausing on hover, and under
 * reduced motion they cycle one at a time instead.
 */
export function MessageSign({ messages, className, lead }: { messages: string[]; className?: string; lead?: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const [scroll, setScroll] = useState(false)
  const [i, setI] = useState(0)
  const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const text = messages.join('   ◆   ')
  useEffect(() => {
    const measure = () => setScroll(!!box.current && !!track.current && track.current.scrollWidth / 2 > box.current.clientWidth)
    measure()
    const ro = new ResizeObserver(measure)
    if (box.current) ro.observe(box.current)
    return () => ro.disconnect()
  }, [text])
  useEffect(() => {
    if (!reduced || messages.length < 2) return
    const iv = setInterval(() => setI((x) => (x + 1) % messages.length), 4000)
    return () => clearInterval(iv)
  }, [reduced, messages.length])
  return (
    <div className={cn('vms flex min-w-0 items-center gap-3 px-3.5', className)} role="status" aria-label={messages.join('. ')}>
      {lead}
      <div ref={box} className="vms-window min-w-0 flex-1 overflow-hidden">
        {reduced ? (
          <div className="vms-led truncate">{messages[i % messages.length]}</div>
        ) : (
          <div ref={track} className={cn('vms-led flex w-max', scroll && 'vms-scroll')} style={scroll ? { animationDuration: `${Math.max(14, text.length * 0.22)}s` } : undefined}>
            <span className="pr-12">{text}</span>
            <span className={cn('pr-12', !scroll && 'hidden')} aria-hidden>
              {text}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

// ---- signal lamp -----------------------------------------------------------
export type Aspect = 'go' | 'caution' | 'stop'
const LAMP: Record<Aspect, { c: string; i: number }> = { stop: { c: '#ff5a4f', i: 0 }, caution: { c: '#ffb547', i: 1 }, go: { c: '#3ddc84', i: 2 } }

/** The lamps alone: red, amber, green in a dark housing, one lit. Position carries the state too. */
export function LampHead({ aspect, size = 6, className }: { aspect: Aspect; size?: number; className?: string }) {
  const lit = LAMP[aspect]
  return (
    <span aria-hidden className={cn('inline-flex shrink-0 items-center rounded-full bg-[#0c1322] shadow-[inset_0_0_0_1px_rgb(255_255_255/0.08)]', className)} style={{ gap: Math.max(2, size / 2), padding: `${Math.max(2, size * 0.6)}px ${Math.max(3, size * 0.8)}px` }}>
      {(['stop', 'caution', 'go'] as Aspect[]).map((a) => {
        const on = LAMP[a].i === lit.i
        return (
          <span
            key={a}
            className="rounded-full transition-[background-color,box-shadow] duration-300"
            style={{ width: size, height: size, background: on ? LAMP[a].c : 'rgb(255 255 255 / 0.13)', boxShadow: on ? `0 0 ${size}px ${LAMP[a].c}` : 'none' }}
          />
        )
      })}
    </span>
  )
}

/** A tiny horizontal signal head; the lit lamp and the label carry the state together. */
export function SignalLamp({ aspect, label, className }: { aspect: Aspect; label: ReactNode; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <LampHead aspect={aspect} className={aspect === 'caution' ? 'pulse' : undefined} />
      {label}
    </span>
  )
}

// ---- queue lanes -----------------------------------------------------------
export interface QueueLane {
  label: string
  color: string
  /** Vehicles in the queue; one block each. */
  count: number
  strong?: boolean
}

/**
 * Two-lane approaches stopped at a red signal, one block per queued vehicle.
 * Shorter queues show the vehicles they avoided as dashed outlines, so the
 * difference reads as cars, not as a percentage. Cars roll up on mount.
 */
export function QueueLanes({ lanes, className }: { lanes: QueueLane[]; className?: string }) {
  const [shown, setShown] = useState(false)
  const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  useEffect(() => {
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)))
    return () => cancelAnimationFrame(id)
  }, [])
  const max = Math.max(...lanes.map((l) => l.count))
  const W = 600
  const PITCH = 7.2
  const CAR = 6
  const STOP = W - 34
  const ROWS = [4, 14.5]
  const H = 24
  const pos = (i: number) => ({ x: STOP - 3 - Math.floor(i / 2) * PITCH - CAR, y: ROWS[i % 2] })
  return (
    <div className={cn('flex flex-col gap-2.5', className)}>
      {lanes.map((lane, li) => (
        <div key={lane.label} className="flex items-center gap-3">
          <span className={cn('flex w-[104px] shrink-0 items-center gap-2 text-[13px]', lane.strong ? 'font-medium text-ink' : 'text-ink-3')}>
            <span className="h-2 w-2 shrink-0 rounded-[2px]" style={{ background: lane.color }} />
            {lane.label}
          </span>
          <svg viewBox={`0 0 ${W} ${H + 2}`} className="block h-auto min-w-0 flex-1" role="img" aria-label={`${lane.label}: ${lane.count} vehicles queued`}>
            {/* road surface, edge lines and the dashed line between the two lanes */}
            <rect x={0} y={1} width={STOP} height={H} rx={3} fill="rgb(255 255 255 / 0.035)" />
            <line x1={0} x2={STOP} y1={1} y2={1} stroke="rgb(255 255 255 / 0.14)" />
            <line x1={0} x2={STOP} y1={H + 1} y2={H + 1} stroke="rgb(255 255 255 / 0.14)" />
            <line x1={0} x2={STOP} y1={13} y2={13} stroke="rgb(255 255 255 / 0.18)" strokeDasharray="6 6" />
            {/* stop line and signal head showing red */}
            <rect x={STOP} y={1} width={2.5} height={H} fill="rgb(255 255 255 / 0.75)" />
            <rect x={W - 24} y={1} width={11} height={H} rx={3} fill="#05080f" stroke="rgb(255 255 255 / 0.1)" />
            <circle cx={W - 18.5} cy={6.5} r={2.6} fill="#ff5a4f" style={{ filter: 'drop-shadow(0 0 3px #ff5a4f)' }} />
            <circle cx={W - 18.5} cy={13} r={2.6} fill="rgb(255 255 255 / 0.12)" />
            <circle cx={W - 18.5} cy={19.5} r={2.6} fill="rgb(255 255 255 / 0.12)" />
            {Array.from({ length: max }, (_, i) => {
              const { x, y } = pos(i)
              if (i >= lane.count)
                return (
                  <rect
                    key={i}
                    x={x + 0.4}
                    y={y + 0.4}
                    width={CAR - 0.8}
                    height={4.7}
                    rx={1.3}
                    fill="none"
                    stroke="rgb(255 255 255 / 0.4)"
                    strokeWidth={0.8}
                    strokeDasharray="1.6 1.2"
                    style={{ opacity: shown ? 1 : 0, transition: reduced ? undefined : `opacity 400ms ease-out ${900 + li * 120}ms` }}
                  />
                )
              const delay = Math.floor(i / 2) * 9 + li * 120
              return (
                <rect
                  key={i}
                  x={x}
                  y={y}
                  width={CAR}
                  height={5.5}
                  rx={1.4}
                  fill={lane.color}
                  style={{
                    transform: shown || reduced ? 'none' : 'translateX(-48px)',
                    opacity: shown || reduced ? 1 : 0,
                    transition: reduced ? undefined : `transform 620ms cubic-bezier(0.2, 0, 0, 1) ${delay}ms, opacity 240ms ease-out ${delay}ms`,
                  }}
                />
              )
            })}
          </svg>
          <span className={cn('tnum w-10 shrink-0 text-right', lane.strong ? 'font-semibold text-ink' : 'text-ink-2')}>{lane.count}</span>
        </div>
      ))}
    </div>
  )
}
