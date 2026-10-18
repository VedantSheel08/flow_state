// Shapes served by the lab backend. Observed = measured by SUMO; "p30/p60" and intervals = model forecasts; ambulance status comes from SUMO state.
export type Pt = [number, number]
export interface Geometry {
  lanes: { id: string; s: Pt[]; w: number; i: number }[]
  junctions: { id: string; s: Pt[]; tls: boolean; c: Pt }[]
  heads: { j: string; l: string; x: number; y: number; k: number[] }[]
  approaches: { key: string; edge: string; cap: number; entry: boolean; a: Pt; b: Pt }[]
  bounds: [number, number, number, number]
  controlled: string[]
}
// vehicle: [id, x*10, y*10, angle deg, type 0 car|1 truck|2 ambulance, speed*10 m/s, waiting 0|1]
export type Veh = [number, number, number, number, number, number, number]
// ambulance state in a frame: [id, status 0 moving|1 red light|2 stuck in queue|3 green ahead, metres to the next signal * 10]
export type EvState = [number, number, number]
export interface Frame { t: number; v: Veh[]; s: string[]; k: number[]; e?: EvState[] }
export interface Obs { t: number; q: number[]; p30?: number[]; p60?: number[]; lo30?: number[]; hi30?: number[]; ml?: number[] }
export interface DiffRow { key: string; label: string; unit: string; left: number; right: number; diff: number; pct: number | null; verdict: string }
export interface RunRec {
  controller: string
  controllerInfo: { mlInControl?: boolean; emergencyGreen?: boolean }
  metrics: Record<string, number | string | unknown[]>
  ml?: { decisions: number; changed: number; switchedEarlier: number; heldLonger: number }
  mlLog?: [number, number, number][]
  frames: Frame[]
  obs: Obs[]
}
export interface Replay { id: string; scenario: string; seed: number; intensity: number; demandSha: string; left: RunRec; right: RunRec; diff: DiffRow[] }
export interface ReplaySummary { id: string; scenario: string; seed: number; intensity: number; left: string; right: string; diff: DiffRow[] }
export interface StoryAmbulance { id: number; segments: [number, number, number][]; metrics: { travel_s: number; stopped_red_s: number; stopped_queue_s: number; finished: boolean } | null }
export interface Story {
  statusNames: Record<string, string>
  sides: Record<'left' | 'right', { controller: string; emergencyGreen: boolean; totals: Record<string, number | null>; ambulances: StoryAmbulance[] }>
}
export interface Pair { mean_diff: number; ci95: [number, number]; pairs: number; better: number; worse: number; left_mean: number; right_mean: number; pct: number | null }
export type PairBlock = Record<string, Pair>
export interface Comparison {
  summary: {
    episodes: number; seeds: number[]; scenarios: string[]; arms?: Record<string, string>
    ml_influence: { decisions: number; changed: number; switchedEarlier: number; heldLonger: number }
    ml_vs_heuristic: PairBlock; heuristic_vs_fixed: PairBlock
    ml_ev_vs_heuristic?: PairBlock; ev_rule_effect_on_heuristic?: PairBlock; ev_rule_effect_on_ml?: PairBlock; ml_vs_heuristic_both_with_ev?: PairBlock
  }
}
export const LABEL: Record<string, string> = {
  fixed_time: 'Fixed-time (30 s)', queue_heuristic: 'Queue heuristic · no ML', ml_forecast: 'Queue heuristic + ML forecast',
  queue_heuristic_ev: 'Queue heuristic + ambulance green', ml_forecast_ev: 'ML forecast + ambulance green',
}
export const SHORT: Record<string, string> = { fixed_time: 'Fixed-time', queue_heuristic: 'No ML', ml_forecast: 'ML', queue_heuristic_ev: 'No ML + ambulance green', ml_forecast_ev: 'ML + ambulance green' }
export const STATUS_COLOR = ['#34d399', '#f43f5e', '#f59e0b', '#22d3ee']
export const STATUS_TEXT = ['moving', 'stopped at a red light', 'stuck behind queued cars', 'moving, green light ahead']
