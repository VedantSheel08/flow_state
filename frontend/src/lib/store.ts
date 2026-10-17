import { create } from 'zustand'
import type { Health } from './api'
import type { CompareJob, Kpi, NetworkGeom, RunInfo, RunResults, RunStatus, ScenarioSpec, SimEvent, StatsMessage } from './types'

export type Selection = { kind: 'vehicle'; id: string } | { kind: 'junction'; id: string } | { kind: 'edge'; id: string } | null
export type View = 'simulate' | 'analyze' | 'runs'

export interface Layers {
  buildings: boolean
  labels: boolean
  pedestrians: boolean
  flow: boolean
  signals: boolean
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
  rightTab: 'overview' | 'inspect' | 'events'
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
  layers: { buildings: true, labels: true, pedestrians: true, flow: false, signals: true },
  pick: null,
  view: 'simulate',
  toasts: [],
  compare: null,
  rightTab: 'overview',

  set: (p) => set(p),
  setDraft: (scenario, key, value) =>
    set((s) => ({ drafts: { ...s.drafts, [scenario]: { ...(s.drafts[scenario] ?? {}), [key]: value } } })),
  resetDraft: (scenario) => set((s) => ({ drafts: { ...s.drafts, [scenario]: {} } })),
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
}))

export function paramsFor(spec: ScenarioSpec, draft: Record<string, unknown> | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of spec.params) out[p.key] = draft && p.key in draft ? draft[p.key] : p.default
  return out
}
