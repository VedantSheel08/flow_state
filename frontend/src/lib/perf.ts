// Lightweight runtime profiler. Recording costs a few performance.now() calls
// per frame, so it stays on; the numbers feed the dev perf overlay and the
// profiling scripts (window.__fs.perf in dev builds).

class Series {
  private buf: Float64Array
  private n = 0
  private i = 0
  constructor(size = 600) {
    this.buf = new Float64Array(size)
  }
  push(v: number) {
    this.buf[this.i] = v
    this.i = (this.i + 1) % this.buf.length
    if (this.n < this.buf.length) this.n++
  }
  clear() {
    this.n = this.i = 0
  }
  values(): number[] {
    const out: number[] = []
    for (let k = 0; k < this.n; k++) out.push(this.buf[(this.i - this.n + k + this.buf.length) % this.buf.length])
    return out
  }
  stats() {
    const v = this.values().sort((a, b) => a - b)
    if (!v.length) return { n: 0, mean: 0, p50: 0, p95: 0, p99: 0, max: 0 }
    const q = (p: number) => v[Math.min(v.length - 1, Math.floor(p * v.length))]
    return { n: v.length, mean: v.reduce((a, b) => a + b, 0) / v.length, p50: q(0.5), p95: q(0.95), p99: q(0.99), max: v[v.length - 1] }
  }
  last(): number {
    return this.n ? this.buf[(this.i - 1 + this.buf.length) % this.buf.length] : 0
  }
}

export interface WsCounter {
  count: number
  bytes: number
  ms: number
}

class Perf {
  frameDt = new Series() // wall time between animation frames (ms)
  renderMs = new Series() // canvas draw (ms)
  interpMs = new Series() // frame-buffer interpolation (ms)
  vehicles = new Series()
  visible = new Series() // vehicles actually drawn (inside the view)
  ws: Record<string, WsCounter> = {}
  reactCommits = 0
  reactMs = 0
  longTasks: { start: number; duration: number }[] = []
  since = performance.now()
  private observer: PerformanceObserver | null = null

  constructor() {
    try {
      this.observer = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          this.longTasks.push({ start: e.startTime, duration: e.duration })
          if (this.longTasks.length > 200) this.longTasks.shift()
        }
      })
      this.observer.observe({ type: 'longtask', buffered: false })
    } catch {
      /* long-task timing unsupported (Firefox/Safari) */
    }
  }

  wsMessage(type: string, bytes: number, ms: number) {
    const c = (this.ws[type] ??= { count: 0, bytes: 0, ms: 0 })
    c.count++
    c.bytes += bytes
    c.ms += ms
  }

  reactCommit(ms: number) {
    this.reactCommits++
    this.reactMs += ms
  }

  reset() {
    for (const s of [this.frameDt, this.renderMs, this.interpMs, this.vehicles, this.visible]) s.clear()
    this.ws = {}
    this.reactCommits = 0
    this.reactMs = 0
    this.longTasks = []
    this.since = performance.now()
  }

  /** Summary over the window since the last reset. */
  report() {
    const secs = Math.max(0.001, (performance.now() - this.since) / 1000)
    const dt = this.frameDt.stats()
    const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
    const ws: Record<string, { perSec: number; kbPerSec: number; msPerMsg: number }> = {}
    for (const [k, c] of Object.entries(this.ws)) ws[k] = { perSec: +(c.count / secs).toFixed(2), kbPerSec: +(c.bytes / 1024 / secs).toFixed(1), msPerMsg: +(c.ms / Math.max(1, c.count)).toFixed(3) }
    const r = (x: number, d = 2) => +x.toFixed(d)
    return {
      seconds: r(secs, 1),
      fps: dt.mean ? r(1000 / dt.mean, 1) : 0,
      frameMs: { p50: r(dt.p50), p95: r(dt.p95), p99: r(dt.p99), max: r(dt.max) },
      jankFrames: this.frameDt.values().filter((v) => v > 50).length,
      renderMs: (({ mean, p95, max }) => ({ mean: r(mean), p95: r(p95), max: r(max) }))(this.renderMs.stats()),
      interpMs: (({ mean, p95 }) => ({ mean: r(mean, 3), p95: r(p95, 3) }))(this.interpMs.stats()),
      vehicles: r(this.vehicles.stats().mean, 0),
      visible: r(this.visible.stats().mean, 0),
      ws,
      react: { commitsPerSec: r(this.reactCommits / secs, 1), msPerSec: r(this.reactMs / secs) },
      longTasks: { count: this.longTasks.length, totalMs: r(this.longTasks.reduce((a, t) => a + t.duration, 0), 0), maxMs: r(Math.max(0, ...this.longTasks.map((t) => t.duration)), 0) },
      heapMB: mem ? r(mem.usedJSHeapSize / 1048576, 1) : null,
    }
  }

  get live() {
    return { fps: this.frameDt.last() ? 1000 / this.frameDt.last() : 0, renderMs: this.renderMs.last(), vehicles: this.vehicles.last() }
  }
}

export const perf = new Perf()
