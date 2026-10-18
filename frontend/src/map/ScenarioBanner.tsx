// What the scenario is doing right now, on the map: the weather in force and
// the current or next timeline event, with a button that flies to its scene.

import { CloudDrizzle, CloudFog, CloudRain, Crosshair, Snowflake, type LucideIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Tip } from '../components/ui'
import { frames } from '../lib/frames'
import { WEATHER_LABEL, clock } from '../lib/format'
import { effectsLive, useStore } from '../lib/store'
import type { Marker } from '../lib/types'
import { mapApi } from './MapView'
import { TONE, focusTarget, placeOverlays } from './overlays'
import type { Scene } from './scene'
import { useWeather } from './WeatherLayer'

const WEATHER_ICON: Record<string, LucideIcon> = { light_rain: CloudDrizzle, heavy_rain: CloudRain, snow: Snowflake, fog: CloudFog }
const MARK_TONE: Record<string, string> = { incident: TONE.incident, emergency: TONE.emergency, demand: TONE.demand, transit: TONE.transit }
/** A timeline event stays "now" for this long after it happens. */
const NOW_S = 45

function useSimTime(): number {
  const [t, setT] = useState(0)
  useEffect(() => {
    const iv = setInterval(() => setT(isFinite(frames.renderT) ? Math.floor(frames.renderT) : 0), 250)
    return () => clearInterval(iv)
  }, [])
  return t
}

/** The event that just happened, else the next one, else the last one. */
function currentMarker(markers: Marker[], t: number): { m: Marker; when: 'now' | 'next' | 'past' } | null {
  const passed = markers.filter((m) => m.t <= t)
  const last = passed[passed.length - 1]
  if (last && t - last.t <= NOW_S) return { m: last, when: 'now' }
  const next = markers.find((m) => m.t > t)
  if (next) return { m: next, when: 'next' }
  return last ? { m: last, when: 'past' } : null
}

export function ScenarioBanner({ scene }: { scene: Scene }) {
  const run = useStore((s) => s.run)
  const live = useStore(effectsLive)
  const cond = useWeather()
  const t = useSimTime()
  const weather = run?.overlays?.find((o) => o.kind === 'weather')
  const placed = useMemo(() => (run && run.network === scene.id ? placeOverlays(scene, run.overlays) : []), [run, scene])
  if (!run || !live) return null
  const cur = currentMarker(run.markers, t)
  const target = focusTarget(placed, t)
  const WIcon = cond ? WEATHER_ICON[cond] : undefined
  if (!cur && !WIcon && !target) return null
  return (
    <div className="pointer-events-none absolute bottom-[78px] left-1/2 z-10 flex max-w-[calc(100%-24px)] -translate-x-1/2 justify-center" data-testid="scenario-banner">
      <div className="glass pointer-events-auto flex h-9 min-w-0 items-center gap-2.5 rounded-xl pl-3 pr-1.5 text-[12px]">
        {WIcon && cond && (
          <Tip side="top" label={weather?.detail ?? 'Drivers slow down and keep longer gaps'}>
            <span className="flex shrink-0 items-center gap-1.5 font-semibold text-[#0369A1]">
              <WIcon size={15} strokeWidth={2} />
              {WEATHER_LABEL[cond] ?? cond}
            </span>
          </Tip>
        )}
        {WIcon && cur && <span className="h-4 w-px shrink-0 bg-black/[0.1]" />}
        {cur && (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className={cur.when === 'now' ? 'pulse h-2 w-2 shrink-0 rounded-full' : 'h-2 w-2 shrink-0 rounded-full'} style={{ background: MARK_TONE[cur.m.kind] ?? '#475467' }} />
            <span className="truncate font-medium text-ink">{cur.m.label}</span>
            <span className="tnum shrink-0 text-ink-3">{cur.when === 'now' ? 'now' : cur.when === 'next' ? `in ${clock(cur.m.t - t)}` : `at ${clock(cur.m.t)}`}</span>
          </span>
        )}
        {target ? (
          <Tip side="top" label={target.o.detail ? `${target.o.label} · ${target.o.detail}` : target.o.label}>
            <button
              aria-label={`Show ${target.o.label} on the map`}
              onClick={() => {
                useStore.getState().set({ follow: false })
                mapApi.current?.focus(target.x, target.y, 6)
              }}
              className="flex h-7 shrink-0 items-center gap-1 rounded-lg bg-black/[0.05] px-2 text-[11.5px] font-medium text-ink-2 hover:bg-black/[0.09] hover:text-ink"
            >
              <Crosshair size={12} /> Show
            </button>
          </Tip>
        ) : (
          <span className="w-1.5" />
        )}
      </div>
    </div>
  )
}
