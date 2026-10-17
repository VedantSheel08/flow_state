import { apiUrl } from './endpoints'
import type { CompareJob, CongestionPreset, JunctionDetail, Kpi, NetworkGeom, RunInfo, RunRecord, RunStatus, ScenarioSpec, SimEvent, VehicleDetail } from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(apiUrl(path), {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'The simulation server is not reachable.')
  }
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, msg)
  }
  return res.json() as Promise<T>
}

export interface Health {
  ok: boolean
  sumo: string
  backend: string
  speeds: number[]
  signalModes: Record<string, string>
}

export interface Snapshot {
  run: RunInfo | null
  status: RunStatus
  events?: SimEvent[]
}

export const api = {
  health: () => req<Health>('GET', '/api/health'),
  scenarios: () => req<ScenarioSpec[]>('GET', '/api/scenarios'),
  network: (id: string) => req<NetworkGeom>('GET', `/api/networks/${id}`),
  congestionPresets: () => req<CongestionPreset[]>('GET', '/api/congestion-presets'),
  startRun: (scenario: string, params: Record<string, unknown>, seed: number | null, speed: number, congestion?: string | null) =>
    req<RunInfo>('POST', '/api/runs', congestion ? { scenario, params, seed, speed, congestion } : { scenario, params, seed, speed }),
  runs: () => req<RunRecord[]>('GET', '/api/runs'),
  current: () => req<Snapshot>('GET', '/api/runs/current'),
  control: (action: 'pause' | 'resume' | 'restart' | 'stop' | 'speed', speed?: number) =>
    req<RunStatus | RunInfo>('POST', '/api/runs/current/control', { action, speed }),
  liveParams: (params: Record<string, unknown>) => req<Record<string, unknown>>('POST', '/api/runs/current/params', params),
  metrics: () => req<Kpi[]>('GET', '/api/runs/current/metrics'),
  vehicle: (id: string) => req<VehicleDetail>('GET', `/api/runs/current/vehicles/${encodeURIComponent(id)}`),
  junction: (id: string) => req<JunctionDetail>('GET', `/api/runs/current/junctions/${encodeURIComponent(id)}`),
  compare: (scenario: string, params: Record<string, unknown>, seed: number, key: string, values: unknown[]) =>
    req<CompareJob>('POST', '/api/compare', { scenario, params, seed, key, values }),
  compareJob: (id: string) => req<CompareJob>('GET', `/api/compare/${id}`),
}
