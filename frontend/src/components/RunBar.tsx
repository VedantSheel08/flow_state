import { Pause, Play, Repeat, Shuffle, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { control, newVariation, reproduce, setSpeed } from '../lib/actions'
import { frames } from '../lib/frames'
import { clock, cn } from '../lib/format'
import { useStore } from '../lib/store'
import { Button, Segmented, Tip } from './ui'

const SPEEDS = [1, 2, 4, 8, 16]
const MARK: Record<string, string> = { incident: '#DC2626', emergency: '#2563EB', demand: '#D97706', transit: '#7C3AED', event: '#475467' }

function useSimClock() {
  const [t, setT] = useState(0)
  useEffect(() => {
    let raf = 0
    let last = 0
    const loop = (now: number) => {
      if (now - last > 100) {
        last = now
        setT(isFinite(frames.renderT) ? frames.renderT : 0)
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [])
  return t
}

function useWallElapsed(runId: string | undefined, running: boolean) {
  const [ms, setMs] = useState(0)
  const acc = useRef(0)
  const since = useRef<number | null>(null)
  useEffect(() => {
    acc.current = 0
    since.current = null
    setMs(0)
  }, [runId])
  useEffect(() => {
    if (running) since.current = performance.now()
    else if (since.current != null) {
      acc.current += performance.now() - since.current
      since.current = null
    }
    const iv = setInterval(() => setMs(acc.current + (since.current != null ? performance.now() - since.current : 0)), 250)
    return () => clearInterval(iv)
  }, [running])
  return ms / 1000
}

export function RunBar() {
  const run = useStore((s) => s.run)
  const status = useStore((s) => s.status)
  const t = useSimClock()
  const state = status.state
  const paused = !!status.paused
  const running = state === 'running' && !paused
  const wall = useWallElapsed(run?.id, running)
  const speed = status.speed ?? 1
  const dur = run?.duration ?? null
  const progress = dur ? Math.min(1, Math.max(0, t / dur)) : 0
  const lagging = running && status.achieved != null && status.achieved > 0 && status.achieved < speed * 0.85 && speed > 1

  if (!run) return null
  return (
    <div className="pointer-events-auto absolute inset-x-3 bottom-3 flex h-[52px] items-center gap-3 rounded-lg border border-border bg-surface/95 px-2.5 shadow-float backdrop-blur-[2px]">
      <div className="flex items-center gap-1">
        {state === 'finished' ? (
          <Tip label="Run again with the same seed">
            <Button variant="primary" size="icon" aria-label="Reproduce run" onClick={() => reproduce()}>
              <Repeat size={15} />
            </Button>
          </Tip>
        ) : (
          <Tip label={paused ? 'Resume (Space)' : 'Pause (Space)'}>
            <Button variant="primary" size="icon" aria-label={paused ? 'Resume' : 'Pause'} disabled={state !== 'running'} onClick={() => control(paused ? 'resume' : 'pause')}>
              {paused ? <Play size={15} fill="currentColor" /> : <Pause size={15} fill="currentColor" />}
            </Button>
          </Tip>
        )}
        <Tip label="Restart · same seed (R)">
          <Button variant="ghost" size="icon" aria-label="Restart with the same seed" onClick={() => reproduce()} disabled={state === 'starting'}>
            <Repeat size={15} />
          </Button>
        </Tip>
        <Tip label="New variation · new seed (N)">
          <Button variant="ghost" size="icon" aria-label="New variation" onClick={() => newVariation()} disabled={state === 'starting'}>
            <Shuffle size={15} />
          </Button>
        </Tip>
        <Tip label="Stop simulation">
          <Button variant="ghost" size="icon" aria-label="Stop" onClick={() => control('stop')} disabled={state === 'starting' || state === 'finished'}>
            <Square size={13} />
          </Button>
        </Tip>
      </div>
      <div className="h-7 w-px bg-border" />
      <div className="flex w-[104px] shrink-0 flex-col leading-tight">
        <span className="tnum text-[15px] font-semibold text-ink">
          {clock(Math.max(0, t))}
          <span className="text-[12px] font-normal text-ink-3"> / {clock(dur)}</span>
        </span>
        <span className="tnum whitespace-nowrap text-[10.5px] text-ink-3">sim · {clock(wall)} real</span>
      </div>
      <div className="relative mx-1 h-8 min-w-0 flex-1">
        <div className="absolute inset-x-0 top-[14px] h-[4px] rounded-full bg-muted" />
        <div className="absolute left-0 top-[14px] h-[4px] rounded-full bg-ink transition-[width] duration-150" style={{ width: `${progress * 100}%` }} />
        {dur &&
          run.markers.map((m, i) => (
            <Tip key={i} label={`${clock(m.t)} · ${m.label}`} side="top">
              <span className="absolute top-[9px] h-[14px] w-[10px] -translate-x-1/2 cursor-default" style={{ left: `${(m.t / dur) * 100}%` }}>
                <span className="absolute left-1/2 top-0 h-full w-[2px] -translate-x-1/2 rounded-full" style={{ background: MARK[m.kind] ?? MARK.event, opacity: m.t <= t ? 1 : 0.45 }} />
              </span>
            </Tip>
          ))}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {lagging && (
          <Tip label="Achieved simulation speed (the requested speed exceeds what this machine sustains)">
            <span className="tnum text-[11px] text-warn">≈{status.achieved?.toFixed(1)}×</span>
          </Tip>
        )}
        <Segmented size="sm" value={speed} options={SPEEDS.map((v) => ({ value: v, label: `${v}×` }))} onChange={(v) => setSpeed(v)} />
      </div>
      <div className={cn('flex w-[64px] shrink-0 items-center justify-end gap-1.5 text-[11.5px] font-medium', state === 'error' ? 'text-bad' : 'text-ink-2')}>
        <span className={cn('h-1.5 w-1.5 rounded-full', running ? 'pulse bg-good' : state === 'finished' ? 'bg-ink-3' : state === 'error' ? 'bg-bad' : 'bg-[#f79009]')} />
        {state === 'running' ? (paused ? 'Paused' : 'Live') : state === 'warming' ? 'Warm-up' : state === 'finished' ? 'Complete' : state === 'error' ? 'Error' : 'Starting'}
      </div>
    </div>
  )
}
