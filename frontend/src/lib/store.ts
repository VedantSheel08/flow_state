import { create } from 'zustand'
import type { Health } from './api'
import { defaultCongestion } from './scenarioPresets'
import type { RlJob } from './controllerResults'
import type { CompareJob, CongestionPreset, Kpi, NetworkGeom, RunInfo, RunResults, RunStatus, ScenarioSpec, SimEvent, StatsMessage } from './types'

export type Selection = { kind: 'vehicle'; id: string } | { kind: 'junction'; id: string } | { kind: 'edge'; id: string } | null
export type View = 'simulate' | 'results' | 'analyze' | 'runs'

export interface Layers {
  buildings: boolean
  trees: boolean
  labels: boolean
  pedestrians: boolean
  flow: boolean
  signals: boolean
}

export type CamMode = 'intersection' | 'network'

export interface Panels {
  left: boolean
  right: boolean
}

/** Default camera target: John Street & King Street West. */
export const HOME_JUNCTION = 'n3_3'

const PANELS_KEY = 'flowstate.panels'

function loadPanels(): Panels {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(PANELS_KEY) : null
    if (raw) {
      const p = JSON.parse(raw)
      return { left: p.left !== false, right: p.right !== false }
    }
  } catch {
    /* storage unavailable */
  }
  return { left: true, right: true }
}

export interface Toast {
  id: number
  tone: 'info' | 'error' | 'success'
  text: string
}

interface State {
  connection: 'connecting' | 'open' | 'closed'
  health: Health | null
  scenarios: ScenarioSpec[]
  scenarioId: string
  drafts: Record<string, Record<string, unknown>>
  seed: string
  run: RunInfo | null
  status: RunStatus
  stats: StatsMessage | null
  history: Kpi[]
  events: SimEvent[]
  results: RunResults | null
  networks: Record<string, NetworkGeom>
  selection: Selection
  follow: boolean
  layers: Layers
  pick: string | null
  view: View
  toasts: Toast[]
  compare: CompareJob | null
  /** Latest controller comparison on /api/rl (Results tab). */
  rlJob: RlJob | null
  rightTab: 'overview' | 'inspect' | 'events'
  camMode: CamMode
  focusJunction: string
  /** Bumped on every camera request so re-focusing the same target re-centres. */
  camNonce: number
  /** The user has panned/zoomed away from the requested camera view. */
  camDetached: boolean
  panels: Panels
  /** Wall-clock milestones of the current Generate & Run (performance.now()), for progress feedback. */
  launch: { t0: number; posted: number | null; scenario: string } | null
  /** Backend congestion presets; null until loaded, [] when the backend has no preset API. */
  congestionPresets: CongestionPreset[] | null
  /** Selected congestion preset id per scenario (null = scenario defaults). */
  congestion: Record<string, string | null>
  /** The Configure dialog is open. */
  configOpen: boolean
  /** Reopen the Configure dialog once a map pick finishes (it closes so the map is clickable). */
  reopenConfigAfterPick: boolean
}

interface Actions {
  set: (p: Partial<State>) => void
  setDraft: (scenario: string, key: string, value: unknown) => void
  resetDraft: (scenario: string) => void
  select: (s: Selection) => void
  toast: (tone: Toast['tone'], text: string) => void
  toggleLayer: (k: keyof Layers) => void
  onRun: (run: RunInfo) => void
  onStats: (m: StatsMessage) => void
  onEvent: (e: SimEvent) => void
  focusOn: (junction: string) => void
  showNetwork: () => void
  togglePanel: (side: keyof Panels, open?: boolean) => void
  /** Select a congestion preset and drop draft values it supplies, so the preset applies. */
  setCongestion: (scenario: string, id: string | null, controlled: string[]) => void
}

let toastId = 1

export const useStore = create<State & Actions>((set, get) => ({
  connection: 'connecting',
  health: null,
  scenarios: [],
  scenarioId: 'rush_hour',
  drafts: {},
  seed: '',
  run: null,
  status: { run: null, state: 'idle' },
  stats: null,
  history: [],
  events: [],
  results: null,
  networks: {},
  selection: null,
  follow: false,
  layers: { buildings: true, trees: true, labels: true, pedestrians: true, flow: false, signals: true },
  pick: null,
  view: 'simulate',
  toasts: [],
  compare: null,
  rlJob: null,
  rightTab: 'overview',
  camMode: 'intersection',
  focusJunction: HOME_JUNCTION,
  camNonce: 0,
  camDetached: false,
  panels: loadPanels(),
  launch: null,
  congestionPresets: null,
  congestion: {},
  configOpen: false,
  reopenConfigAfterPick: false,

  set: (p) => set(p),
  setDraft: (scenario, key, value) =>
    set((s) => ({ drafts: { ...s.drafts, [scenario]: { ...(s.drafts[scenario] ?? {}), [key]: value } } })),
  resetDraft: (scenario) =>
    set((s) => ({ drafts: { ...s.drafts, [scenario]: {} }, congestion: { ...s.congestion, [scenario]: defaultCongestion(scenario, s.congestionPresets) } })),
  select: (sel) => set({ selection: sel, follow: sel?.kind === 'vehicle' ? get().follow : false, rightTab: sel ? 'inspect' : get().rightTab }),
  toast: (tone, text) => {
    const id = toastId++
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, tone, text }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), tone === 'error' ? 7000 : 4000)
  },
  toggleLayer: (k) => set((s) => ({ layers: { ...s.layers, [k]: !s.layers[k] } })),
  onRun: (run) =>
    set((s) => ({
      run,
      history: [],
      events: [],
      stats: null,
      results: null,
      selection: s.selection?.kind === 'junction' ? s.selection : null,
      follow: false,
    })),
  onStats: (m) =>
    set((s) => {
      if (s.run && m.run !== s.run.id) return {}
      const last = s.history[s.history.length - 1]
      const history = !last || m.kpi.t > last.t ? [...s.history, m.kpi] : s.history
      return { stats: m, history: history.length > 4000 ? history.slice(-4000) : history, status: m.status }
    }),
  onEvent: (e) => set((s) => ({ events: [...s.events.slice(-499), e] })),
  setCongestion: (scenario, id, controlled) =>
    set((s) => {
      const draft = { ...(s.drafts[scenario] ?? {}) }
      for (const k of controlled) delete draft[k]
      return { congestion: { ...s.congestion, [scenario]: id }, drafts: { ...s.drafts, [scenario]: draft } }
    }),
  focusOn: (junction) => set((s) => ({ camMode: 'intersection', focusJunction: junction, camNonce: s.camNonce + 1, camDetached: false, follow: false })),
  showNetwork: () => set((s) => ({ camMode: 'network', camNonce: s.camNonce + 1, camDetached: false, follow: false })),
  togglePanel: (side, open) =>
    set((s) => {
      const panels = { ...s.panels, [side]: open ?? !s.panels[side] }
      try {
        localStorage.setItem(PANELS_KEY, JSON.stringify(panels))
      } catch {
        /* storage unavailable */
      }
      return { panels }
    }),
}))

// The first time a scenario is shown (once presets have loaded), start it on its
// own congestion preset. An explicit null (preset toggled off) is left alone.
function seedCongestion(s: State) {
  if (!s.congestionPresets || s.scenarioId in s.congestion) return
  useStore.setState({ congestion: { ...s.congestion, [s.scenarioId]: defaultCongestion(s.scenarioId, s.congestionPresets) } })
}
useStore.subscribe((s, prev) => {
  if (s.scenarioId !== prev.scenarioId || s.congestionPresets !== prev.congestionPresets) seedCongestion(s)
})

export function paramsFor(spec: ScenarioSpec, draft: Record<string, unknown> | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of spec.params) out[p.key] = draft && p.key in draft ? draft[p.key] : p.default
  return out
}
