// Generate & Run progress. Each step reflects a real backend phase:
//   1. POST /api/runs pending  — the server stops the previous run and draws the seeded scenario
//   2. status "warming"        — SUMO runs the warm-up as fast as it can (status.t counts up to 0)
//   3. first frame             — live playback begins
// The indeterminate bar animates with a CSS transform, which the browser runs
// off the main thread, so it keeps moving even while JavaScript is busy.

import { Check, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../lib/format'
import { frames } from '../lib/frames'
import { useStore } from '../lib/store'

function useNow(active: boolean, ms = 100) {
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    if (!active) return
    const iv = setInterval(() => setNow(performance.now()), ms)
    return () => clearInterval(iv)
  }, [active, ms])
  return now
}

const secs = (ms: number) => `${(Math.max(0, ms) / 1000).toFixed(1)} s`

function Step({ state, title, detail, children }: { state: 'done' | 'active' | 'todo'; title: string; detail?: string; children?: React.ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <span
        className={cn(
          'mt-[1px] flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border',
          state === 'done' && 'border-transparent bg-good text-white',
          state === 'active' && 'border-accent/40 bg-accent-soft text-accent',
          state === 'todo' && 'border-border bg-surface text-ink-3',
        )}
      >
        {state === 'done' ? <Check size={11} strokeWidth={3} /> : state === 'active' ? <Loader2 size={11} className="animate-spin" /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-3">
          <span className={cn('text-[12.5px] font-medium', state === 'todo' ? 'text-ink-3' : 'text-ink')}>{title}</span>
          {detail && <span className="tnum shrink-0 text-[11px] text-ink-3">{detail}</span>}
        </span>
        {children}
      </span>
    </li>
  )
}

type Launch = { t0: number; posted: number | null; scenario: string }

export function LaunchProgress() {
  const launch = useStore((s) => s.launch)
  // keyed by launch so each Generate & Run starts with fresh state
  return launch ? <LaunchCard key={launch.t0} launch={launch} /> : null
}

function LaunchCard({ launch }: { launch: Launch }) {
  const status = useStore((s) => s.status)
  const set = useStore((s) => s.set)
  const state = status.state
  const [live, setLive] = useState<number | null>(null)
  const active = live === null
  const now = useNow(active)

  // first rendered frame closes the sequence; the card lingers briefly as confirmation
  useEffect(() => {
    if (state !== 'running') return
    let raf = 0
    const check = () => {
      if (frames.frames.length) setLive(performance.now())
      else raf = requestAnimationFrame(check)
    }
    raf = requestAnimationFrame(check)
    return () => cancelAnimationFrame(raf)
  }, [state])
  useEffect(() => {
    if (live === null) return
    const t = setTimeout(() => set({ launch: null }), 900)
    return () => clearTimeout(t)
  }, [live, set])
  useEffect(() => {
    if (state === 'error' || state === 'idle') set({ launch: null })
  }, [state, set])

  const posted = launch.posted
  // the WebSocket "warming" status can arrive before the HTTP response does
  const built = posted !== null || state === 'warming' || state === 'running'
  const warm = status.warmup ?? 90
  const warmFrac = state === 'warming' ? Math.max(0, Math.min(1, 1 + (status.t ?? -warm) / warm)) : state === 'running' ? 1 : 0
  const warmDone = state === 'running'
  const end = live ?? now
  return (
    <div className="glass anim-in absolute left-1/2 top-[64px] z-20 w-[340px] -translate-x-1/2 rounded-xl px-4 py-3" role="status" aria-live="polite">
      <div className="mb-2.5 flex items-baseline justify-between">
        <span className="text-[13px] font-semibold tracking-[-0.01em] text-ink">{live ? 'Live' : 'Generating'} · {launch.scenario}</span>
        <span className="tnum text-[11.5px] text-ink-3">{secs(end - launch.t0)}</span>
      </div>
      <ol className="space-y-2">
        <Step state={built ? 'done' : 'active'} title="Building scenario" detail={posted ? secs(posted - launch.t0) : built ? undefined : secs(now - launch.t0)}>
          {!built && (
            <span className="mt-1 block text-[11px] leading-snug text-ink-3">Server is stopping the previous run and drawing the seeded variation</span>
          )}
        </Step>
        <Step state={warmDone ? 'done' : built ? 'active' : 'todo'} title="SUMO warm-up" detail={built ? `${Math.round(warmFrac * 100)}%` : undefined}>
          {built && !warmDone && (
            <span className="mt-1.5 block h-[3px] overflow-hidden rounded-full bg-muted">
              {state === 'warming' ? (
                <span className="block h-full rounded-full bg-accent transition-[width] duration-200" style={{ width: `${warmFrac * 100}%` }} />
              ) : (
                <span className="indeterminate block h-full w-1/3 rounded-full bg-accent" />
              )}
            </span>
          )}
          {built && !warmDone && <span className="mt-1 block text-[11px] text-ink-3">Populating {warm} s of traffic before the clock starts</span>}
        </Step>
        <Step state={live ? 'done' : warmDone ? 'active' : 'todo'} title="Streaming live traffic" />
      </ol>
      {!built && (
        <span className="mt-2.5 block h-[3px] overflow-hidden rounded-full bg-muted">
          <span className="indeterminate block h-full w-1/3 rounded-full bg-accent" />
        </span>
      )}
    </div>
  )
}
