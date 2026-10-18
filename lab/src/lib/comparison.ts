import { obsAt } from './interp'
import { KPI, type Frame, type RunRec, type DiffRow } from './types'

export type ChartMetric = 'queue' | 'wait' | 'throughput'
export const CHART_METRICS: Record<
  ChartMetric,
  { title: string; label: string; note: string }
> = {
  queue: {
    title: 'How the queues build',
    label: 'Vehicles stopped on approaches',
    note: 'Measured total queue, sampled every five seconds.',
  },
  wait: {
    title: 'How waiting changes',
    label: 'Mean current waiting time · seconds',
    note: 'Average delay here means SUMO current waiting time across vehicles in the network. It is distinct from completed-trip waiting time and travel-time loss.',
  },
  throughput: {
    title: 'How traffic gets through',
    label: 'Arrivals per simulated hour',
    note: 'Cumulative arrivals ÷ elapsed demand time × 3,600. Stops updating after the 900-second demand window.',
  },
}
/** The recorded flag is 1 only when SUMO reports positive current waiting time. */
export function delayedVehicles(frame: Frame | undefined): number | undefined {
  return frame?.v.reduce((sum, vehicle) => sum + (vehicle[6] === 1 ? 1 : 0), 0)
}
export function countDifference(
  left: number | undefined,
  right: number | undefined,
): DiffRow | undefined {
  return left === undefined || right === undefined
    ? undefined
    : {
        key: 'delayed_now',
        label: 'Delayed vehicles',
        unit: 'veh',
        left,
        right,
        diff: right - left,
        pct: null,
        verdict:
          right === left
            ? 'same'
            : right < left
              ? 'right better'
              : 'left better',
      }
}
export function chartValue(
  run: Pick<RunRec, 'frames'>,
  frame: Frame,
  metric: ChartMetric,
): number {
  if (metric === 'throughput') {
    const end = Math.min(frame.t, 900)
    const observed = frameAt(run.frames, end)
    return end > 0 && observed ? (observed.k[KPI.arrived] / end) * 3600 : 0
  }
  return frame.k[metric === 'queue' ? KPI.halting : KPI.meanWaitNow]
}

/** Replays start at SUMO t=1, not array index 0. Use measured timestamps everywhere. */
export function frameAt(frames: Frame[], time: number): Frame | undefined {
  return obsAt(frames, time)
}
export function framePair(run: Pick<RunRec, 'frames'>, time: number) {
  const frame = frameAt(run.frames, time)
  const next = frame && frameAt(run.frames, frame.t + 1)
  return {
    frame,
    next,
    fraction:
      frame && next && next.t > frame.t
        ? Math.min(1, (time - frame.t) / (next.t - frame.t))
        : 0,
  }
}
export function signed(value: number, digits = 1): string {
  const rounded = Number(value.toFixed(digits))
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}`
}
/** A small measured change must never be relabeled as equality after rounding. */
export function displayPrecision(diff: number, digits: number): number {
  return diff !== 0 && Number(diff.toFixed(digits)) === 0
    ? Math.max(2, digits)
    : digits
}
export function waitFinding(diff: number): string {
  return diff < 0
    ? 'Less waiting in this run'
    : diff > 0
      ? 'More waiting in this run'
      : 'No difference in this run'
}
