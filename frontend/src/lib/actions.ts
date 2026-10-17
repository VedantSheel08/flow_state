import { api, ApiError } from './api'
import { frames } from './frames'
import { runRequest } from './intensity'
import { useStore } from './store'

export async function launch(opts: { seed?: number | null; scenario?: string; params?: Record<string, unknown>; congestion?: string | null } = {}) {
  const st = useStore.getState()
  const scenario = opts.scenario ?? st.scenarioId
  const spec = st.scenarios.find((s) => s.id === scenario)
  if (!spec) return
  // a selected backend congestion preset is sent by id; only hand-edited values override it
  const preset = st.congestionPresets?.find((p) => p.id === st.congestion[scenario]) ?? null
  const req = opts.params ? { params: opts.params, congestion: opts.congestion ?? null } : runRequest(spec, st.drafts[scenario], preset)
  const params = req.params
  const seedText = st.seed.trim()
  const seed = opts.seed !== undefined ? opts.seed : seedText ? Number(seedText) : null
  if (seed !== null && (!Number.isInteger(seed) || seed < 0)) {
    st.toast('error', 'Seed must be a whole number.')
    return
  }
  st.set({ pick: null, view: 'simulate', status: { run: null, state: 'starting' }, launch: { t0: performance.now(), posted: null, scenario: spec.title } })
  try {
    const run = await api.startRun(scenario, params, seed, frames.speed > 0 ? Math.min(frames.speed, 4) : 1, req.congestion)
    frames.reset(run.id)
    useStore.getState().onRun(run)
    const l = useStore.getState().launch
    useStore.getState().set({ scenarioId: scenario, launch: l && { ...l, posted: performance.now() } })
  } catch (e) {
    useStore.getState().set({ status: { run: null, state: 'idle' }, launch: null })
    st.toast('error', e instanceof ApiError ? e.message : String(e))
  }
}

export async function control(action: 'pause' | 'resume' | 'stop', ) {
  try {
    await api.control(action)
  } catch (e) {
    useStore.getState().toast('error', e instanceof ApiError ? e.message : String(e))
  }
}

/** Stop the run; the backend saves it (with partial results) to the run history. */
export async function endRun() {
  try {
    await api.control('stop')
    useStore.getState().toast('info', 'Run ended · saved to Run history')
  } catch (e) {
    useStore.getState().toast('error', e instanceof ApiError ? e.message : String(e))
  }
}

export async function setSpeed(speed: number) {
  frames.speed = speed
  try {
    await api.control('speed', speed)
  } catch (e) {
    useStore.getState().toast('error', e instanceof ApiError ? e.message : String(e))
  }
}

export function reproduce() {
  const run = useStore.getState().run
  if (!run) return
  // resolved params already contain the preset's values; the id keeps warm-up and metadata identical
  return launch({ seed: run.seed, scenario: run.scenario, params: run.params, congestion: run.congestion ?? null })
}

export function newVariation() {
  const run = useStore.getState().run
  if (!run) return launch()
  return launch({ seed: null, scenario: run.scenario, params: run.params, congestion: run.congestion ?? null })
}
