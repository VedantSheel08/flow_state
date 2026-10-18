import { describe, expect, it } from 'vitest'
import { evState, frameAt, gunzipJson, locate, sample, spriteCode, windowFor, type DemoAsset, type DemoFrame } from '../lib/flowstateDemo'

const frames: DemoFrame[] = [
  { t: 10, v: [[1, 1000, 2000, 90, 0, 100], [2, 0, 0, 350, 2, 0]], s: ['GGGg'], e: [[2, 1, 123]] },
  { t: 11, v: [[1, 1100, 2000, 90, 0, 140], [2, 0, 0, 10, 2, 0]], s: ['rrrr'] },
  { t: 12, v: [[1, 1200, 2000, 90, 0, 140]], s: ['rrrr'] },
]

describe('frameAt / sample', () => {
  it('interpolates between one-second frames along the recorded heading and speed (Hermite)', () => {
    const [a] = sample(frames, 10.5)
    expect(a.id).toBe(1)
    expect(a.x).toBeCloseTo(104.5, 5) // 0.5·100 + 0.125·10 + 0.5·110 − 0.125·14
    expect(a.y).toBeCloseTo(200, 5)
    expect(a.speed).toBe(12)
  })
  it('takes the short way round the compass', () => {
    const amb = sample(frames, 10.5).find((p) => p.id === 2)!
    expect(((amb.angle % 360) + 360) % 360).toBeCloseTo(0, 5)
  })
  it('clamps outside the recording', () => {
    expect(frameAt(frames, 99).a.t).toBe(12)
    expect(sample(frames, -5).map((p) => p.id)).toEqual([1, 2])
  })
  it('drops a car that has left by the next frame only after its own frame', () => {
    expect(locate(frames, 2, 11.5)).not.toBeNull()
    expect(locate(frames, 2, 12)).toBeNull()
  })
})

describe('evState', () => {
  it('reads the measured status of the ambulance', () => {
    expect(evState(frames, 2, 10.2)).toEqual({ status: 1, metresToSignal: 12.3 })
    expect(evState(frames, 2, 11.2)).toBeNull()
  })
})

describe('windowFor', () => {
  const unit = (first: number, last: number) => ({ travelS: 0, stoppedRedS: 0, stoppedQueueS: 0, finished: true, departS: 0, first, last })
  const asset = { left: { frames }, right: { frames }, ambulances: [{ id: 7, left: unit(11, 12), right: unit(10, 11), savedS: 1 }] } as unknown as DemoAsset
  it('covers both journeys, clamped to the recording', () => {
    expect(windowFor(asset, 7, 8)).toEqual([10, 12])
    expect(windowFor(asset, 7, 0)).toEqual([10, 12])
  })
  it('falls back to the whole recording for an unknown ambulance', () => {
    expect(windowFor(asset, 99)).toEqual([10, 12])
  })
})

describe('spriteCode', () => {
  it('maps the ambulance and trucks to their sprites', () => {
    expect(spriteCode({ id: 3, x: 0, y: 0, angle: 0, speed: 0, type: 2 })).toBe(5)
    expect(spriteCode({ id: 3, x: 0, y: 0, angle: 0, speed: 0, type: 1 })).toBe(2)
  })
})

describe('gunzipJson', () => {
  const doc = { a: 1, b: ['x'] }
  it('unpacks gzip bytes', async () => {
    const packed = new Response(new Response(JSON.stringify(doc)).body!.pipeThrough(new CompressionStream('gzip')))
    expect(await gunzipJson(packed)).toEqual(doc)
  })
  it('accepts a body the browser has already unpacked', async () => {
    expect(await gunzipJson(new Response(JSON.stringify(doc)))).toEqual(doc)
  })
})
