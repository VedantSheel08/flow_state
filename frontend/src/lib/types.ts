// Shapes of the FlowState API. Everything here originates from SUMO.

export type ParamType = 'int' | 'float' | 'select' | 'bool' | 'edge'

export interface ParamSpec {
  key: string
  label: string
  type: ParamType
  default: unknown
  min?: number | null
  max?: number | null
  step?: number | null
  options?: { value: string | number; label: string }[] | null
  unit?: string | null
  description?: string | null
  live: boolean
  group: string
}

export interface ScenarioSpec {
  id: string
  title: string
  tagline: string
  description: string
  category: string
  params: ParamSpec[]
  networks: string[]
  measures: string[]
  assumptions: string[]
}

export interface BriefingItem {
  label: string
  value: string
  detail?: string | null
}

export interface Marker {
  t: number
  label: string
  kind: string
}

/** Where a scenario component acts on the map; edges are resolved against the scene. */
export interface Overlay {
  id: string
  kind: 'point' | 'zone' | 'route' | 'weather'
  tone: 'incident' | 'emergency' | 'demand' | 'transit' | 'environment' | string
  label: string
  edges: string[]
  pos?: number | null
  tStart?: number | null
  tEnd?: number | null
  color?: string | null
  condition?: string | null
  detail?: string | null
}

/** GET /api/congestion-presets (backend-performance contract, version 1). */
export interface CongestionPreset {
  id: string
  label: string
  version: number
  /** Scenario parameters the preset supplies; explicit run params override them. */
  params: Record<string, number>
  /** Warm-up seconds before the observation clock starts. */
  warmup: number
  /** Commuter peak multiplier applied when the scenario is rush_hour. */
  rushHourPeak?: number
  stressTest: boolean
  calibrated: boolean
  safetyValidated: boolean
  description?: string
}

export interface RunInfo {
  id: string
  scenario: string
  title: string
  seed: number
  params: Record<string, unknown>
  network: string
  duration: number | null
  briefing: BriefingItem[]
  markers: Marker[]
  /** absent on backends that predate map overlays */
  overlays?: Overlay[]
  signalMode: string
  spec: ScenarioSpec
  created: number
  // present when the backend supports congestion presets
  warmup?: number
  congestion?: string | null
  congestionPreset?: CongestionPreset | null
}

export type RunState = 'idle' | 'starting' | 'warming' | 'running' | 'finished' | 'error'

export interface RunStatus {
  run: string | null
  state: RunState
  paused?: boolean
  speed?: number
  achieved?: number
  t?: number
  warmup?: number
  duration?: number | null
  error?: string | null
  stepMs?: number
}

export interface ModeSummary {
  trips: number
  travelTime: number | null
  timeLoss: number | null
  waiting: number | null
  speed: number | null
}

export interface Kpi {
  t: number
  active: number
  activeByMode: Record<string, number>
  persons: number
  completed: number
  throughput: number | null
  travelTime: number | null
  timeLoss: number | null
  waiting: number | null
  meanSpeed: number | null
  queued: number
  maxQueue: number
  pending: number
  teleports: number
  overall: ModeSummary
  queues?: Record<string, number>
}

export interface Incident {
  id: string
  kind: 'accident' | 'construction'
  edge: string
  lanes: number[]
  pos: number
  length: number
  tStart: number
  tEnd: number | null
  x: number
  y: number
  angle: number
  severity: string
  cleared: boolean
  closed: boolean
  description: string
}

export interface Tracked {
  id: string
  kind: 'ambulance' | 'police' | 'suspect' | 'bus' | 'responder'
  label: string
  dispatched: number
  arrived: number | null
  status: string
  [k: string]: unknown
}

export interface SimEvent {
  id: number
  t: number
  category: string
  severity: 'info' | 'success' | 'warning' | 'critical'
  message: string
  tls?: string | null
  edge?: string | null
  vehicle?: string | null
}

export interface StatsMessage {
  type: 'stats'
  run: string
  t: number
  kpi: Kpi
  edges: Record<string, [number, number, number]>
  incidents: Incident[]
  tracked: Tracked[]
  signals: { mode: string; tspExtensions: number; tspSeconds: number }
  scenario: Record<string, unknown>
  status: RunStatus
}


// [id, typeCode, x, y, angle(deg, 0 = north, clockwise), speed m/s, signals bitmask]
export type VehicleTuple = [string, number, number, number, number, number, number]
// [id, x, y, angle, speed]
export type PersonTuple = [string, number, number, number, number]

export interface FrameMessage {
  type: 'frame'
  run: string
  t: number
  v: VehicleTuple[]
  p: PersonTuple[]
  tls: Record<string, string>
}

export interface RunResults {
  t: number
  metrics: { departed: number; completed: number; byMode: Record<string, ModeSummary>; junctionMaxQueue: Record<string, number> }
  last: Kpi
  teleports: number
  collisions: number
  signals: { mode: string; tspExtensions: number; tspSeconds: number }
  scenario: Record<string, unknown>
  tracked: Tracked[]
  incidents: Incident[]
  history?: Kpi[]
  describe?: { briefing: BriefingItem[]; params: Record<string, unknown>; seed: number }
}

export interface RunRecord {
  id: string
  scenario: string
  title: string
  seed: number
  params: Record<string, unknown>
  network: string
  briefing: BriefingItem[]
  signalMode: string
  created: number
  summary: { t: number | null; completed: number | null; timeLoss: number | null }
}

export interface CompareJob {
  id: string
  scenario: string
  seed: number
  params: Record<string, unknown>
  key: string
  values: unknown[]
  state: 'running' | 'done' | 'error'
  variants: (RunResults | null)[]
  progress: number[]
}

export interface VehicleDetail {
  id: string
  type: string
  profile: string
  mode: string
  speed: number
  accel: number
  allowedSpeed: number
  road: string
  roadName: string
  lane: number
  route: string[]
  destination: string | null
  nextSignal: { junction: string; distance: number; state: string } | null
  leader: { id: string; gap: number } | null
  waiting: number | null
  timeLoss: number | null
  travelled: number | null
  departed: number | null
  tau: number
  maxAccel: number
  decel: number
  minGap: number
  speedFactor: number
  tracked: Tracked | null
}

export interface JunctionDetail {
  id: string
  name: string
  control: string
  approaches: { edge: string; heading: string; name: string; queue: number; vehicles: number; speed: number | null }[]
  signal?: {
    state: string
    phase: number
    program: string
    nextSwitch: number
    phases: { duration: number; state: string }[]
  }
  maxQueue: number
}

// ---- network geometry ----------------------------------------------------
export type LaneRole = 'car' | 'bike' | 'sidewalk' | 'median' | 'bus'

export interface LaneGeom {
  id: string
  i: number
  w: number
  role: LaneRole
  shape: number[]
  len: number
  dirs: string
  control: 'tls' | 'stop' | 'yield' | null
  tls: string | null
  links: number[]
}

export interface EdgeGeom {
  id: string
  from: string
  to: string
  name: string
  street: string
  cls: string
  kind: 'entry' | 'exit' | 'street'
  part: 'full' | 'main' | 'bay'
  lanes: LaneGeom[]
}

export interface JunctionGeom {
  id: string
  type: string
  x: number
  y: number
  shape: number[]
  name: string | null
}

export interface CityLayout {
  streets: { id: string; name: string; cls: string; axis: 'ns' | 'ew'; pos: number }[]
  blocks: { id: string; cell: [number, number]; use: string; shape: number[][]; outer: boolean }[]
  buildings: { shape: number[][]; h: number; use: string; tone: number }[]
  trees: [number, number, number][]
  features: Record<string, unknown>[]
  pois: { id: string; kind: string; name: string; x: number; y: number; entrance: [number, number]; access: string[] }[]
  labels: { text: string; x: number; y: number; angle: number; cls: string }[]
  busStops: { id: string; edge: string; lane: string; line: string; start: number; end: number }[]
  busLines: { id: string; name: string; street: string; color: string }[]
}

export interface NetworkGeom {
  id: string
  title: string
  description: string
  bounds: [number, number, number, number]
  roadBounds: [number, number, number, number]
  focus: [number, number]
  edges: EdgeGeom[]
  junctions: JunctionGeom[]
  crossings: { id: string; w: number; shape: number[]; tls: string | null; link: number | null; junction: string }[]
  walkingareas: { id: string; shape: number[] }[]
  tls: { id: string; phases: { d: number; s: string }[] }[]
  city: CityLayout
}

export const VEHICLE_TYPES = ['sedan', 'suv', 'truck', 'bus', 'bike', 'ambulance', 'police', 'suspect', 'wreck', 'hatch', 'van', 'pickup'] as const
export type VehicleType = (typeof VEHICLE_TYPES)[number]
