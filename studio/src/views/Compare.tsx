import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import LineChart from '../components/LineChart'
import RoadMap, { type Scene, type View } from '../render/RoadMap'
import { fmt, getJSON, signed } from '../lib/api'
import { at, interpolate, statusSince } from '../lib/interp'
import { LABEL, STATUS_COLOR, STATUS_TEXT, type Comparison, type Geometry, type Pt, type Replay, type ReplaySummary, type RunRec, type Story } from '../lib/types'

const LCOL = '#38bdf8', RCOL = '#c084fc'
type Side = 'left' | 'right'
const lbl = (c: string) => LABEL[c] ?? c

function tracksOf(run: RunRec) {
  const m = new Map<number, [number, number, number][]>()
  for (const f of run.frames) for (const v of f.v) if (v[4] === 2) { const a = m.get(v[0]) ?? []; a.push([f.t, v[1] / 10, v[2] / 10]); m.set(v[0], a) }
  return m
}

export default function Compare({ geom }: { geom: Geometry }) {
  const [index, setIndex] = useState<ReplaySummary[]>([])
  const [rid, setRid] = useState<string | null>(null)
  const [rep, setRep] = useState<Replay | null>(null)
  const [story, setStory] = useState<Story | null>(null)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(8)
  const [t, setT] = useState(0)
  const [err, setErr] = useState<string | null>(null)
  const [cmp, setCmp] = useState<Comparison | null>(null)
  const tref = useRef(0)
  const view = useRef<View | null>(null)

  useEffect(() => {
    getJSON<Comparison>('/api/lab/comparison').then(setCmp).catch(() => undefined)
    getJSON<{ replays: ReplaySummary[] }>('/api/lab/replays').then((r) => {
      setIndex(r.replays)
      setRid(r.replays.find((x) => x.id.includes('ml_forecast_ev__rush_ramp'))?.id ?? r.replays[0]?.id ?? null)
    }).catch((e) => setErr(String(e)))
  }, [])
  useEffect(() => {
    if (!rid) return
    setPlaying(false); setRep(null); setStory(null)
    Promise.all([getJSON<Replay>(`/api/lab/replays/${rid}`), getJSON<Story>(`/api/studio/replays/${rid}/story`)])
      .then(([r, s]) => { setRep(r); setStory(s); tref.current = 0; setT(0); setPlaying(true) }).catch((e) => setErr(String(e)))
  }, [rid])

  const dur = rep ? Math.max(rep.left.frames.length, rep.right.frames.length) - 1 : 0
  useEffect(() => {
    if (!playing || !rep) return
    let raf = 0, prev = performance.now(), lastUi = 0
    const loop = (now: number) => {
      tref.current = Math.min(dur, tref.current + (Math.max(0, now - prev) / 1000) * speed)
      prev = now
      if (now - lastUi > 100) { setT(tref.current); lastUi = now }
      if (tref.current >= dur) { setPlaying(false); setT(dur); return }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, rep, dur])

  const tracks = useMemo(() => (rep ? { left: tracksOf(rep.left), right: tracksOf(rep.right) } : null), [rep])
  const sceneFor = useCallback((side: Side) => (): Scene | null => {
    if (!rep || !tracks) return null
    const run = rep[side], tt = Math.max(0, tref.current), i = Math.max(0, Math.min(run.frames.length - 1, Math.floor(tt))), f = run.frames[i]
    if (!f) return null
    const o = at(run.obs, tt)
    const ambulances = (f.e ?? []).map(([id, status]) => {
      const segs = story?.sides[side].ambulances.find((a) => a.id === id)?.segments
      const since = segs ? statusSince(segs, f.t) : null
      const trail = tracks[side].get(id)?.filter((p) => p[0] <= tt && p[0] >= tt - 50).map((p) => [p[1], p[2]] as Pt)
      return { id, status, seconds: since?.seconds, trail }
    })
    return { vehicles: interpolate(f, run.frames[i + 1], tt - i), signals: f.s, queue: o?.q, forecast: o?.p30, ambulances,
      mlJunctions: (run.mlLog ?? []).filter(([ts]) => ts <= tt && tt - ts < 5).map(([, j]) => j) }
  }, [rep, tracks, story])
  const sceneL = useMemo(() => sceneFor('left'), [sceneFor])
  const sceneR = useMemo(() => sceneFor('right'), [sceneFor])

  const series = useMemo(() => {
    if (!rep) return null
    const pts = (r: RunRec, k: number) => r.frames.filter((_, i) => i % 5 === 0).map((f) => [f.t, f.k[k]] as [number, number])
    const marks = (story?.sides.right.ambulances ?? []).flatMap((a) => a.segments.filter((s) => s[2] === 1 || s[2] === 2).slice(0, 1).map((s) => ({ x: s[0], color: STATUS_COLOR[s[2]], title: `ambulance ${a.id}: ${STATUS_TEXT[s[2]]} from ${s[0]} s` })))
    return { halted: [{ name: lbl(rep.left.controller), color: LCOL, points: pts(rep.left, 1), area: true }, { name: lbl(rep.right.controller), color: RCOL, points: pts(rep.right, 1), area: true }],
      wait: [{ name: lbl(rep.left.controller), color: LCOL, points: pts(rep.left, 2) }, { name: lbl(rep.right.controller), color: RCOL, points: pts(rep.right, 2) }], marks }
  }, [rep, story])

  const seek = (x: number) => { tref.current = x; setT(x) }
  const ti = Math.floor(t)
  const kOf = (side: Side) => rep?.[side].frames[Math.min(ti, rep[side].frames.length - 1)]?.k
  if (err) return <div className="error">{err}</div>
  const row = (key: string) => rep?.diff.find((d) => d.key === key)

  return (
    <div className="compare">
      <div className="toolbar">
        <label className="grow">Recorded pair<select value={rid ?? ''} onChange={(e) => setRid(e.target.value)}>
          {index.map((r) => <option key={r.id} value={r.id}>{lbl(r.left)}  vs  {lbl(r.right)} · {r.scenario} · seed {r.seed} · {r.intensity}×</option>)}</select></label>
        <button className="primary" disabled={!rep} onClick={() => { if (t >= dur) seek(0); setPlaying(!playing) }}>{playing ? 'Pause' : 'Play'}</button>
        <button disabled={!rep} onClick={() => { seek(0); setPlaying(false) }}>Restart</button>
        <label>Speed<select value={speed} onChange={(e) => setSpeed(+e.target.value)}>{[1, 2, 4, 8, 16, 32].map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>
        <span className="clock">{String(Math.floor(ti / 60)).padStart(2, '0')}:{String(ti % 60).padStart(2, '0')} <small>/ {Math.floor(dur / 60)}:{String(dur % 60).padStart(2, '0')}</small></span>
      </div>
      {!rep && <div className="card">Loading recorded runs…</div>}
      {rep && (
        <>
          <Banner rep={rep} row={row} cmp={cmp} />
          <div className="pair">
            {(['left', 'right'] as const).map((side) => (
              <div className="side" key={side}>
                <div className="mapbox"><RoadMap geom={geom} scene={side === 'left' ? sceneL : sceneR} view={view} accent={side === 'left' ? LCOL : RCOL}
                  title={`${side === 'left' ? 'LEFT' : 'RIGHT'} · ${lbl(rep[side].controller)}`} /></div>
                <Tiles k={kOf(side)} other={kOf(side === 'left' ? 'right' : 'left')} />
                <div className="chips">
                  {rep[side].controllerInfo.mlInControl && <span className="chip ml">ML forecast steers the signals</span>}
                  {rep[side].controllerInfo.emergencyGreen ? <span className="chip ev">Lights ahead of ambulances turn green (controller rule)</span> : <span className="chip">No ambulance priority</span>}
                </div>
              </div>
            ))}
          </div>
          <input className="scrub" type="range" min={0} max={dur} step={1} value={ti} onChange={(e) => seek(+e.target.value)} />
          <div className="legend-row">
            <span><i className="v" style={{ background: '#8fb4e8' }} />car</span><span><i className="v" style={{ background: '#8696ab' }} />truck</span>
            <span><i className="v" style={{ background: '#f8fafc', boxShadow: '0 0 6px #ef4444' }} />ambulance</span><span><i className="q" style={{ background: 'rgba(244,63,94,.5)' }} />measured queue</span>
            <span><i className="q dash" />forecast queue (+30 s, model output)</span><span><i className="ring" />ML changed a decision</span>
          </div>
          <div className="grid2">
            <div className="card">
              <h4>Ambulances <small>what the simulator measured: nothing makes other cars or lights give way</small></h4>
              {story && (['left', 'right'] as const).map((side) => <AmbulanceLanes key={side} side={side} story={story} dur={dur} t={t} onSeek={seek} rep={rep} />)}
              <div className="legend-row">{STATUS_TEXT.map((s, i) => <span key={s}><i className="q" style={{ background: STATUS_COLOR[i] }} />{s}</span>)}</div>
            </div>
            <div className="card">
              <h4>Halted vehicles on the approaches <small>measured · click to seek · ticks = ambulance stopped</small></h4>
              {series && <LineChart series={series.halted} xMax={dur} cursor={t} onSeek={seek} markers={series.marks} height={140} />}
              <h4>Mean waiting time of vehicles on the road (s)</h4>
              {series && <LineChart series={series.wait} xMax={dur} cursor={t} onSeek={seek} height={110} />}
            </div>
          </div>
          <DiffTable rep={rep} />
        </>
      )}
    </div>
  )
}

function Banner({ rep, row, cmp }: { rep: Replay; row: (k: string) => Replay['diff'][number] | undefined; cmp: Comparison | null }) {
  const civ = row('mean_wait_civilian_s'), evt = row('ev_travel_mean_s'), red = row('ev_stopped_red_s'), que = row('ev_stopped_queue_s')
  if (!civ) return null
  const blk = rep.left.controller === 'queue_heuristic' && rep.right.controller === 'ml_forecast_ev' ? cmp?.summary.ml_ev_vs_heuristic : undefined
  const multi = blk?.ev_travel_mean_s && blk.mean_wait_civilian_s ? { ev: blk.ev_travel_mean_s, civ: blk.mean_wait_civilian_s } as { ev: Comparison['summary']['ml_vs_heuristic'][string]; civ: Comparison['summary']['ml_vs_heuristic'][string] } : null
  const w = (d: Replay['diff'][number] | undefined, unit = 's') => d && <b className={d.diff < 0 ? 'good' : d.diff > 0 ? 'bad' : ''}>{signed(d.diff)} {unit}</b>
  return (
    <div className="banner">
      <div className="b-title">This recorded run <span>{rep.scenario} · seed {rep.seed} · demand {rep.intensity}×</span></div>
      <div className="b-grid">
        <div><div className="b-k">Every vehicle except ambulances</div><div className="b-v">{w(civ)}</div><div className="b-s">mean wait ({fmt(civ.left, 1)} → {fmt(civ.right, 1)} s)</div></div>
        {evt && <div><div className="b-k">Ambulance travel time</div><div className="b-v">{w(evt)}</div><div className="b-s">{fmt(evt.left, 0)} → {fmt(evt.right, 0)} s average per unit</div></div>}
        {red && <div><div className="b-k">Ambulance stopped at red lights</div><div className="b-v">{w(red)}</div><div className="b-s">{fmt(red.left, 1)} → {fmt(red.right, 1)} s</div></div>}
        {que && <div><div className="b-k">Ambulance stuck behind queues</div><div className="b-v">{w(que)}</div><div className="b-s">{fmt(que.left, 1)} → {fmt(que.right, 1)} s: cars still block it</div></div>}
      </div>
      {multi && <div className="b-multi">Across all {multi.civ.pairs} held-out runs of this pair: ambulance travel {signed(multi.ev.mean_diff)} s (95% CI {fmt(multi.ev.ci95[0], 1)} to {fmt(multi.ev.ci95[1], 1)}), everyone else's mean wait {signed(multi.civ.mean_diff)} s (CI {fmt(multi.civ.ci95[0], 1)} to {fmt(multi.civ.ci95[1], 1)}).</div>}
      <div className="b-note">One seed is one sample; the Results tab has the full multi-seed picture.</div>
    </div>
  )
}

function Tiles({ k, other }: { k?: number[]; other?: number[] }) {
  if (!k) return null
  const cell = (label: string, i: number, d = 0, unit = '', lower = true) => {
    const diff = other ? k[i] - other[i] : 0
    const cls = !other || Math.abs(diff) < 1e-9 ? '' : (diff < 0) === lower ? 'good' : 'bad'
    return <div className="tile"><div className="tl">{label}</div><div className={`tv ${cls}`}>{fmt(k[i], d)}<small>{unit}</small></div></div>
  }
  return <div className="tiles">{cell('On the road', 0)}{cell('Halted', 1)}{cell('Wait now', 2, 1, ' s')}{cell('Arrived', 3, 0, '', false)}{cell('CO₂ so far', 4, 1, ' kg')}{cell('Teleports', 5)}</div>
}

function AmbulanceLanes({ side, story, dur, t, onSeek, rep }: { side: Side; story: Story; dur: number; t: number; onSeek: (x: number) => void; rep: Replay }) {
  const s = story.sides[side]
  const W = 600, X = (x: number) => (x / Math.max(1, dur)) * W
  return (
    <div className="amb-block">
      <div className="amb-head"><b style={{ color: side === 'left' ? LCOL : RCOL }}>{side === 'left' ? 'Left' : 'Right'}</b> {lbl(rep[side].controller)}
        {s.totals.ev_total !== undefined && <span> · {s.totals.ev_total} ambulances · avg travel {fmt(s.totals.ev_travel_mean_s as number, 0)} s · red {fmt(s.totals.ev_stopped_red_s as number, 1)} s · queue {fmt(s.totals.ev_stopped_queue_s as number, 1)} s{s.totals.ev_unfinished ? ` · ${s.totals.ev_unfinished} not finished (time so far)` : ''}</span>}</div>
      {s.ambulances.map((a) => (
        <div className="amb-row" key={a.id}>
          <span className="amb-id">unit {a.id}</span>
          <svg viewBox={`0 0 ${W} 14`} preserveAspectRatio="none" onMouseDown={(e) => { const r = e.currentTarget.getBoundingClientRect(); onSeek(((e.clientX - r.left) / r.width) * dur) }}>
            <rect x="0" y="3" width={W} height="8" rx="4" fill="#141b29" />
            {a.segments.map((g, i) => <rect key={i} x={X(g[0])} y="3" width={Math.max(1.5, X(g[1] - g[0] + 1))} height="8" fill={STATUS_COLOR[g[2]]} opacity="0.92" />)}
            <line x1={X(t)} x2={X(t)} y1="0" y2="14" stroke="#fff" strokeWidth="1.5" />
          </svg>
          <span className="amb-m">{a.metrics ? `${fmt(a.metrics.travel_s, 0)} s${a.metrics.finished ? '' : '+'}` : ''}</span>
        </div>
      ))}
      {!s.ambulances.length && <div className="muted small">No ambulance appears in this run.</div>}
    </div>
  )
}

function DiffTable({ rep }: { rep: Replay }) {
  const amb = new Set(['ev_travel_mean_s', 'ev_stopped_red_s', 'ev_stopped_queue_s'])
  const rows = (pred: (k: string) => boolean) => rep.diff.filter((d) => pred(d.key)).map((d) => (
    <tr key={d.key}><td>{d.label}{d.unit && <small> ({d.unit})</small>}</td><td className="num">{fmt(d.left, 1)}</td><td className="num">{fmt(d.right, 1)}</td>
      <td className="num">{signed(d.diff)}{d.pct !== null && <small> ({signed(d.pct, 1)}%)</small>}</td>
      <td>{d.verdict === 'same' ? <span className="tag">same</span> : d.verdict === 'right better' ? <span className="tag ok">right better</span> : <span className="tag bad">left better</span>}</td></tr>
  ))
  return (
    <div className="card">
      <h4>Measured differences for this run <small>both sides: same network, route file ({rep.demandSha}), SUMO seed {rep.seed}; two independent SUMO processes</small></h4>
      <table className="tbl"><thead><tr><th>All traffic</th><th className="num" style={{ color: LCOL }}>Left</th><th className="num" style={{ color: RCOL }}>Right</th><th className="num">Δ</th><th /></tr></thead>
        <tbody>{rows((k) => !amb.has(k))}</tbody>
        <thead><tr><th colSpan={5}>Ambulances</th></tr></thead>
        <tbody>{rows((k) => amb.has(k))}</tbody></table>
    </div>
  )
}
