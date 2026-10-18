import { useEffect, useState } from 'react'
import { fmt, getJSON } from '../lib/api'

type T = Record<string, { mean_ms: number; p95_ms: number; n: number }>
interface Perf {
  live: { status: string; clients: number; framesSent: number; wsSendMs: { mean: number; p95: number } }
  workerTiming?: T; lastRun?: { dropped?: number }; forecastInference?: { mean: number; p95: number }
}
interface Rep { left: { controller: string; timing: T; wall_s: number }; right: { controller: string; timing: T; wall_s: number }; id: string }

export default function PerfView({ replay }: { replay: string | null }) {
  const [p, setP] = useState<Perf | null>(null)
  const [r, setR] = useState<Rep | null>(null)
  useEffect(() => {
    const load = () => getJSON<Perf>('/api/lab/perf').then(setP).catch(() => undefined)
    load()
    const iv = setInterval(load, 2000)
    return () => clearInterval(iv)
  }, [])
  useEffect(() => { if (replay) getJSON<Rep>(`/api/lab/replays/${replay}`).then(setR).catch(() => undefined) }, [replay])
  const row = (name: string, t?: { mean_ms: number; p95_ms: number; n?: number }) => <tr key={name}><td>{name}</td><td className="num">{fmt(t?.mean_ms, 3)}</td><td className="num">{fmt(t?.p95_ms, 3)}</td><td className="num">{t?.n ?? '—'}</td></tr>
  return (
    <div className="two">
      <div className="card">
        <h4>Live session <small>refreshes every 2 s</small></h4>
        <table className="tbl"><thead><tr><th>Stage</th><th className="num">mean ms</th><th className="num">p95 ms</th><th className="num">samples</th></tr></thead>
          <tbody>
            {row('SUMO step (1 s simulated)', p?.workerTiming?.sumo_step)}
            {row('TrafficState observation (16 approaches)', p?.workerTiming?.observation)}
            {row('ML inference (per decision)', p?.workerTiming?.inference)}
            {row('WebSocket frame send', p ? { mean_ms: p.live.wsSendMs.mean, p95_ms: p.live.wsSendMs.p95, n: p.live.framesSent } : undefined)}
          </tbody></table>
        <p className="small">Status {p?.live.status ?? '—'} · {p?.live.clients ?? 0} client(s) · frames sent {p?.live.framesSent ?? 0} · frames dropped by back-pressure {p?.lastRun?.dropped ?? 0}</p>
        <p className="small muted">SUMO runs in a separate worker process; Gemini calls run in a thread pool; recordings in a 2-process pool. None of them can block the event loop or the simulation.
          Frames are bounded (queue of 256); if the browser falls behind, old frames are dropped rather than slowing SUMO.</p>
      </div>
      <div className="card">
        <h4>Recorded pair <small>{r?.id}</small></h4>
        {r && (['left', 'right'] as const).map((s) => <div key={s}><b className="small">{s}: {r[s].controller} · whole run {r[s].wall_s}s wall</b>
          <table className="tbl"><tbody>{row('SUMO step', r[s].timing.sumo_step)}{row('Observation', r[s].timing.observation)}{row('ML inference', r[s].timing.inference)}</tbody></table></div>)}
        <p className="small">Offline forecaster benchmark: {fmt(p?.forecastInference?.mean, 3)} ms mean, {fmt(p?.forecastInference?.p95, 3)} ms p95 per decision.</p>
      </div>
    </div>
  )
}
