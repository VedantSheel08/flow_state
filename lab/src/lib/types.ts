// Shapes sent by the lab backend (backend/flowstate/lab). Everything is measured from SUMO unless named p30/p60 (forecasts).
export type Pt = [number, number]
export interface Geometry {
  lanes: { id: string; s: Pt[]; w: number; i: number }[]
  junctions: { id: string; s: Pt[]; tls: boolean; c: Pt }[]
  heads: { j: string; l: string; x: number; y: number; k: number[] }[]
  approaches: { key: string; edge: string; cap: number; entry: boolean; a: Pt; b: Pt }[]
  bounds: [number, number, number, number]
  controlled: string[]
}
// vehicle: [id, x*10, y*10, angle deg, type 0 car|1 truck|2 emergency, speed*10 m/s, waiting 0|1]
export type Veh = [number, number, number, number, number, number, number]
export interface Frame { t: number; v: Veh[]; s: string[]; k: number[] }
export interface Obs { t: number; q: number[]; p30?: number[]; p60?: number[]; ml?: number[] }
export interface DiffRow { key: string; label: string; unit: string; left: number; right: number; diff: number; pct: number | null; verdict: string }
export interface RunRec {
  controller: string
  controllerInfo: { mlInControl?: boolean; params?: Record<string, number> }
  metrics: Record<string, number | string>
  timing: Record<string, { mean_ms: number; p95_ms: number; n: number }>
  wall_s: number
  demandSha: string
  ml?: { decisions: number; changed: number; switchedEarlier: number; heldLonger: number }
  mlLog?: [number, number, number][]
  frames: Frame[]
  obs: Obs[]
}
export interface Replay {
  id: string; scenario: string; seed: number; intensity: number; demandSha: string; kpiFields: string[]
  left: RunRec; right: RunRec; diff: DiffRow[]; mlInControl: { left: boolean; right: boolean }; recorded_at: string
}
export interface ReplaySummary {
  id: string; scenario: string; seed: number; intensity: number; left: string; right: string
  mlInControl: { left: boolean; right: boolean }; diff: DiffRow[]; ml?: RunRec['ml']; duration_s: number
}
export const KPI = { inNetwork: 0, halting: 1, meanWaitNow: 2, arrived: 3, co2Kg: 4, teleports: 5, collisions: 6, pending: 7 } as const
export const CONTROLLER_LABEL: Record<string, string> = {
  fixed_time: 'Fixed-time (30 s)',
  queue_heuristic: 'Queue heuristic — no ML',
  ml_forecast: 'Queue heuristic + ML forecast',
}
