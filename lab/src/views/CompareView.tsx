import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Chart from '../components/Chart'
import MapCanvas, { type DrawData, type View } from '../components/MapCanvas'
import { fmt, getJSON, postJSON } from '../lib/api'
import { interpolate, obsAt } from '../lib/interp'
import { CONTROLLER_LABEL, KPI, type Geometry, type Replay, type ReplaySummary, type RunRec } from '../lib/types'

interface EvalSummary { summary: Record<string, Record<string, { mean_diff: number; ci95: [number, number]; pairs: number; better: number; worse: number; left_mean: number; right_mean: number }>> & { episodes?: number }; meta?: Record<string, unknown> }
interface Props { geom: Geometry; scenarios: { id: string; title: string }[]; mlAvailable: boolean; onReplay: (id: string | null) => void; pending?: Record<string, unknown> | null }

const LCOL = '#38bdf8', RCOL = '#c084fc'

export default function CompareView({ geom, scenarios, mlAvailable, onReplay, pending }: Props) {
  const [index, setIndex] = useState<ReplaySummary[]>([])
  const [rid, setRid] = useState<string | null>(null)
  const [rep, setRep] = useState<Replay | null>(null)
  const [loading, setLoading] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(8)
  const [t, setT] = useState(0)
  const [ev, setEv] = useState<EvalSummary | null>(null)
  const [form, setForm] = useState({ left: 'queue_heuristic', right: 'ml_forecast', scenario: 'rush_ramp', seed: 3200, intensity: 1.18 })
  const [job, setJob] = useState<{ id: string; state: string; error?: string; wall_s?: number } | null>(null)
  const tref = useRef(0)
  const view = useRef<View | null>(null)

  const loadIndex = useCallback(async (select?: string) => {
    const r = await getJSON<{ replays: ReplaySummary[] }>('/api/lab/replays')
    setIndex(r.replays)
    if (select) setRid(select)
    else setRid((cur) => cur ?? r.replays[0]?.id ?? null)
  }, [])
  useEffect(() => { loadIndex().catch(() => undefined); getJSON<EvalSummary>('/api/lab/comparison').then(setEv).catch(() => undefined) }, [loadIndex])
  useEffect(() => {
    if (!rid) return
    setLoading(true); setPlaying(false); onReplay(rid)
    getJSON<Replay>(`/api/lab/replays/${rid}`).then((r) => { setRep(r); tref.current = 0; setT(0); setPlaying(true) }).finally(() => setLoading(false))
  }, [rid, onReplay])
  useEffect(() => {
    if (pending) { setForm((f) => ({ ...f, ...pending })) }
  }, [pending])

  const dur = rep ? Math.max(rep.left.frames.length, rep.right.frames.length) - 1 : 0
  useEffect(() => {
    if (!playing || !rep) return
    let raf = 0, prev = performance.now(), lastUi = 0
    const loop = (now: number) => {
      tref.current = Math.min(dur, tref.current + (Math.max(0, now - prev) / 1000) * speed) // rAF time can precede performance.now()
      prev = now
      if (now - lastUi > 120) { setT(tref.current); lastUi = now }
      if (tref.current >= dur) { setPlaying(false); setT(dur); return }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, rep, dur])

  const seek = (x: number) => { tref.current = x; setT(x) }
  const mkSource = (side: 'left' | 'right') => (): DrawData | null => {
    if (!rep) return null
    const run = rep[side]
    const tt = Math.max(0, tref.current)
    const i = Math.max(0, Math.min(run.frames.length - 1, Math.floor(tt)))
    const f = run.frames[i]
    if (!f) return null
    const o = obsAt(run.obs, tt)
    const recentMl = (run.mlLog ?? []).filter(([ts]) => ts <= tt && tt - ts < 6).map(([, j]) => j)
    return { vehicles: interpolate(f, run.frames[i + 1], tt - i), signals: f.s, queue: o?.q, forecast: o?.p30, mlJunctions: recentMl }
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const srcL = useCallback(mkSource('left'), [rep])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const srcR = useCallback(mkSource('right'), [rep])

  const series = useMemo(() => {
    if (!rep) return null
    const pts = (r: RunRec, k: number) => r.frames.filter((_, i) => i % 5 === 0).map((f) => [f.t, f.k[k]] as [number, number])
    return {
      halting: [{ name: `${lbl(rep.left.controller)} halted`, color: LCOL, points: pts(rep.left, KPI.halting) },
        { name: `${lbl(rep.right.controller)} halted`, color: RCOL, points: pts(rep.right, KPI.halting) }],
      wait: [{ name: `${lbl(rep.left.controller)}`, color: LCOL, points: pts(rep.left, KPI.meanWaitNow) },
        { name: `${lbl(rep.right.controller)}`, color: RCOL, points: pts(rep.right, KPI.meanWaitNow) }],
      markers: (rep.right.mlLog ?? []).map(([ts, j, d]) => ({ x: ts, color: d > 0 ? '#c084fc' : '#f0abfc', title: `${geom.controlled[j]}: ML ${d > 0 ? 'switched earlier' : 'held green longer'} at ${ts}s` })),
    }
  }, [rep, geom])

  const record = async () => {
    try {
      const j = await postJSON<{ id: string; state: string }>('/api/lab/replays', form)
      setJob(j)
      const poll = async () => {
        const s = await getJSON<{ id: string; state: string; error?: string; wall_s?: number; result?: { id: string } }>(`/api/lab/jobs/${j.id}`)
        setJob(s)
        if (s.state === 'running') setTimeout(poll, 1000)
        else if (s.state === 'done' && s.result) loadIndex(s.result.id)
      }
      setTimeout(poll, 1000)
    } catch (e) { setJob({ id: '-', state: 'error', error: String(e) }) }
  }

  const ti = Math.floor(t)
  const kL = rep?.left.frames[Math.min(ti, rep.left.frames.length - 1)]?.k
  const kR = rep?.right.frames[Math.min(ti, rep.right.frames.length - 1)]?.k
  const wait = rep?.diff.find((d) => d.key === 'mean_wait_s')
  const mlRight = rep?.mlInControl.right
  const rightLabel = rep ? (mlRight ? `RIGHT · ${lbl(rep.right.controller)} (ML in control)` : `RIGHT · ${lbl(rep.right.controller)}`) : ''
  const pairKey = rep && `${rep.left.controller}|${rep.right.controller}`
  const evBlock = ev && pairKey ? (pairKey === 'queue_heuristic|ml_forecast' ? ev.summary.ml_vs_heuristic : pairKey === 'fixed_time|queue_heuristic' ? ev.summary.heuristic_vs_fixed : pairKey === 'fixed_time|ml_forecast' ? ev.summary.ml_vs_fixed : null) : null

  return (
    <div className="compare">
      <div className="toolbar">
        <label className="grow">Recorded pair<select value={rid ?? ''} onChange={(e) => setRid(e.target.value)}>
          {index.map((r) => <option key={r.id} value={r.id}>{lbl(r.left)} vs {lbl(r.right)} · {r.scenario} · seed {r.seed} · {r.intensity}×</option>)}</select></label>
        <button className="primary" disabled={!rep} onClick={() => { if (t >= dur) seek(0); setPlaying(!playing) }}>{playing ? 'Pause' : 'Play'}</button>
        <button disabled={!rep} onClick={() => { seek(0); setPlaying(false) }}>⏮</button>
        <label>Speed<select value={speed} onChange={(e) => setSpeed(+e.target.value)}>{[1, 2, 4, 8, 16, 32].map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>
        <span className="mono">t = {ti} s / {dur} s</span>
        {loading && <span className="pill starting">loading…</span>}
      </div>
      {ev && <Headline ev={ev} />}
      {!index.length && <div className="card">No recorded pairs yet. Record one below (two SUMO runs, ~15 s).</div>}
      {rep && (
        <>
          <div className="pair">
            <div className="side-run">
              <MapCanvas geom={geom} source={srcL} view={view} label={`LEFT · ${lbl(rep.left.controller)}`} accent={LCOL} />
              <RunTiles k={kL} other={kR} />
            </div>
            <div className="side-run">
              <MapCanvas geom={geom} source={srcR} view={view} label={rightLabel} accent={RCOL} />
              <RunTiles k={kR} other={kL} />
            </div>
          </div>
          <input className="scrub" type="range" min={0} max={dur} step={1} value={ti} onChange={(e) => { seek(+e.target.value) }} />
          <div className="compare-bottom">
            <div className="card">
              <h4>Timeline: halted vehicles on approaches <small>measured · click to seek{series?.markers.length ? ' · ticks = ML-changed decisions' : ''}</small></h4>
              {series && <Chart series={series.halting} cursor={t} markers={series.markers} xMax={dur} onSeek={seek} height={130} />}
              <h4>Mean current waiting time of vehicles in network (s)</h4>
              {series && <Chart series={series.wait} cursor={t} xMax={dur} onSeek={seek} height={100} />}
            </div>
            <div className="card">
              <Verdict rep={rep} wait={wait} />
              <table className="tbl">
                <thead><tr><th>Measured (whole run)</th><th className="num" style={{ color: LCOL }}>Left</th><th className="num" style={{ color: RCOL }}>Right</th><th className="num">Δ</th><th /></tr></thead>
                <tbody>{rep.diff.map((d) => (
                  <tr key={d.key}><td>{d.label}{d.unit && <small> ({d.unit})</small>}</td><td className="num">{fmt(d.left, 1)}</td><td className="num">{fmt(d.right, 1)}</td>
                    <td className="num">{d.diff > 0 ? '+' : ''}{fmt(d.diff, 1)}{d.pct !== null && <small> ({d.pct > 0 ? '+' : ''}{d.pct}%)</small>}</td>
                    <td>{d.verdict === 'same' ? <span className="tag">same</span> : d.verdict === 'right better' ? <span className="tag ok">right better</span> : <span className="tag bad">left better</span>}</td></tr>
                ))}</tbody>
              </table>
              <p className="small muted">Same network, route file (SHA {rep.demandSha}), SUMO seed {rep.seed}, demand {rep.intensity}×, 900 s demand window + drain. Two independent SUMO processes. CO₂ uses SUMO's HBEFA3 model with an assumed fleet.</p>
              {rep.right.ml && <p className="small">ML changed <b>{rep.right.ml.changed}</b> of {rep.right.ml.decisions} eligible decisions ({rep.right.ml.switchedEarlier} earlier switches, {rep.right.ml.heldLonger} longer holds).</p>}
            </div>
          </div>
          {evBlock && <EvalCard block={evBlock} episodes={(ev?.summary as { episodes?: number }).episodes} pair={pairKey!} />}
        </>
      )}
      <div className="card record">
        <h4>Record a new paired run</h4>
        <div className="toolbar inline">
          <label>Left<select value={form.left} onChange={(e) => setForm({ ...form, left: e.target.value })}>{Object.entries(CONTROLLER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label>Right<select value={form.right} onChange={(e) => setForm({ ...form, right: e.target.value })}>{Object.entries(CONTROLLER_LABEL).map(([k, v]) => <option key={k} value={k} disabled={k === 'ml_forecast' && !mlAvailable}>{v}</option>)}</select></label>
          <label>Scenario<select value={form.scenario} onChange={(e) => setForm({ ...form, scenario: e.target.value })}>{scenarios.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
          <label>Seed<input type="number" value={form.seed} onChange={(e) => setForm({ ...form, seed: +e.target.value })} /></label>
          <label>Demand<select value={form.intensity} onChange={(e) => setForm({ ...form, intensity: +e.target.value })}><option value={0.7}>0.7×</option><option value={1}>1.0×</option><option value={1.18}>1.18× stress</option></select></label>
          <button className="primary" disabled={job?.state === 'running'} onClick={record}>Run both in SUMO</button>
          {job && <span className={`pill ${job.state === 'done' ? 'finished' : job.state}`}>{job.state}{job.wall_s ? ` (${job.wall_s}s)` : ''}{job.error ? `: ${job.error}` : ''}</span>}
        </div>
      </div>
    </div>
  )
}

const lbl = (c: string) => CONTROLLER_LABEL[c] ?? c

type Pair = { mean_diff: number; ci95: [number, number]; pairs: number; better: number; worse: number; left_mean: number; right_mean: number; pct?: number }
function Headline({ ev }: { ev: EvalSummary }) {
  const s = ev.summary as unknown as { ml_vs_heuristic?: Record<string, Pair>; heuristic_vs_fixed?: Record<string, Pair>; ml_influence?: { decisions: number; changed: number; switchedEarlier: number; heldLonger: number } }
  const w = s.ml_vs_heuristic?.mean_wait_s
  if (!w) return null
  const verdict = w.ci95[1] < 0 ? 'yes' : w.ci95[0] > 0 ? 'worse' : 'no'
  const hf = s.heuristic_vs_fixed?.mean_wait_s
  const inf = s.ml_influence
  return <div className={`headline ${verdict === 'yes' ? 'good' : verdict === 'worse' ? 'bad' : 'neutral'}`}>
    <div className="hq">Did machine learning improve traffic?</div>
    <div className="ha">{verdict === 'yes' ? 'Yes' : verdict === 'worse' ? 'No: it made waiting worse' : 'No measurable improvement'}</div>
    <div className="small">Held-out test, {w.pairs} paired SUMO episodes (5 scenarios × 12 unseen seeds): mean wait {fmt(w.left_mean, 1)} s without ML vs {fmt(w.right_mean, 1)} s with ML
      (Δ {w.mean_diff > 0 ? '+' : ''}{fmt(w.mean_diff, 2)} s, 95% CI {fmt(w.ci95[0], 2)} to {fmt(w.ci95[1], 2)}).
      {inf && <> The forecast changed {inf.changed.toLocaleString()} of {inf.decisions.toLocaleString()} decisions ({fmt((100 * inf.changed) / Math.max(1, inf.decisions), 1)}%), {inf.heldLonger.toLocaleString()} of them longer holds for predicted spillback.</>}
      {hf && <> For reference, the heuristic vs fixed-time: Δ {hf.mean_diff > 0 ? '+' : ''}{fmt(hf.mean_diff, 2)} s (CI {fmt(hf.ci95[0], 2)} to {fmt(hf.ci95[1], 2)}).</>}</div>
  </div>
}

function RunTiles({ k, other }: { k?: number[]; other?: number[] }) {
  if (!k) return null
  const cell = (label: string, i: number, u = '', d = 0, lower = true) => {
    const diff = other ? k[i] - other[i] : 0
    const cls = !other || Math.abs(diff) < 1e-9 ? '' : (diff < 0) === lower ? 'good' : 'badv'
    return <div className="tile sm"><div className="tl">{label}</div><div className={`tv ${cls}`}>{fmt(k[i], d)}<small> {u}</small></div></div>
  }
  return <div className="tiles row">
    {cell('In network', KPI.inNetwork)}{cell('Halted', KPI.halting)}{cell('Wait now', KPI.meanWaitNow, 's', 1)}
    {cell('Arrived', KPI.arrived, '', 0, false)}{cell('CO₂', KPI.co2Kg, 'kg', 1)}{cell('Teleports', KPI.teleports)}
  </div>
}

function Verdict({ rep, wait }: { rep: Replay; wait?: { left: number; right: number; diff: number; pct: number | null } }) {
  if (!wait) return null
  const better = wait.diff < 0
  const who = lbl(rep.right.controller)
  return <div className={`verdict ${better ? 'good' : wait.diff > 0 ? 'bad' : ''}`}>
    <b>This run:</b> {who} {better ? 'reduced' : wait.diff > 0 ? 'increased' : 'did not change'} mean waiting time
    {wait.diff !== 0 && <> by <b>{fmt(Math.abs(wait.diff), 1)} s</b>{wait.pct !== null && <> ({fmt(Math.abs(wait.pct), 1)}%)</>}</>} versus {lbl(rep.left.controller)}.
    <div className="small muted">One seed is one sample: see the multi-seed held-out evaluation for whether the effect is reliable.</div>
  </div>
}

function EvalCard({ block, episodes, pair }: { block: Record<string, { mean_diff: number; ci95: [number, number]; pairs: number; better: number; worse: number; left_mean: number; right_mean: number }>; episodes?: number; pair: string }) {
  const rows: [string, string, boolean][] = [['mean_wait_s', 'Mean waiting time (s)', true], ['mean_travel_s', 'Mean travel time (s)', true], ['throughput_vph', 'Throughput (veh/h)', false],
    ['queue_total_mean_veh', 'Mean queued vehicles', true], ['co2_kg', 'CO₂ estimate (kg)', true], ['teleports', 'Teleports', true], ['collisions', 'Collisions', true]]
  const w = block.mean_wait_s
  const clear = w && (w.ci95[1] < 0 || w.ci95[0] > 0)
  return <div className="card eval">
    <h4>Held-out multi-seed evaluation <small>{pair.replace('|', ' → ')} · {w?.pairs ?? episodes} paired episodes on seeds never used for training or tuning</small></h4>
    {w && <div className={`verdict ${clear ? (w.mean_diff < 0 ? 'good' : 'bad') : ''}`}>
      {clear ? (w.mean_diff < 0 ? 'Reliable improvement' : 'Reliably worse') : 'No reliable difference'} in mean waiting time: {w.mean_diff > 0 ? '+' : ''}{fmt(w.mean_diff, 2)} s
      (95% bootstrap CI {fmt(w.ci95[0], 2)} to {fmt(w.ci95[1], 2)}); right better in {w.better} of {w.pairs} pairs, worse in {w.worse}.</div>}
    <table className="tbl"><thead><tr><th>Metric</th><th className="num">Left mean</th><th className="num">Right mean</th><th className="num">Mean Δ</th><th className="num">95% CI</th><th className="num">Right better / worse</th></tr></thead>
      <tbody>{rows.filter(([k]) => block[k]).map(([k, l]) => { const b = block[k]; return <tr key={k}><td>{l}</td><td className="num">{fmt(b.left_mean, 1)}</td><td className="num">{fmt(b.right_mean, 1)}</td>
        <td className="num">{b.mean_diff > 0 ? '+' : ''}{fmt(b.mean_diff, 2)}</td><td className="num">[{fmt(b.ci95[0], 2)}, {fmt(b.ci95[1], 2)}]</td><td className="num">{b.better} / {b.worse}</td></tr> })}</tbody></table>
  </div>
}
