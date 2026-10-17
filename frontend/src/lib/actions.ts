import { api, ApiError } from './api'
import { frames } from './frames'
import { paramsFor, useStore } from './store'

export async function launch(opts: { seed?: number | null; scenario?: string; params?: Record<string, unknown> } = {}) {
  const st = useStore.getState()
  const scenario = opts.scenario ?? st.scenarioId
  const spec = st.scenarios.find((s) => s.id === scenario)
  if (!spec) return
  const params = opts.params ?? paramsFor(spec, st.drafts[scenario])
  const seedText = st.seed.trim()
  const seed = opts.seed !== undefined ? opts.seed : seedText ? Number(seedText) : null
  if (seed !== null && (!Number.isInteger(seed) || seed < 0)) {
    st.toast('error', 'Seed must be a whole number.')
    return
  }
  st.set({ pick: null, view: 'simulate', status: { run: null, state: 'starting' } })
  try {
    const run = await api.startRun(scenario, params, seed, frames.speed > 0 ? Math.min(frames.speed, 4) : 1)
    frames.reset(run.id)
    useStore.getState().onRun(run)
    useStore.getState().set({ scenarioId: scenario })
  } catch (e) {
    useStore.getState().set({ status: { run: null, state: 'idle' } })
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
  return launch({ seed: run.seed, scenario: run.scenario, params: run.params })
}

export function newVariation() {
  const run = useStore.getState().run
  if (!run) return launch()
  return launch({ seed: null, scenario: run.scenario, params: run.params })
}
