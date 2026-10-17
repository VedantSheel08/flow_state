import { CloudRain, Loader2, WifiOff } from 'lucide-react'
import { useEffect } from 'react'
import { Header } from './components/Header'
import { RightPanel } from './components/RightPanel'
import { RunBar } from './components/RunBar'
import { ICONS, ScenarioRail } from './components/ScenarioRail'
import { TooltipProvider } from './components/ui'
import { control, newVariation, reproduce } from './lib/actions'
import { api } from './lib/api'
import { cn } from './lib/format'
import { paramsFor, useStore } from './lib/store'
import { connect } from './lib/ws'
import { MapView, mapApi } from './map/MapView'
import { AnalyticsView } from './views/AnalyticsView'
import { RunsView } from './views/RunsView'

function useBootstrap() {
  const conn = useStore((s) => s.connection)
  useEffect(() => {
    connect()
  }, [])
  useEffect(() => {
    if (conn !== 'open') return
    const st = useStore.getState()
    api.health().then((h) => st.set({ health: h })).catch(() => {})
    api
      .scenarios()
      .then((sc) => useStore.getState().set({ scenarios: sc }))
      .catch((e) => st.toast('error', e.message))
  }, [conn])
}

function useNetwork(): string {
  const run = useStore((s) => s.run)
  const scenarioId = useStore((s) => s.scenarioId)
  const scenarios = useStore((s) => s.scenarios)
  const drafts = useStore((s) => s.drafts)
  const networks = useStore((s) => s.networks)
  const conn = useStore((s) => s.connection)
  const spec = scenarios.find((s) => s.id === scenarioId)
  const wanted = run?.network ?? ((spec && (paramsFor(spec, drafts[spec.id]).network as string)) || 'district')
  useEffect(() => {
    if (networks[wanted] || conn !== 'open') return
    api
      .network(wanted)
      .then((n) => useStore.getState().set({ networks: { ...useStore.getState().networks, [n.id]: n } }))
      .catch((e) => useStore.getState().toast('error', `Could not load network: ${e.message}`))
  }, [wanted, networks, conn])
  return wanted
}

function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      const st = useStore.getState()
      if (e.key === ' ') {
        e.preventDefault()
        if (st.status.state === 'running') control(st.status.paused ? 'resume' : 'pause')
      } else if (e.key === 'r' || e.key === 'R') reproduce()
      else if (e.key === 'n' || e.key === 'N') newVariation()
      else if (e.key === 'f' || e.key === 'F') mapApi.current?.fit()
      else if (e.key === 'h' || e.key === 'H') mapApi.current?.home()
      else if (e.key === '+' || e.key === '=') mapApi.current?.zoom(1.5)
      else if (e.key === '-') mapApi.current?.zoom(1 / 1.5)
      else if (e.key === 'Escape') st.set({ selection: null, follow: false, pick: null })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}

function MapStage() {
  const netId = useNetwork()
  const net = useStore((s) => s.networks[netId])
  const status = useStore((s) => s.status)
  const conn = useStore((s) => s.connection)
  const run = useStore((s) => s.run)
  const pick = useStore((s) => s.pick)
  return (
    <main className="relative min-w-0 flex-1">
      {net ? (
        <MapView net={net} />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center bg-[#E8EAE6] text-[12.5px] text-ink-3">
          {conn === 'open' ? (
            <span className="flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" /> Loading street network…
            </span>
          ) : null}
        </div>
      )}
      {pick && (
        <div className="anim-in absolute left-1/2 top-3 -translate-x-1/2 rounded-md bg-ink px-3 py-1.5 text-[12px] font-medium text-white shadow-float">
          Click a street segment to place the event · Esc to cancel
        </div>
      )}
      {(status.state === 'warming' || status.state === 'starting') && (
        <div className="anim-in absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-2.5 rounded-lg border border-border bg-surface px-3.5 py-2 shadow-float">
          <Loader2 size={14} className="animate-spin text-accent" />
          <div>
            <div className="text-[12.5px] font-medium text-ink">{status.state === 'starting' ? 'Generating scenario…' : 'Warming up SUMO'}</div>
            <div className="text-[11px] text-ink-3">
              {status.state === 'starting' ? 'Drawing a seeded variation and starting SUMO' : `Populating the network · ${Math.round(Math.max(0, 1 + (status.t ?? -90) / (status.warmup ?? 90)) * 100)}%`}
            </div>
          </div>
        </div>
      )}
      {conn === 'closed' && (
        <div className="anim-in absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-2.5 rounded-lg border border-bad/20 bg-surface px-3.5 py-2 shadow-float">
          <WifiOff size={14} className="text-bad" />
          <div>
            <div className="text-[12.5px] font-medium text-ink">Simulation server unreachable</div>
            <div className="text-[11px] text-ink-3">Reconnecting automatically. Start it with run.ps1 or `python -m flowstate.cli serve`.</div>
          </div>
        </div>
      )}
      {!run && conn === 'open' && net && status.state === 'idle' && (
        <div className="anim-in pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-lg border border-border bg-surface/95 px-4 py-2.5 text-center shadow-float">
          <div className="text-[13px] font-medium text-ink">{net.title}</div>
          <div className="text-[11.5px] text-ink-3">Pick a scenario and press Generate &amp; Run. Drag to pan, scroll to zoom.</div>
        </div>
      )}
      <RunChip />
      <RunBar />
    </main>
  )
}

const WEATHER_LABEL: Record<string, string> = { light_rain: 'Light rain', heavy_rain: 'Heavy rain', snow: 'Snow', fog: 'Dense fog', clear: 'Clear' }

function RunChip() {
  const run = useStore((s) => s.run)
  const scen = useStore((s) => s.stats?.scenario)
  const pick = useStore((s) => s.pick)
  if (!run || pick) return null
  const Icon = ICONS[run.scenario]
  const weather = scen?.weather as string | undefined
  return (
    <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded-lg border border-border bg-surface/95 px-3 py-1.5 shadow-panel">
      {Icon && <Icon size={14} className="text-ink-2" />}
      <span className="text-[12.5px] font-semibold text-ink">{run.title}</span>
      <span className="tnum text-[12px] text-ink-3">seed {run.seed}</span>
      {weather && weather !== 'clear' && (
        <span className="flex items-center gap-1 border-l border-border pl-2 text-[12px] font-medium text-ink-2">
          <CloudRain size={13} /> {WEATHER_LABEL[weather] ?? weather}
        </span>
      )}
      {typeof scen?.pursuit === 'string' && (
        <span className="border-l border-border pl-2 text-[12px] font-medium capitalize text-ink-2">{scen.pursuit}</span>
      )}
    </div>
  )
}

function Toasts() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="pointer-events-none fixed bottom-20 right-[356px] z-50 flex flex-col items-end gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            'anim-in pointer-events-auto max-w-[360px] rounded-md border bg-surface px-3 py-2 text-[12.5px] shadow-float',
            t.tone === 'error' ? 'border-bad/30 text-bad' : 'border-border text-ink',
          )}
        >
          {t.text}
        </div>
      ))}
    </div>
  )
}

export default function App() {
  useBootstrap()
  useShortcuts()
  const view = useStore((s) => s.view)
  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0 flex-col">
        <Header />
        <div className="flex min-h-0 flex-1">
          <div className={cn('flex min-h-0 min-w-0 flex-1', view !== 'simulate' && 'hidden')}>
            <ScenarioRail />
            <MapStage />
            <RightPanel />
          </div>
          {view === 'analyze' && <AnalyticsView />}
          {view === 'runs' && <RunsView />}
        </div>
      </div>
      <Toasts />
    </TooltipProvider>
  )
}
