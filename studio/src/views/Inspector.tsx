import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import RoadMap, { type Scene } from '../render/RoadMap'
import GeminiPanel from './GeminiPanel'
import { fmt, getJSON, wsURL } from '../lib/api'
import { interpolate } from '../lib/interp'
import { LABEL, STATUS_TEXT, type Frame, type Geometry, type Pt } from '../lib/types'

export interface Meta {
  forecaster: { features: string[] | null; importance30s: Record<string, number> | null; kind: string | null }
  ppo: { checkpoint: string; validationPassed: boolean; validationReason: string | null; role: string; observationNames: string[]; groups: { name: string; from: number; to: number }[] }
  camera: { available: boolean; reason: string | null; footprintM: number }
  junctions: string[]
  gemini: { status: Record<string, unknown>; system: string; schema: unknown; inControlLoop: boolean; sees: string; neverSees: string }
  model: { version: string | null; sha256: string; calibrated: boolean }
}
interface Det { box: [number, number, number, number]; stopped: boolean | null; matchedTruth: boolean; estimatedVehicles: number }
interface Camera { available: boolean; reason?: string; junction?: string; size?: number; frame?: string; mask?: string; detections?: Det[]; truthVehiclesInView?: number; matched?: number; countEstimate?: number
  precision?: number | null; recall?: number | null; approaches?: { approach: string; cameraQueue: number; sumoQueue: number; error: number }[]; latencyMs?: number; method?: string; limits?: string; footprintM?: number }
interface Ppo { available: boolean; reason?: string; observation?: number[][]; probabilities?: number[][]; value?: number[]; ppoAction?: number[]; activeAction?: number[]; agrees?: number[]; latencyMs?: number; actionNames?: string[] }
interface Perception { forecaster: { features: string[]; approaches: string[]; matrix: number[][] }; ppo: Ppo; camera: Camera }
interface Cell { queue: number; interval?: { lo: number; hi: number } }
interface Intel { predicted: { approaches: { key: string; p30: Cell; p60: Cell }[] } | null; observed: { approaches: { key: string; queue: number; capacity: number }[] } }
interface ObsMsg { t: number; perception?: Perception; intel?: Intel; mlChanged?: number[] }

const HEAT = (v: number) => `rgba(56,189,248,${Math.max(0.04, Math.min(0.85, v))})`

export default function Inspector({ geom, meta }: { geom: Geometry; meta: Meta }) {
  const [cfg, setCfg] = useState({ scenario: 'rush_ramp', controller: 'ml_forecast_ev', seed: 3100, intensity: 1.18, speed: 4 })
  const [status, setStatus] = useState({ status: 'idle', paused: false })
  const [err, setErr] = useState<string | null>(null)
  const [obs, setObs] = useState<ObsMsg | null>(null)
  const [scenarios, setScenarios] = useState<{ id: string; title: string }[]>([])
  const [camJ, setCamJ] = useState('A0')
  const [agree, setAgree] = useState<{ n: number; same: number }[]>([])
  const ws = useRef<WebSocket | null>(null)
  const sid = useRef(-1)
  const fr = useRef<{ a: Frame | null; b: Frame | null; at: number; dt: number }>({ a: null, b: null, at: 0, dt: 250 })
  const ambHist = useRef<Map<number, { pts: [number, number, number][]; status: number; since: number }>>(new Map())
  const [, tick] = useState(0)

  useEffect(() => { getJSON<{ scenarios: { id: string; title: string }[] }>('/api/lab/status').then((s) => setScenarios(s.scenarios)).catch(() => undefined) }, [])
  useEffect(() => {
    let alive = true
    const connect = () => {
      const s = new WebSocket(wsURL('/ws/live'))
      ws.current = s
      s.onmessage = (ev) => {
        const m = JSON.parse(ev.data)
        if (m.type === 'started') sid.current = m.sid
        else if (m.sid !== undefined && m.sid !== sid.current && m.type !== 'state') return
        if (m.type === 'frame') {
          const now = performance.now(), f = fr.current
          f.dt = f.b ? Math.min(1000, Math.max(16, now - f.at)) : 250
          f.a = f.b ?? m; f.b = m; f.at = now
          for (const [id, st] of (m.e ?? []) as [number, number][]) {
            const h: { pts: [number, number, number][]; status: number; since: number } = ambHist.current.get(id) ?? { pts: [], status: st, since: m.t }
            if (h.status !== st) { h.status = st; h.since = m.t }
            const v = (m.v as number[][]).find((x) => x[0] === id)
            if (v) { h.pts.push([m.t, v[1] / 10, v[2] / 10]); while (h.pts.length && h.pts[0][0] < m.t - 50) h.pts.shift() }
            ambHist.current.set(id, h)
          }
        } else if (m.type === 'obs') {
          setObs(m)
          if (m.perception?.ppo?.agrees) setAgree((p) => (m.perception.ppo.agrees as number[]).map((a, i) => ({ n: (p[i]?.n ?? 0) + 1, same: (p[i]?.same ?? 0) + a })))
        } else if (m.type === 'state') setStatus({ status: m.status, paused: m.paused })
        else if (m.type === 'started') { setStatus({ status: 'running', paused: false }); setErr(null) }
        else if (m.type === 'done' || m.type === 'stopped') setStatus({ status: 'finished', paused: false })
        else if (m.type === 'error') { setErr(m.error); setStatus((p) => ({ ...p, status: 'error' })) }
      }
      s.onclose = () => { if (alive) setTimeout(connect, 1000) }
    }
    connect()
    const iv = setInterval(() => tick((x) => x + 1), 400)
    return () => { alive = false; clearInterval(iv); ws.current?.close() }
  }, [])

  const send = (m: object) => ws.current?.readyState === 1 && ws.current.send(JSON.stringify(m))
  const start = () => {
    fr.current = { a: null, b: null, at: 0, dt: 250 }; ambHist.current = new Map(); setObs(null); setAgree([]); setErr(null)
    send({ cmd: 'start', ...cfg, perception: true, camera: camJ }); setStatus({ status: 'starting', paused: false })
  }
  const pickCam = (j: string) => { setCamJ(j); send({ cmd: 'camera', junction: j }) }
  const running = status.status === 'running' || status.status === 'starting'

  const scene = useCallback((): Scene | null => {
    const f = fr.current
    if (!f.b) return null
    const k = Math.min(1, (performance.now() - f.at) / f.dt)
    const ambulances = (f.b.e ?? []).map(([id, st]) => { const h = ambHist.current.get(id); return { id, status: st, seconds: h ? f.b!.t - h.since + 1 : undefined, trail: h?.pts.map((p) => [p[1], p[2]] as Pt) } })
    const j = geom.junctions.find((x) => x.id === camJ)
    return { vehicles: f.a && f.a !== f.b ? interpolate(f.a, f.b, k) : f.b.v, signals: f.b.s, queue: obs?.intel?.observed.approaches.map((a) => a.queue),
      forecast: obs?.intel?.predicted?.approaches.map((a) => a.p30.queue), ambulances, camera: j ? { c: j.c, half: (obs?.perception?.camera.footprintM ?? 160) / 2 } : null,
      mlJunctions: obs?.mlChanged ?? [] }
  }, [geom, obs, camJ])

  const p = obs?.perception
  const cur = fr.current.b
  return (
    <div className="inspector">
      <div className="toolbar">
        <label>Scenario<select value={cfg.scenario} onChange={(e) => setCfg({ ...cfg, scenario: e.target.value })}>{scenarios.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
        <label>Controller<select value={cfg.controller} onChange={(e) => setCfg({ ...cfg, controller: e.target.value })}>{Object.entries(LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label>Seed<input type="number" value={cfg.seed} onChange={(e) => setCfg({ ...cfg, seed: +e.target.value })} /></label>
        <label>Demand<select value={cfg.intensity} onChange={(e) => setCfg({ ...cfg, intensity: +e.target.value })}><option value={0.7}>Light</option><option value={1}>Standard</option><option value={1.18}>Stress</option></select></label>
        <button className="primary" onClick={start}>{running ? 'Restart' : 'Start live run'}</button>
        <button disabled={!running} onClick={() => send({ cmd: status.paused ? 'resume' : 'pause' })}>{status.paused ? 'Resume' : 'Pause'}</button>
        <button disabled={!running} onClick={() => send({ cmd: 'stop' })}>Stop</button>
        <label>Speed<select value={cfg.speed} onChange={(e) => { setCfg({ ...cfg, speed: +e.target.value }); send({ cmd: 'speed', value: +e.target.value }) }}>{[1, 2, 4, 8, 16].map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>
        <span className={`pill ${status.status}`}>{status.paused ? 'paused' : status.status}</span>
        <span className="clock">t = {cur?.t ?? 0} s</span>
      </div>
      {err && <div className="error">{err}</div>}
      <p className="small muted lead">A live SUMO run with the three AI components attached as <b>read-only observers</b>. Nothing on this page can change a signal. Every panel shows the exact numbers that component receives, refreshed each 5 s decision.</p>
      <div className="insp-grid">
        <div className="insp-map">
          <div className="mapbox tall"><RoadMap geom={geom} scene={scene} title={`Ground truth (SUMO) · ${LABEL[cfg.controller]}`} accent="#38bdf8" /></div>
          <div className="cam-pick">Camera at {meta.junctions.map((j) => <button key={j} className={j === camJ ? 'on' : ''} onClick={() => pickCam(j)}>{j}</button>)}</div>
          {(cur?.e?.length ?? 0) > 0 && <div className="card slim"><b className="small">Ambulances on the road now</b>{cur!.e!.map(([id, st]) => <div className="small" key={id}><i className="dot" style={{ background: ['#34d399', '#f43f5e', '#f59e0b', '#22d3ee'][st] }} /> unit {id}: {STATUS_TEXT[st]}</div>)}</div>}
        </div>
        <div className="insp-panels">
          <CameraPanel cam={p?.camera} meta={meta} />
          <ForecastPanel meta={meta} geom={geom} p={p} obs={obs} />
          <PpoPanel meta={meta} ppo={p?.ppo} agree={agree} />
          <GeminiPanel meta={meta} live={running && !!obs} />
        </div>
      </div>
    </div>
  )
}

function CameraPanel({ cam, meta }: { cam?: Camera; meta: Meta }) {
  const [mode, setMode] = useState<'camera' | 'mask'>('camera')
  return (
    <div className="card">
      <h4>Computer vision <span className="badge syn">synthetic camera</span> <small>an overhead camera rendered from the simulation, then analysed with OpenCV</small></h4>
      {!meta.camera.available && <div className="error">OpenCV is not installed ({meta.camera.reason}). Install <code>opencv-python-headless</code>.</div>}
      {!cam && <p className="muted small">Start a live run to see the camera feed.</p>}
      {cam && !cam.available && <div className="error">{cam.reason}</div>}
      {cam?.available && cam.frame && (
        <div className="cam">
          <div className="cam-img">
            <div className="seg"><button className={mode === 'camera' ? 'on' : ''} onClick={() => setMode('camera')}>What the camera records</button><button className={mode === 'mask' ? 'on' : ''} onClick={() => setMode('mask')}>What the detector extracts</button></div>
            <div className="imgwrap">
              <img src={mode === 'camera' ? cam.frame : cam.mask} alt="camera" />
              <svg viewBox={`0 0 ${cam.size} ${cam.size}`}>
                {cam.detections?.map((d, i) => <g key={i}><rect x={d.box[0]} y={d.box[1]} width={d.box[2]} height={d.box[3]} fill="none" strokeWidth="1.6" stroke={d.stopped ? '#f43f5e' : '#34d399'} strokeDasharray={d.matchedTruth ? undefined : '4 3'} />
                  {d.estimatedVehicles > 1 && <text x={d.box[0] + 2} y={d.box[1] - 3} fontSize="11" fill="#fde68a">×{d.estimatedVehicles}</text>}</g>)}
              </svg>
            </div>
            <div className="small muted">Green box: moving · red box: stopped · dashed: does not match any SUMO vehicle · ×n: touching vehicles merged into one blob, count from blob area.</div>
          </div>
          <div className="cam-stats">
            <div className="kv"><span>Vehicles SUMO has in view (ground truth)</span><b>{cam.truthVehiclesInView}</b></div>
            <div className="kv"><span>Vehicles counted from blob areas</span><b>{cam.countEstimate} <small className={Math.abs((cam.countEstimate ?? 0) - (cam.truthVehiclesInView ?? 0)) > 5 ? 'warn' : 'good'}>({(cam.countEstimate ?? 0) - (cam.truthVehiclesInView ?? 0) >= 0 ? '+' : ''}{(cam.countEstimate ?? 0) - (cam.truthVehiclesInView ?? 0)})</small></b></div>
            <div className="kv"><span>Blobs found / blobs that are real vehicles</span><b>{cam.detections?.length} / {cam.precision !== null ? fmt((cam.precision ?? 0) * 100, 0) + '%' : '—'}</b></div>
            <div className="kv"><span>Vehicles that stand alone as their own blob</span><b>{cam.recall !== null ? fmt((cam.recall ?? 0) * 100, 0) + '%' : '—'} <small className="muted">(the rest touch a neighbour)</small></b></div>
            <table className="tbl"><thead><tr><th>Approach ({cam.junction})</th><th className="num">Camera queue</th><th className="num">SUMO queue</th><th className="num">Error</th></tr></thead>
              <tbody>{cam.approaches?.map((a) => <tr key={a.approach}><td>{a.approach}</td><td className="num">{a.cameraQueue}</td><td className="num">{a.sumoQueue}</td><td className={`num ${a.error === 0 ? '' : Math.abs(a.error) > 2 ? 'bad' : 'warn'}`}>{a.error > 0 ? '+' : ''}{a.error}</td></tr>)}</tbody></table>
            <p className="small muted"><b>Method.</b> {cam.method}</p>
            <p className="small muted"><b>Limits.</b> {cam.limits} Compute time {fmt(cam.latencyMs, 0)} ms per frame. The road-shape reconstruction prototype on <code>task/vision</code> is a separate tool and is not part of this loop.</p>
          </div>
        </div>
      )}
    </div>
  )
}

function ForecastPanel({ meta, geom, p, obs }: { meta: Meta; geom: Geometry; p?: Perception; obs: ObsMsg | null }) {
  const f = p?.forecaster
  const cols = f ? f.features.map((_, j) => { const v = f.matrix.map((r) => r[j]); return { min: Math.min(...v), max: Math.max(...v) } }) : []
  const imp = Object.entries(meta.forecaster.importance30s ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 8)
  const im = Math.max(...imp.map((x) => x[1]), 1e-9)
  const pred = obs?.intel?.predicted?.approaches
  return (
    <div className="card">
      <h4>Queue forecaster <small>{meta.forecaster.kind} · model v{meta.model.version} · {meta.model.calibrated ? '90% intervals calibrated on validation seeds' : 'no calibrated intervals'}</small></h4>
      <p className="small muted">It sees one row of 19 detector-style numbers per approach (counts, halted, waiting, exit occupancy, signal state, upstream platoons, 10–30 s history). It never sees positions, the camera, the map or the ambulance. Shade = value relative to the other approaches.</p>
      {!f ? <p className="muted small">Start a live run.</p> : (
        <div className="heat-wrap">
          <table className="heat">
            <thead><tr><th>approach</th>{f.features.map((n) => <th key={n}><div>{n}</div></th>)}<th className="pr">now</th><th className="pr">+30 s</th><th className="pr">+60 s</th></tr></thead>
            <tbody>{f.approaches.map((a, i) => {
              const pr = pred?.[i], cap = geom.approaches[i]?.cap ?? 1
              return (
                <tr key={a}><td className="rk">{a}</td>
                  {f.matrix[i].map((v, j) => <td key={j} style={{ background: HEAT(cols[j].max > cols[j].min ? (v - cols[j].min) / (cols[j].max - cols[j].min) * 0.8 : 0) }} title={`${f.features[j]} = ${v}`}>{Math.abs(v) >= 100 ? Math.round(v) : v}</td>)}
                  <td className="pr">{f.matrix[i][0]}</td>
                  <td className="pr pred" style={{ outline: pr && pr.p30.queue >= 0.425 * cap ? '1px solid #f0abfc' : undefined }}>{pr ? `${pr.p30.queue}` : '—'}{pr?.p30.interval && <small> [{pr.p30.interval.lo}–{pr.p30.interval.hi}]</small>}</td>
                  <td className="pr pred">{pr ? `${pr.p60.queue}` : '—'}{pr?.p60.interval && <small> [{pr.p60.interval.lo}–{pr.p60.interval.hi}]</small>}</td></tr>
              )
            })}</tbody>
          </table>
        </div>
      )}
      <h5>What it relies on most <small>permutation importance, 30 s model, held-out rows</small></h5>
      {imp.map(([n, v]) => <div className="bar-row" key={n}><span>{n}</span><div><i style={{ width: `${(100 * Math.max(0, v)) / im}%` }} /></div><b>{fmt(v, 2)}</b></div>)}
    </div>
  )
}

function PpoPanel({ meta, ppo, agree }: { meta: Meta; ppo?: Ppo; agree: { n: number; same: number }[] }) {
  const names = meta.ppo.observationNames
  return (
    <div className="card">
      <h4>Reinforcement-learning agent (PPO) <span className="badge warn">shadow only</span> <small>checkpoint {meta.ppo.checkpoint}</small></h4>
      <div className={`gate ${meta.ppo.validationPassed ? 'ok' : 'fail'}`}>{meta.ppo.validationPassed ? 'Passed the validation gate.' : <>Failed the publication gate and is <b>not controlling anything</b>: {meta.ppo.validationReason}.</>} It is shown here only so you can see what it observes and what it would do.</div>
      {!ppo && <p className="muted small">Start a live run.</p>}
      {ppo && !ppo.available && <div className="error">{ppo.reason}</div>}
      {ppo?.available && ppo.observation && ppo.probabilities && ppo.activeAction && ppo.ppoAction && ppo.agrees && (
        <>
          <div className="ppo-cols">
            {meta.junctions.map((j, ji) => (
              <div className="ppo-col" key={j}>
                <div className="ppo-h">{j}</div>
                <div className="ppo-act">
                  <div className="small">PPO would <b>{ppo.actionNames?.[ppo.ppoAction![ji]]}</b> <span className="muted">({fmt(ppo.probabilities![ji][ppo.ppoAction![ji]] * 100, 0)}%)</span></div>
                  <div className="probbar"><i style={{ width: `${ppo.probabilities![ji][1] * 100}%` }} /><span>switch {fmt(ppo.probabilities![ji][1] * 100, 0)}%</span></div>
                  <div className="small">Active controller: <b>{ppo.activeAction![ji] ? 'switch' : 'hold'}</b> <span className={`tag ${ppo.agrees![ji] ? 'ok' : 'warn'}`}>{ppo.agrees![ji] ? 'same' : 'differs'}</span></div>
                  <div className="small muted">value estimate {fmt(ppo.value?.[ji], 2)} · agreed on {agree[ji] ? fmt((100 * agree[ji].same) / agree[ji].n, 0) : '—'}% of {agree[ji]?.n ?? 0} decisions</div>
                </div>
              </div>
            ))}
          </div>
          <h5>What each junction's policy observes <small>33 numbers scaled to −1…1</small></h5>
          <table className="obs">
            <tbody>{meta.ppo.groups.map((g) => (
              <Fragment key={g.name}>
                <tr className="grp"><td colSpan={5}>{g.name}</td></tr>
                {names.slice(g.from, g.to).map((n, k) => (
                  <tr key={n + g.from}><td className="on">{n}</td>
                    {ppo.observation!.map((row, ji) => { const v = row[g.from + k]; return <td key={ji}><div className="ob"><i style={{ width: `${Math.min(100, Math.abs(v) * 100)}%`, background: v < 0 ? '#f59e0b' : '#38bdf8' }} /><b>{fmt(v, 2)}</b></div></td> })}</tr>
                ))}
              </Fragment>
            ))}</tbody>
          </table>
          <div className="small muted">Inference {fmt(ppo.latencyMs, 1)} ms. The decision grid is 5 s; the agent can only hold the green or ask for the next stage, and the safety layer enforces minimum green, amber and all-red.</div>
        </>
      )}
    </div>
  )
}
