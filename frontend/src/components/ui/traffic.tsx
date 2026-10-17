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
