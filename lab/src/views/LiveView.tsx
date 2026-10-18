import { useCallback, useEffect, useRef, useState } from 'react'
import Chart from '../components/Chart'
import MapCanvas, { type DrawData } from '../components/MapCanvas'
import { fmt, wsURL } from '../lib/api'
import { interpolate } from '../lib/interp'
import {
  CONTROLLER_LABEL,
  KPI,
  type Frame,
  type Geometry,
  type Obs,
} from '../lib/types'

interface Props {
  geom: Geometry
  scenarios: { id: string; title: string; description: string }[]
  mlAvailable: boolean
  onSnapshot: (live: boolean) => void
}
interface ObsMsg extends Obs {
  mlChanged?: number[]
  mlStats?: { decisions: number; changed: number }
  timing?: Record<string, { mean_ms: number; p95_ms: number }>
}

export default function LiveView({
  geom,
  scenarios,
  mlAvailable,
  onSnapshot,
}: Props) {
  const [cfg, setCfg] = useState({
    scenario: 'rush_ramp',
    controller: 'queue_heuristic',
    seed: 3100,
    intensity: 1.18,
    speed: 4,
  })
  const [status, setStatus] = useState<{ status: string; paused: boolean }>({
    status: 'idle',
    paused: false,
  })
  const [err, setErr] = useState<string | null>(null)
  const [started, setStarted] = useState<{
    manifest?: Record<string, unknown>
    controller?: Record<string, unknown>
  } | null>(null)
  const [end, setEnd] = useState<Record<string, unknown> | null>(null)
  const [sel, setSel] = useState<number | null>(null)
  const [connected, setConnected] = useState(false)
  const [, setTick] = useState(0)
  const ws = useRef<WebSocket | null>(null)
  const fr = useRef<{
    a: Frame | null
    b: Frame | null
    at: number
    dt: number
  }>({ a: null, b: null, at: 0, dt: 250 })
  const obs = useRef<ObsMsg[]>([])
  const kpis = useRef<[number, number[]][]>([])
  const mlFlash = useRef<{ j: number[]; until: number }>({ j: [], until: 0 })
  const sid = useRef(-1)

  useEffect(() => {
    let alive = true
    let retry: ReturnType<typeof setTimeout> | undefined
    const connect = () => {
      const s = new WebSocket(wsURL('/ws/live'))
      ws.current = s
      s.onopen = () => {
        if (alive) setConnected(true)
      }
      s.onmessage = (ev) => {
        const m = JSON.parse(ev.data)
        if (m.type === 'started') sid.current = m.sid
        else if (
          m.sid !== undefined &&
          m.sid !== sid.current &&
          m.type !== 'state'
        )
          return // tail of a replaced session
        if (m.type === 'frame') {
          const now = performance.now(),
            f = fr.current
          f.dt = f.b ? Math.min(1000, Math.max(16, now - f.at)) : 250
          f.a = f.b ?? m
          f.b = m
          f.at = now
          if (m.t % 5 === 0) kpis.current.push([m.t, m.k])
        } else if (m.type === 'obs') {
          obs.current.push(m)
          if (m.mlChanged?.length)
            mlFlash.current = {
              j: m.mlChanged,
              until: performance.now() + 1500,
            }
        } else if (m.type === 'state')
          setStatus({ status: m.status, paused: m.paused })
        else if (m.type === 'started') {
          setStarted(m)
          setStatus({ status: 'running', paused: false })
          setErr(null)
        } else if (m.type === 'done' || m.type === 'stopped') {
          setEnd(m)
          setStatus({ status: 'finished', paused: false })
        } else if (m.type === 'error') {
          setErr(m.error)
          setStatus((p) => ({ ...p, status: 'error' }))
        }
      }
      s.onclose = () => {
        if (alive) {
          setConnected(false)
          retry = setTimeout(connect, 1000)
        }
      }
    }
    connect()
    const iv = setInterval(() => setTick((t) => t + 1), 300)
    return () => {
      alive = false
      clearInterval(iv)
      clearTimeout(retry)
      ws.current?.close()
    }
  }, [])

  const send = (m: object) =>
    ws.current?.readyState === 1 && ws.current.send(JSON.stringify(m))
  const start = () => {
    fr.current = { a: null, b: null, at: 0, dt: 250 }
    obs.current = []
    kpis.current = []
    setEnd(null)
    setErr(null)
    setStarted(null)
    send({ cmd: 'start', ...cfg })
    setStatus({ status: 'starting', paused: false })
    onSnapshot(true)
  }

  const source = useCallback((): DrawData | null => {
    const f = fr.current
    if (!f.b) return null
    const k = Math.min(1, (performance.now() - f.at) / f.dt)
    const last = obs.current[obs.current.length - 1]
    return {
      vehicles: f.a && f.a !== f.b ? interpolate(f.a, f.b, k) : f.b.v,
      signals: f.b.s,
      queue: last?.q,
      forecast: last?.p30,
      selected: sel,
      mlJunctions:
        performance.now() < mlFlash.current.until ? mlFlash.current.j : [],
    }
  }, [sel])

  const cur = fr.current.b
  const last = obs.current[obs.current.length - 1]
  // live forecast check: compare the prediction made 30 s / 60 s ago with what SUMO measures now (honest running error)
  const check = (h: number, key: 'p30' | 'p60') => {
    const byT = new Map(obs.current.map((o) => [o.t, o]))
    let s = 0,
      sp = 0,
      n = 0
    for (const o of obs.current) {
      const later = byT.get(o.t + h)
      const p = o[key]
      if (!later || !p) continue
      p.forEach((v, i) => {
        s += Math.abs(v - later.q[i])
        sp += Math.abs(o.q[i] - later.q[i])
        n++
      })
    }
    return n ? { mae: s / n, pers: sp / n, n } : null
  }
  const c30 = check(30, 'p30'),
    c60 = check(60, 'p60')
  const ml = last?.mlStats
  const selKey = sel !== null ? geom.approaches[sel]?.key : null
  const kp = cur?.k
  const running = status.status === 'running' || status.status === 'starting'
  const activeController =
    (started?.controller as { used?: string })?.used ?? cfg.controller

  return (
    <div className="live">
      <div className="toolbar">
        <label>
          Scenario
          <select
            value={cfg.scenario}
            onChange={(e) => setCfg({ ...cfg, scenario: e.target.value })}
          >
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Controller
          <select
            value={cfg.controller}
            onChange={(e) => setCfg({ ...cfg, controller: e.target.value })}
          >
            {Object.entries(CONTROLLER_LABEL).map(([k, v]) => (
              <option
                key={k}
                value={k}
                disabled={k === 'ml_forecast' && !mlAvailable}
              >
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          Seed
          <input
            type="number"
            value={cfg.seed}
            onChange={(e) => setCfg({ ...cfg, seed: +e.target.value })}
          />
        </label>
        <label>
          Demand
          <select
            value={cfg.intensity}
            onChange={(e) => setCfg({ ...cfg, intensity: +e.target.value })}
          >
            <option value={0.7}>Light (0.7×)</option>
            <option value={1}>Standard (1.0×)</option>
            <option value={1.18}>Stress (1.18×)</option>
          </select>
        </label>
        <button className="primary" disabled={!connected} onClick={start}>
          {running ? 'Restart' : 'Start'}
        </button>
        <button
          disabled={!running || !connected}
          onClick={() => send({ cmd: status.paused ? 'resume' : 'pause' })}
        >
          {status.paused ? 'Resume' : 'Pause'}
        </button>
        <button
          disabled={!running || !connected}
          onClick={() => send({ cmd: 'stop' })}
        >
          Stop
        </button>
        <label>
          Speed
          <select
            value={cfg.speed}
            onChange={(e) => {
              setCfg({ ...cfg, speed: +e.target.value })
              send({ cmd: 'speed', value: +e.target.value })
            }}
          >
            {[1, 2, 4, 8, 16].map((s) => (
              <option key={s} value={s}>
                {s}×
              </option>
            ))}
          </select>
        </label>
        <span className={`pill ${status.status}`}>
          {status.paused ? 'paused' : status.status}
        </span>
      </div>
      {!connected && (
        <p className="error" role="status">
          Connecting to the live simulation…
        </p>
      )}
      {err && <div className="error">{err}</div>}
      <div className="live-grid">
        <div className="map-wrap">
          <div className="section-heading">
            <h2>Live traffic</h2>
            <span>
              {CONTROLLER_LABEL[activeController]}{' '}
              · {cur?.t ?? 0}s
            </span>
          </div>
          <MapCanvas
            geom={geom}
            source={source}
            onPick={setSel}
            label={`${CONTROLLER_LABEL[activeController]} · t = ${cur?.t ?? 0} s`}
            accent={activeController === 'ml_forecast' ? '#39bca6' : '#8c9eab'}
          />
          <div className="map-legend">
            <span>
              <i style={{ background: '#8c9eab' }} />
              moving
            </span>
            <span>
              <i style={{ background: '#e9a85d' }} />
              slow
            </span>
            <span>
              <i style={{ background: '#e06f64' }} />
              stopped
            </span>
            <span>
              <i className="bar" style={{ background: '#e06f64' }} />
              measured queue
            </span>
            <span>
              <i className="bar dash" style={{ borderColor: '#65afa1' }} />
              forecast queue (+30 s)
            </span>
            {activeController === 'ml_forecast' && (
              <span>
                <i className="ring" />
                ML changed a decision
              </span>
            )}
          </div>
        </div>
        <div className="side">
          <div className="tiles">
            <Tile label="Vehicles in network" v={kp?.[KPI.inNetwork]} d={0} />
            <Tile label="Halted on approaches" v={kp?.[KPI.halting]} d={0} />
            <Tile label="Mean current wait" v={kp?.[KPI.meanWaitNow]} u="s" />
            <Tile label="Arrived" v={kp?.[KPI.arrived]} d={0} />
            <Tile label="CO₂ so far (est.)" v={kp?.[KPI.co2Kg]} u="kg" />
            <Tile
              label="Teleports / collisions"
              text={kp ? `${kp[KPI.teleports]} / ${kp[KPI.collisions]}` : '—'}
            />
          </div>
          <div className="card">
            <h4>
              Network queue <small>measured, every 5 s</small>
            </h4>
            <Chart
              height={110}
              series={[
                {
                  name: 'halted vehicles',
                  color: '#3a9c87',
                  points: kpis.current.map(([t, k]) => [t, k[KPI.halting]]),
                },
              ]}
            />
          </div>
          <details className="disclosure">
            <summary>
              Live technical details{' '}
              <span>Forecasts, approach data & timing</span>
            </summary>
            <div className="disclosure-body">
              <div className="card">
                <h4>
                  Forecast vs reality{' '}
                  <small>
                    {started?.controller &&
                    (started.controller as { mlInControl?: boolean })
                      .mlInControl
                      ? 'forecast is used by the controller'
                      : 'shadow mode: shown, not used for control'}
                  </small>
                </h4>
                {c30 ? (
                  <p className="small">
                    Live check (this run, {c30.n} approach-samples): 30 s
                    forecast MAE <b>{fmt(c30.mae, 2)}</b> veh vs persistence{' '}
                    {fmt(c30.pers, 2)}
                    {c60 && (
                      <>
                        {' '}
                        · 60 s MAE <b>{fmt(c60.mae, 2)}</b> vs{' '}
                        {fmt(c60.pers, 2)}
                      </>
                    )}
                  </p>
                ) : (
                  <p className="small muted">
                    Accumulating: the first check needs 30 s of simulated time.
                  </p>
                )}
                {sel !== null && (
                  <Chart
                    height={110}
                    yLabel={`${selKey} halted vehicles`}
                    series={[
                      {
                        name: 'measured',
                        color: '#9aabb6',
                        points: obs.current.map((o) => [o.t, o.q[sel]]),
                      },
                      {
                        name: 'predicted (+30 s, plotted at target time)',
                        color: '#3a9c87',
                        dash: true,
                        points: obs.current
                          .filter((o) => o.p30)
                          .map((o) => [o.t + 30, o.p30![sel]]),
                      },
                    ]}
                  />
                )}
                <ApproachTable
                  geom={geom}
                  last={last}
                  sel={sel}
                  onSel={setSel}
                />
              </div>
              {ml && (
                <div className="card ml">
                  <h4>ML influence</h4>
                  <p className="small">
                    The forecast changed <b>{ml.changed}</b> of {ml.decisions}{' '}
                    eligible signal decisions (vs. what the plain heuristic
                    would have done). SafeSignal limits applied to all.
                  </p>
                </div>
              )}
              {last?.timing && (
                <div className="card">
                  <h4>Worker timing</h4>
                  <p className="small mono">
                    SUMO step {fmt(last.timing.sumo_step?.mean_ms, 2)} ms (p95{' '}
                    {fmt(last.timing.sumo_step?.p95_ms, 2)}) · observation{' '}
                    {fmt(last.timing.observation?.mean_ms, 2)} ms · inference{' '}
                    {fmt(last.timing.inference?.mean_ms, 2)} ms
                  </p>
                </div>
              )}
            </div>
          </details>
          {end && (
            <div className="card">
              <h4>Run finished</h4>
              <Metrics
                m={(end as { metrics: Record<string, number> }).metrics}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Tile({
  label,
  v,
  u,
  d = 1,
  text,
}: {
  label: string
  v?: number
  u?: string
  d?: number
  text?: string
}) {
  return (
    <div className="tile">
      <div className="tl">{label}</div>
      <div className="tv">
        {text ?? fmt(v, d)}
        {u && v !== undefined && <small> {u}</small>}
      </div>
    </div>
  )
}

export function Metrics({ m }: { m: Record<string, number> }) {
  const rows: [string, string, number][] = [
    ['Mean wait', 's', m.mean_wait_s],
    ['Mean travel', 's', m.mean_travel_s],
    ['Throughput', 'veh/h', m.throughput_vph],
    ['Completed', '', m.vehicles_completed],
    ['CO₂ (HBEFA3 est.)', 'kg', m.co2_kg],
    ['Teleports', '', m.teleports],
    ['Collisions', '', m.collisions],
  ]
  return (
    <table className="tbl">
      <tbody>
        {rows.map(([l, u, v]) => (
          <tr key={l}>
            <td>{l}</td>
            <td className="num">
              {fmt(v, 1)} {u}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ApproachTable({
  geom,
  last,
  sel,
  onSel,
}: {
  geom: Geometry
  last?: Obs
  sel: number | null
  onSel: (i: number) => void
}) {
  if (!last) return null
  const rows = geom.approaches
    .map((a, i) => ({
      a,
      i,
      q: last.q[i],
      p30: last.p30?.[i],
      p60: last.p60?.[i],
    }))
    .sort((x, y) => (y.p30 ?? y.q) - (x.p30 ?? x.q))
    .slice(0, 8)
  return (
    <table className="tbl click">
      <thead>
        <tr>
          <th>Approach</th>
          <th className="num">Measured now</th>
          <th className="num">Forecast +30 s</th>
          <th className="num">+60 s</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ a, i, q, p30, p60 }) => {
          const risk = (p30 ?? 0) >= a.cap * 0.5 || (p60 ?? 0) >= a.cap * 0.5
          const now = q >= a.cap * 0.5
          return (
            <tr
              key={a.key}
              className={sel === i ? 'sel' : ''}
              onClick={() => onSel(i)}
            >
              <td>
                {a.key}
                {a.entry ? ' (entry)' : ''}
              </td>
              <td className="num">{fmt(q, 0)}</td>
              <td className="num pred">{fmt(p30, 1)}</td>
              <td className="num pred">{fmt(p60, 1)}</td>
              <td>
                {now ? (
                  <span className="tag bad">bottleneck now</span>
                ) : risk ? (
                  <span className="tag warn">spillback risk (forecast)</span>
                ) : (
                  <span className="tag ok">ok</span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
