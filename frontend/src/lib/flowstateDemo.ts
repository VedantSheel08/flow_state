// Side-by-side replay for the Results page: one recorded SUMO pair (same seed, same demand), fixed-time signals vs FlowState.
// The asset is produced by `python -m flowstate.replay.record` (backend/flowstate/replay) from real runs on the `district` network; nothing is simulated in the browser.
import type { NetworkGeom } from './types'

/** [id, x*10, y*10, angle, type, speed*10 m/s]. `type` is one of main's sprite codes when `codes === 'main'` (sedan 0 … ambulance 5), else lab codes (0 car, 1 truck, 2 ambulance). */
export type DemoVeh = [number, number, number, number, number, number]
/** ambulance state: [id, status 0 moving|1 red light|2 stuck in queue|3 green ahead, metres to the next signal * 10] */
export type DemoEv = [number, number, number]
export interface DemoFrame { t: number; v: DemoVeh[]; s: string[]; e?: DemoEv[] }
export interface DemoSide { controller: string; frames: DemoFrame[] }
export interface UnitSide { travelS: number; stoppedRedS: number; stoppedQueueS: number; finished: boolean; departS: number; first: number | null; last: number | null; id?: number }
export interface DemoUnit { id: number; left: UnitSide; right: UnitSide; savedS: number | null; call?: number; label?: string; kind?: string }
export interface DemoFocus { kind: 'point' | 'junction' | 'network' | 'ambulance'; x?: number; y?: number; label?: string; id?: string }
export interface DemoKpi { key: string; label: string; unit: string }
export interface AggStat { mean_diff: number; ci95: [number, number]; pairs: number; better: number; worse: number; left_mean: number; right_mean: number; pct: number | null }
export interface DemoTotals { ev_total: number; ev_unfinished: number; ev_travel_mean_s: number; ev_stopped_red_s: number; ev_stopped_queue_s: number; mean_wait_civilian_s: number; mean_travel_s: number; throughput_vph: number; queue_total_mean_veh: number; car?: Record<string, number>; priority?: Record<string, number | string> | null }
export interface DemoAsset {
  schema: string
  network?: string
  codes?: 'main'
  tlsIds?: string[]
  pair: string
  scenario: string
  scenarioTitle: string
  seed: number
  bestCaseStopS: number
  minQueueS: number
  left: DemoSide
  right: DemoSide
  ambulances: DemoUnit[]
  example: number | null
  totals: { left: DemoTotals; right: DemoTotals }
  aggregate: { episodes: number; seeds: number[]; metrics: Record<string, AggStat> }
  rightIncludes: string
  /** schema 3 */
  units?: DemoUnit[]
  focus?: DemoFocus
  kpis?: DemoKpi[]
  tagline?: string
  dt?: number
}

export interface DemoHeadline { key: string; label: string; unit: string; left: number | null; right: number | null; pct: number | null; ci95: [number, number] | null; better: number | null; worse: number | null }
export interface DemoIndex { network: string; builtAt: string; seeds: number[]; scenarios: { id: string; title: string; tagline: string; units: number; headline: DemoHeadline }[] }

export const STATUS_TEXT = ['moving', 'stopped at a red light', 'stuck behind queued cars', 'moving, green light ahead']

const BASE = `${import.meta.env.BASE_URL}flowstate-demo/`

/** Some servers send the .gz with Content-Encoding: gzip (the browser unpacks it), others as a plain file: unpack only when the bytes are still gzip. */
export async function gunzipJson<T>(res: Response): Promise<T> {
  const buf = new Uint8Array(await res.arrayBuffer())
  if (buf[0] !== 0x1f || buf[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(buf)) as T
  if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot unpack the recorded run (DecompressionStream is missing).')
  const stream = new Response(buf).body!.pipeThrough(new DecompressionStream('gzip'))
  return JSON.parse(await new Response(stream).text()) as T
}

/** Main's per-mode metric names -> the names the page uses (schema 2 assets record main's `Metrics` for cars). */
const CAR_KEYS: Record<string, string> = { car_waiting: 'mean_wait_civilian_s', car_travelTime: 'mean_travel_s', car_timeLoss: 'time_loss_s' }

/** Fills the page-level fields from main's car metrics so one component serves both asset schemas. */
export function normalise(asset: DemoAsset): DemoAsset {
  asset.ambulances ??= asset.units ?? []
  for (const side of ['left', 'right'] as const) {
    const t = asset.totals[side]
    if (t.car) {
      t.mean_wait_civilian_s ??= t.car.waiting
      t.mean_travel_s ??= t.car.travelTime
    }
  }
  const m = asset.aggregate.metrics
  for (const [from, to] of Object.entries(CAR_KEYS)) if (m[from] && !m[to]) m[to] = m[from]
  return asset
}

const cache = new Map<string, Promise<unknown>>()
const once = <T,>(key: string, make: () => Promise<T>): Promise<T> => {
  let p = cache.get(key) as Promise<T> | undefined
  if (!p) { p = make(); cache.set(key, p); p.catch(() => cache.delete(key)) }
  return p
}
const getJson = async <T,>(url: string, what: string): Promise<T> => {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${what} not found (${r.status})`)
  return r.json() as Promise<T>
}
/** The list of recorded scenarios. */
export const loadIndex = () => once('index', () => getJson<DemoIndex>(`${BASE}index.json`, 'Recorded scenarios'))
/** The map description of a recorded network (once per network). */
export const loadNetwork = (id: string) => once(`net:${id}`, () => getJson<NetworkGeom>(`${BASE}${id}-network.json`, 'Map'))
/** One scenario's recorded pair. */
export const loadScenario = (id: string) => once(`sc:${id}`, async () => {
  const r = await fetch(`${BASE}${id}/pair.json.gz`)
  if (!r.ok) throw new Error(`Recorded run not found (${r.status})`)
  return normalise(await gunzipJson<DemoAsset>(r))
})

let loading: Promise<{ asset: DemoAsset; net: NetworkGeom }> | null = null
/** Fetches the recorded pair, then the map description of the network it was recorded on. */
export function loadDemo() {
  loading ??= fetch(`${BASE}pair.json.gz`)
    .then((r) => {
      if (!r.ok) throw new Error(`Recorded run not found (${r.status})`)
      return gunzipJson<DemoAsset>(r)
    })
    .then(async (asset) => {
      const r = await fetch(`${BASE}${asset.network ?? 'grid'}-network.json`)
      if (!r.ok) throw new Error(`Map not found (${r.status})`)
      return { asset: normalise(asset), net: (await r.json()) as NetworkGeom }
    })
  loading.catch(() => { loading = null })
  return loading
}

/** One vehicle at an instant, in metres and degrees. */
export interface Placed { id: number; x: number; y: number; angle: number; speed: number; type: number }

const nextIndex = new WeakMap<DemoFrame, Map<number, DemoVeh>>()
const byId = (f: DemoFrame) => {
  let m = nextIndex.get(f)
  if (!m) { m = new Map(f.v.map((r) => [r[0], r])); nextIndex.set(f, m) }
  return m
}

export function frameAt(frames: DemoFrame[], t: number): { a: DemoFrame; b: DemoFrame; u: number } {
  const first = frames[0].t
  const dt = frames.length > 1 ? frames[1].t - first : 1
  const i = Math.min(frames.length - 1, Math.max(0, Math.floor((t - first) / dt)))
  const a = frames[i], b = frames[Math.min(frames.length - 1, i + 1)]
  const span = b.t - a.t
  return { a, b, u: span > 0 ? Math.min(1, Math.max(0, (t - a.t) / span)) : 0 }
}

const lerpAngle = (a: number, b: number, u: number) => a + ((((b - a) % 360) + 540) % 360 - 180) * u
const RAD = Math.PI / 180

/**
 * Vehicles at time t, interpolated between the two recorded frames that bracket it. Cubic Hermite with SUMO's own heading and speed as end tangents,
 * so a vehicle passes through every recorded position and follows its turning path (the same idea as the live map's playout). A car that appears or
 * leaves between the two frames is drawn from the frame that has it.
 */
export function sample(frames: DemoFrame[], t: number): Placed[] {
  const { a, b, u } = frameAt(frames, t)
  const nb = byId(b)
  const span = b.t - a.t
  return a.v.map((r) => {
    const n = nb.get(r[0])
    if (!n || n === r || span <= 0) return { id: r[0], x: r[1] / 10, y: r[2] / 10, angle: r[3], speed: r[5] / 10, type: r[4] }
    const x0 = r[1] / 10, y0 = r[2] / 10, x1 = n[1] / 10, y1 = n[2] / 10
    const v0 = r[5] / 10, v1 = n[5] / 10
    const dx = x1 - x0, dy = y1 - y0, chord = Math.hypot(dx, dy)
    if (chord > Math.max(v0, v1, 1) * span * 3 + 2) return { id: r[0], x: x0, y: y0, angle: r[3], speed: v0, type: r[4] } // teleport: hold, do not fly
    const m0x = Math.sin(r[3] * RAD) * v0 * span, m0y = Math.cos(r[3] * RAD) * v0 * span
    const m1x = Math.sin(n[3] * RAD) * v1 * span, m1y = Math.cos(n[3] * RAD) * v1 * span
    const u2 = u * u, u3 = u2 * u
    const h00 = 2 * u3 - 3 * u2 + 1, h10 = u3 - 2 * u2 + u, h01 = -2 * u3 + 3 * u2, h11 = u3 - u2
    const x = h00 * x0 + h10 * m0x + h01 * x1 + h11 * m1x
    const y = h00 * y0 + h10 * m0y + h01 * y1 + h11 * m1y
    const d00 = 6 * u2 - 6 * u, d10 = 3 * u2 - 4 * u + 1, d01 = -6 * u2 + 6 * u, d11 = 3 * u2 - 2 * u
    const tx = d00 * x0 + d10 * m0x + d01 * x1 + d11 * m1x, ty = d00 * y0 + d10 * m0y + d01 * y1 + d11 * m1y
    const speed = v0 + (v1 - v0) * u
    const angle = speed > 0.5 && Math.hypot(tx, ty) > 1e-3 ? (Math.atan2(tx, ty) / RAD + 360) % 360 : lerpAngle(r[3], n[3], u)
    return { id: r[0], x, y, angle, speed, type: r[4] }
  })
}

/** Where a given vehicle is at time t, or null if it is not on the map at that moment. */
export function locate(frames: DemoFrame[], id: number, t: number): Placed | null {
  return sample(frames, t).find((p) => p.id === id) ?? null
}

/** The ambulance's measured state at t (status from SUMO), or null when it is not on the network. */
export function evState(frames: DemoFrame[], id: number, t: number): { status: number; metresToSignal: number } | null {
  const { a } = frameAt(frames, t)
  const e = a.e?.find((x) => x[0] === id)
  return e ? { status: e[1], metresToSignal: e[2] / 10 } : null
}

/** The vehicle id of one ambulance on one side (ids differ per side in schema 2 assets). */
export const sideId = (u: DemoUnit, side: 'left' | 'right') => u[side].id ?? u.id

/** Time window that covers both journeys of one ambulance, with a few seconds either side. */
export function windowFor(asset: DemoAsset, id: number, pad = 8): [number, number] {
  const u = asset.ambulances.find((x) => x.id === id)
  const t0 = asset.left.frames[0].t, t1 = asset.left.frames[asset.left.frames.length - 1].t
  if (!u || u.left.first == null || u.right.first == null) return [t0, t1]
  const lo = Math.min(u.left.first, u.right.first) - pad
  const hi = Math.max(u.left.last ?? t1, u.right.last ?? t1) + pad
  return [Math.max(t0, lo), Math.min(t1, hi)]
}

/** Lab vehicle types -> main's sprite codes (sedan, suv, truck, bus, bike, ambulance, ..., hatch, van, pickup). Assets with `codes: 'main'` already carry main's codes. */
const CAR_CODES = [0, 9, 1, 0, 9, 1, 0, 11]
export const spriteCode = (p: Placed, mainCodes = false) => (mainCodes ? p.type : p.type === 2 ? 5 : p.type === 1 ? 2 : CAR_CODES[p.id % CAR_CODES.length])
export const isAmbulance = (p: Placed, mainCodes = false) => (mainCodes ? p.type === 5 : p.type === 2)

/** Is the change a good one for this measure? All of these are "lower is better" except throughput and speed. */
export const LOWER_IS_BETTER = new Set(['ev_travel_mean_s', 'ev_stopped_red_s', 'ev_stopped_queue_s', 'mean_wait_civilian_s', 'mean_travel_s', 'time_loss_s', 'queue_total_mean_veh', 'car_waiting', 'car_travelTime', 'car_timeLoss'])
