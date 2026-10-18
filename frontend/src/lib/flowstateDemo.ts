// Side-by-side replay for the Results page: one recorded SUMO pair (same demand, same seed), fixed-time signals vs FlowState.
// The asset is built by backend `python -m flowstate.lab.cli demo-asset` from a recorded run; nothing here is simulated in the browser.
import type { NetworkGeom } from './types'

/** [id, x*10, y*10, angle, type 0 car|1 truck|2 ambulance, speed*10 m/s] */
export type DemoVeh = [number, number, number, number, number, number]
/** ambulance state: [id, status 0 moving|1 red light|2 stuck in queue|3 green ahead, metres to the next signal * 10] */
export type DemoEv = [number, number, number]
export interface DemoFrame { t: number; v: DemoVeh[]; s: string[]; e?: DemoEv[] }
export interface DemoSide { controller: string; frames: DemoFrame[] }
export interface UnitSide { travelS: number; stoppedRedS: number; stoppedQueueS: number; finished: boolean; departS: number; first: number | null; last: number | null }
export interface DemoUnit { id: number; left: UnitSide; right: UnitSide; savedS: number }
export interface AggStat { mean_diff: number; ci95: [number, number]; pairs: number; better: number; worse: number; left_mean: number; right_mean: number; pct: number | null }
export interface DemoTotals { ev_total: number; ev_unfinished: number; ev_travel_mean_s: number; ev_stopped_red_s: number; ev_stopped_queue_s: number; mean_wait_civilian_s: number; mean_travel_s: number; throughput_vph: number; queue_total_mean_veh: number }
export interface DemoAsset {
  schema: string
  pair: string
  scenario: string
  scenarioTitle: string
  seed: number
  intensity: number
  bestCaseStopS: number
  minQueueS: number
  left: DemoSide
  right: DemoSide
  ambulances: DemoUnit[]
  example: number | null
  totals: { left: DemoTotals; right: DemoTotals }
  aggregate: { episodes: number; seeds: number[]; metrics: Record<string, AggStat> }
  rightIncludes: string
}

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

let loading: Promise<{ asset: DemoAsset; net: NetworkGeom }> | null = null
/** Fetches the recorded pair and the grid's map description once. */
export function loadDemo() {
  loading ??= Promise.all([
    fetch(`${BASE}pair.json.gz`).then((r) => {
      if (!r.ok) throw new Error(`Recorded run not found (${r.status})`)
      return gunzipJson<DemoAsset>(r)
    }),
    fetch(`${BASE}grid-network.json`).then((r) => {
      if (!r.ok) throw new Error(`Map not found (${r.status})`)
      return r.json() as Promise<NetworkGeom>
    }),
  ]).then(([asset, net]) => ({ asset, net }))
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
  const i = Math.min(frames.length - 1, Math.max(0, Math.floor(t - first)))
  const a = frames[i], b = frames[Math.min(frames.length - 1, i + 1)]
  const span = b.t - a.t
  return { a, b, u: span > 0 ? Math.min(1, Math.max(0, (t - a.t) / span)) : 0 }
}

const lerpAngle = (a: number, b: number, u: number) => a + ((((b - a) % 360) + 540) % 360 - 180) * u

/** Vehicles at time t, interpolated between the two recorded one-second frames (a car that appears or leaves is drawn from the frame that has it). */
export function sample(frames: DemoFrame[], t: number): Placed[] {
  const { a, b, u } = frameAt(frames, t)
  const nb = byId(b)
  return a.v.map((r) => {
    const n = nb.get(r[0])
    if (!n || n === r) return { id: r[0], x: r[1] / 10, y: r[2] / 10, angle: r[3], speed: r[5] / 10, type: r[4] }
    return { id: r[0], x: (r[1] + (n[1] - r[1]) * u) / 10, y: (r[2] + (n[2] - r[2]) * u) / 10, angle: lerpAngle(r[3], n[3], u), speed: (r[5] + (n[5] - r[5]) * u) / 10, type: r[4] }
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

/** Time window that covers both journeys of one ambulance, with a few seconds either side. */
export function windowFor(asset: DemoAsset, id: number, pad = 8): [number, number] {
  const u = asset.ambulances.find((x) => x.id === id)
  const t0 = asset.left.frames[0].t, t1 = asset.left.frames[asset.left.frames.length - 1].t
  if (!u || u.left.first == null || u.right.first == null) return [t0, t1]
  const lo = Math.min(u.left.first, u.right.first) - pad
  const hi = Math.max(u.left.last ?? t1, u.right.last ?? t1) + pad
  return [Math.max(t0, lo), Math.min(t1, hi)]
}

/** Lab vehicle types -> main's sprite codes (sedan, suv, truck, bus, bike, ambulance, ..., hatch, van, pickup). */
const CAR_CODES = [0, 9, 1, 0, 9, 1, 0, 11]
export const spriteCode = (p: Placed) => (p.type === 2 ? 5 : p.type === 1 ? 2 : CAR_CODES[p.id % CAR_CODES.length])

/** Is the change a good one for this measure? All of these are "lower is better" except throughput. */
export const LOWER_IS_BETTER = new Set(['ev_travel_mean_s', 'ev_stopped_red_s', 'ev_stopped_queue_s', 'mean_wait_civilian_s', 'mean_travel_s', 'queue_total_mean_veh'])
