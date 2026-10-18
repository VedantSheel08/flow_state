// Constants shared by the Results tab and the live comparison.
import { results, type ControllerId, type MetricKey } from './controllerResults'

export const COLOR: Record<ControllerId, string> = { queue_heuristic: '#2a78d6', fixed_time: '#eb6834', rl_ppo: '#1baf7a' }
export const BASE = results.baseline
export const FS = results.flowstate

export const HERO: MetricKey[] = ['mean_wait_s', 'mean_travel_s', 'queue_max_veh', 'throughput_vph']

export const card = 'rounded-[12px] border border-transparent bg-surface shadow-card'

export const signed = (p: number, digits = 1) => `${p > 0 ? '+' : p < 0 ? '−' : ''}${Math.abs(p).toFixed(digits)}%`
