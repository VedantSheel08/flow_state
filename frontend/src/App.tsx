import { Loader2, WifiOff } from 'lucide-react'
import { useEffect, type CSSProperties } from 'react'
import { ConfigDialog } from './components/ConfigDialog'
import { Header } from './components/Header'
import { LaunchProgress } from './components/LaunchProgress'
import { RightPanel } from './components/RightPanel'
import { RunBar } from './components/RunBar'
import { ScenarioRail } from './components/ScenarioRail'
import { TooltipProvider } from './components/ui'
import { control, newVariation, reproduce } from './lib/actions'
import { api } from './lib/api'
import { cn } from './lib/format'
import { paramsFor, useStore } from './lib/store'
import { connect } from './lib/ws'
import { MapView, mapApi } from './map/MapView'
import type { Dir } from './map/nav'
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
    // congestion presets are optional: older backends answer 404, which disables them
    api
      .congestionPresets()
      .then((p) => useStore.getState().set({ congestionPresets: Array.isArray(p) ? p : [] }))
      .catch(() => useStore.getState().set({ congestionPresets: [] }))
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
    const ARROWS: Record<string, Dir> = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      const tag = el?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || el?.isContentEditable) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const st = useStore.getState()
      if (e.key === ' ') {
        e.preventDefault()
        if (st.status.state === 'running') control(st.status.paused ? 'resume' : 'pause')
      } else if (e.key === 'r' || e.key === 'R') reproduce()
      else if (e.key === 'n' || e.key === 'N') newVariation()
      else if (e.key === 'f' || e.key === 'F') mapApi.current?.toggleOverview()
      else if (e.key === 'h' || e.key === 'H') mapApi.current?.home()
      else if (e.key === '+' || e.key === '=') mapApi.current?.zoom(1.5)
      else if (e.key === '-') mapApi.current?.zoom(1 / 1.5)
      else if (e.key === '[') st.togglePanel('left')
      else if (e.key === ']') st.togglePanel('right')
      else if (e.key === '\\') {
        const open = !st.panels.left && !st.panels.right
        st.togglePanel('left', open)
        st.togglePanel('right', open)
      } else if (e.key in ARROWS && st.view === 'simulate') {
        // let sliders, tabs and menus keep their own arrow-key behaviour
        if (el?.closest('[role=slider],[role=tablist],[role=menu],[role=listbox],[role=radiogroup]')) return
        e.preventDefault()
        mapApi.current?.step(ARROWS[e.key])
      } else if (e.key === 'Escape') st.set({ selection: null, follow: false, pick: null })
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
  const railOpen = useStore((s) => s.panels.left)
  // left-anchored map overlays sit beside the floating scenario rail
  const railStyle = { '--rail-w': railOpen ? '260px' : '64px' } as CSSProperties
  return (
    <main className="relative min-w-0 flex-1" style={railStyle}>
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
        <div className="anim-in absolute left-1/2 top-3 z-10 -translate-x-1/2 rounded-lg bg-ink px-3.5 py-2 text-[12px] font-medium text-white shadow-float">
          Click a street segment to place the event · Esc to cancel
        </div>
      )}
      <LaunchProgress />
      {conn === 'closed' && (
        <div className="glass anim-in absolute left-1/2 top-[64px] z-10 flex -translate-x-1/2 items-center gap-2.5 rounded-xl px-3.5 py-2">
          <WifiOff size={14} className="text-bad" />
          <div>
            <div className="text-[12.5px] font-medium text-ink">Simulation server unreachable</div>
            <div className="text-[11px] text-ink-3">Reconnecting automatically. Start it with run.ps1 or `python -m flowstate.cli serve`.</div>
          </div>
        </div>
      )}
      {!run && conn === 'open' && net && status.state === 'idle' && (
        <div className="glass anim-in pointer-events-none absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-xl px-4 py-2.5 text-center">
          <div className="text-[13px] font-semibold text-ink">{net.title}</div>
          <div className="text-[11.5px] text-ink-3">Pick a scenario and press Generate &amp; Run · drag to pan, scroll to zoom</div>
          <div className="mt-1.5 flex items-center justify-center gap-3 text-[11px] text-ink-3">
            <span className="flex items-center gap-1">
              <span className="kbd">←</span>
              <span className="kbd">→</span> intersections
            </span>
            <span className="flex items-center gap-1">
              <span className="kbd">F</span> network
            </span>
            <span className="flex items-center gap-1">
              <span className="kbd">\</span> map only
            </span>
          </div>
        </div>
      )}
      <ScenarioRail />
      <RunBar />
    </main>
  )
}

function Toasts() {
  const toasts = useStore((s) => s.toasts)
  const rightOpen = useStore((s) => s.panels.right && s.view === 'simulate')
  return (
    <div className={cn('pointer-events-none fixed bottom-20 z-50 flex flex-col items-end gap-2', rightOpen ? 'right-[356px]' : 'right-[72px]')}>
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            'anim-in pointer-events-auto max-w-[360px] rounded-lg border bg-surface px-3 py-2 text-[12.5px] shadow-float',
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
            <MapStage />
            <RightPanel />
          </div>
          {view === 'analyze' && <AnalyticsView />}
          {view === 'runs' && <RunsView />}
        </div>
      </div>
      <ConfigDialog />
      <Toasts />
    </TooltipProvider>
  )
}
