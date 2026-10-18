import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { getJSON } from '../lib/api'
import type { Geometry, Replay, RunRec } from '../lib/types'

vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  getJSON: vi.fn(),
}))
vi.mock('../components/MapCanvas', () => ({ default: () => <canvas /> }))
vi.mock('../views/LiveView', () => ({
  default: () => <div>Live simulation controls</div>,
}))
vi.mock('../views/ForecastView', () => ({
  default: () => <div>Forecast diagnostics</div>,
}))
vi.mock('../views/PerfView', () => ({
  default: () => <div>Runtime diagnostics</div>,
}))

const geometry: Geometry = {
  lanes: [],
  junctions: [],
  heads: [],
  bounds: [0, 0, 10, 10],
  controlled: ['A0'],
  approaches: [
    { key: 'A0:N', edge: 'e', cap: 20, entry: true, a: [0, 0], b: [0, 1] },
  ],
}
const run = (controller: string, waits: number): RunRec => ({
  controller,
  controllerInfo: {},
  metrics: {},
  timing: {},
  wall_s: 1,
  demandSha: 'sample',
  frames: [
    {
      t: 1,
      s: [],
      k: [2, 7, 5, 0],
      v: [
        [1, 0, 0, 0, 0, 0, waits],
        [2, 0, 0, 0, 0, 10, 0],
      ],
    },
  ],
  obs: [{ t: 0, q: [2], p30: [3], p60: [4] }],
  mlLog: [],
})
const replay: Replay = {
  id: 'sample',
  scenario: 'balanced',
  seed: 1,
  intensity: 1,
  demandSha: 'sample',
  kpiFields: [],
  recorded_at: '',
  mlInControl: { left: false, right: true },
  left: run('queue_heuristic', 1),
  right: run('ml_forecast', 0),
  diff: [
    {
      key: 'mean_wait_s',
      label: 'Waiting',
      unit: 's',
      left: 10,
      right: 12,
      diff: 2,
      pct: 20,
      verdict: 'left better',
    },
    {
      key: 'queue_total_mean_veh',
      label: 'Mean queue',
      unit: 'veh',
      left: 4,
      right: 5,
      diff: 1,
      pct: 25,
      verdict: 'left better',
    },
    {
      key: 'throughput_vph',
      label: 'Throughput',
      unit: 'veh/h',
      left: 100,
      right: 90,
      diff: -10,
      pct: -10,
      verdict: 'left better',
    },
  ],
}
let container: HTMLDivElement, root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('requestAnimationFrame', () => 0)
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  vi.mocked(getJSON).mockImplementation(async (path) => {
    if (path.endsWith('/geometry')) return geometry as never
    if (path.endsWith('/status'))
      return {
        scenarios: [{ id: 'balanced', title: 'Balanced', description: '' }],
        mlAvailable: true,
        gemini: {},
      } as never
    if (path.endsWith('/comparison')) return { summary: {} } as never
    if (path.endsWith('/replays'))
      return {
        replays: [
          {
            id: replay.id,
            scenario: replay.scenario,
            seed: 1,
            intensity: 1,
            left: replay.left.controller,
            right: replay.right.controller,
          },
        ],
      } as never
    return replay as never
  })
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})
async function open() {
  await act(async () => {
    root.render(<App />)
  })
}
describe('comparison workspace', () => {
  it('shows exactly the requested four KPIs and reports worse results honestly', async () => {
    await open()
    expect(
      [...container.querySelectorAll('.outcome h3')].map((e) => e.textContent),
    ).toEqual([
      'Average waiting time',
      'Average queued vehicles',
      'Throughput',
      'Delayed vehicles',
    ])
    expect(container.querySelector('.explanation')?.textContent).toContain(
      'More waiting in this run',
    )
    expect(
      container.querySelector('.outcome:last-child')?.textContent,
    ).toContain('-1 veh')
    expect(container.querySelector('#advanced')?.hasAttribute('open')).toBe(
      false,
    )
    expect(container.querySelector('.approach-inspector')).toBeNull()
  })
  it('keeps observed intersection values and forecasts inside Advanced', async () => {
    await open()
    const details = container.querySelector<HTMLDetailsElement>('#advanced')!
    await act(async () => {
      details.open = true
      details.dispatchEvent(new Event('toggle'))
    })
    expect(
      container.querySelector('.approach-inspector')?.textContent,
    ).toContain('Observed left')
    expect(
      container.querySelector('.approach-inspector')?.textContent,
    ).toContain('Predicted right +30s')
    const button = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Inspect A0:N"]',
    )!
    await act(async () => button.click())
    expect(button.getAttribute('aria-pressed')).toBe('true')
  })
  it('reports an unavailable replay instead of retaining a stale result', async () => {
    const normal = vi.mocked(getJSON).getMockImplementation()!
    vi.mocked(getJSON).mockImplementation((path) =>
      path.endsWith('/sample')
        ? Promise.reject(new Error('404 unavailable'))
        : normal(path),
    )
    await open()
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      '404 unavailable',
    )
    expect(container.querySelector('.outcomes')).toBeNull()
  })
})
