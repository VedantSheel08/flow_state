import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Chart from '../components/Chart'
import MapCanvas, { type DrawData, type View } from '../components/MapCanvas'
import { fmt, getJSON, postJSON } from '../lib/api'
import {
  CHART_METRICS,
  chartValue,
  displayPrecision,
  frameAt,
  framePair,
  signed,
  waitFinding,
  type ChartMetric,
} from '../lib/comparison'
import { interpolate, obsAt } from '../lib/interp'
import {
  CONTROLLER_LABEL,
  KPI,
  type DiffRow,
  type Geometry,
  type Replay,
  type ReplaySummary,
} from '../lib/types'

type Pair = {
  mean_diff: number
  ci95: [number, number]
  pairs: number
  better: number
  worse: number
  left_mean: number
  right_mean: number
}
interface EvalSummary {
  summary: Record<string, Record<string, Pair>>
}
interface Props {
  geom: Geometry
  scenarios: { id: string; title: string }[]
  mlAvailable: boolean
  onReplay: (id: string | null) => void
  pending?: Record<string, unknown> | null
}
const LCOL = '#8c9eab',
  RCOL = '#39bca6'
const sideName = (c: string) =>
  c === 'ml_forecast'
    ? 'With ML'
    : c === 'queue_heuristic'
      ? 'Without ML'
      : 'Fixed-time'
const subName = (c: string) =>
  c === 'ml_forecast'
    ? 'Forecast-assisted controller'
    : c === 'queue_heuristic'
      ? 'Queue-responsive heuristic'
      : '30-second signal cycle'

export default function CompareView({
  geom,
  scenarios,
  mlAvailable,
  onReplay,
  pending,
}: Props) {
  const [index, setIndex] = useState<ReplaySummary[]>([])
  const [rid, setRid] = useState<string | null>(null)
  const [rep, setRep] = useState<Replay | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(8)
  const [chartMetric, setChartMetric] = useState<ChartMetric>('queue')
  const [t, setT] = useState(1)
  const [ev, setEv] = useState<EvalSummary | null>(null)
  const [form, setForm] = useState({
    left: 'queue_heuristic',
    right: 'ml_forecast',
    scenario: 'rush_ramp',
    seed: 3200,
    intensity: 1.18,
  })
  const [recordOpen, setRecordOpen] = useState(false)
  const [job, setJob] = useState<{
    id: string
    state: string
    error?: string
    wall_s?: number
  } | null>(null)
  const [revision, setRevision] = useState(0)
  const tref = useRef(1)
  const view = useRef<View | null>(null)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      clearTimeout(pollTimer.current)
    }
  }, [])
  const loadIndex = useCallback(async (select?: string) => {
    const r = await getJSON<{ replays: ReplaySummary[] }>('/api/lab/replays')
    if (!alive.current) return
    setIndex(r.replays)
    setRid(
      (cur) =>
        select ??
        cur ??
        r.replays.find(
          (p) => p.left === 'queue_heuristic' && p.right === 'ml_forecast',
        )?.id ??
        r.replays[0]?.id ??
        null,
    )
    if (!r.replays.length) setLoading(false)
  }, [])
  useEffect(() => {
    loadIndex().catch((e) => {
      setError(String(e))
      setLoading(false)
    })
    getJSON<EvalSummary>('/api/lab/comparison')
      .then(setEv)
      .catch(() => undefined)
  }, [loadIndex])
  useEffect(() => {
    if (!rid) return
    let active = true
    setLoading(true)
    setPlaying(false)
    setRep(null)
    setError(null)
    onReplay(null)
    getJSON<Replay>(`/api/lab/replays/${rid}`)
      .then((r) => {
        if (!active) return
        const start = Math.min(
          r.left.frames[0]?.t ?? 1,
          r.right.frames[0]?.t ?? 1,
        )
        setRep(r)
        tref.current = start
        setT(start)
        setPlaying(true)
        onReplay(rid)
      })
      .catch((e) => {
        if (active) setError(String(e))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [rid, revision, onReplay])
  useEffect(() => {
    if (pending) {
      setForm((f) => ({ ...f, ...pending }))
      setRecordOpen(true)
    }
  }, [pending])
  const start = rep
    ? Math.min(rep.left.frames[0]?.t ?? 1, rep.right.frames[0]?.t ?? 1)
    : 1
  const dur = rep
    ? Math.max(rep.left.frames.at(-1)?.t ?? 1, rep.right.frames.at(-1)?.t ?? 1)
    : 1
  const seek = (x: number) => {
    tref.current = Math.max(start, Math.min(dur, x))
    setT(tref.current)
  }
  useEffect(() => {
    if (!playing || !rep) return
    let raf = 0,
      prev = performance.now(),
      lastUi = 0
    const loop = (now: number) => {
      tref.current = Math.min(
        dur,
        tref.current + (Math.max(0, now - prev) / 1000) * speed,
      )
      prev = now
      if (now - lastUi > 120) {
        setT(tref.current)
        lastUi = now
      }
      if (tref.current >= dur) {
        setPlaying(false)
        setT(dur)
        return
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, rep, dur])
  const sources = useMemo(
    () =>
      (['left', 'right'] as const).map((side) => (): DrawData | null => {
        if (!rep) return null
        const run = rep[side],
          tt = tref.current
        const { frame, next, fraction } = framePair(run, tt)
        if (!frame) return null
        const o = obsAt(run.obs, tt)
        return {
          vehicles: interpolate(frame, next, fraction),
          signals: frame.s,
          queue: o?.q,
          mlJunctions: (run.mlLog ?? [])
            .filter(([ts]) => ts <= tt && tt - ts < 6)
            .map(([, j]) => j),
        }
      }),
    [rep],
  )
  const series = useMemo(
    () =>
      rep
        ? {
            lines: (['left', 'right'] as const).map((side, i) => ({
              name: `${i ? 'Right' : 'Left'} · ${sideName(rep[side].controller)}`,
              color: i ? RCOL : LCOL,
              points: rep[side].frames
                .filter((_, n, a) => n % 5 === 0 || n === a.length - 1)
                .map(
                  (f) =>
                    [f.t, chartValue(rep[side], f, chartMetric)] as [
                      number,
                      number,
                    ],
                ),
            })),
            markers: (['left', 'right'] as const).flatMap((side, i) =>
              (rep[side].mlLog ?? []).map(([ts, j, d]) => ({
                x: ts,
                color: i ? RCOL : LCOL,
                title: `${side === 'left' ? 'Left' : 'Right'} · ${geom.controlled[j]} · ${d > 0 ? 'requested earlier switch' : 'held green longer'} · ${ts}s`,
              })),
            ),
          }
        : null,
    [rep, geom, chartMetric],
  )
  const record = async () => {
    try {
      const j = await postJSON<{ id: string; state: string }>(
        '/api/lab/replays',
        form,
      )
      setJob(j)
      const poll = async () => {
        if (!alive.current) return
        try {
          const s = await getJSON<{
            id: string
            state: string
            error?: string
            wall_s?: number
            result?: { id: string }
          }>(`/api/lab/jobs/${j.id}`)
          if (!alive.current) return
          setJob(s)
          if (s.state === 'running') pollTimer.current = setTimeout(poll, 1000)
          else if (s.state === 'done' && s.result) {
            await loadIndex(s.result.id)
            setRevision((v) => v + 1)
          }
        } catch (e) {
          if (alive.current)
            setJob({ id: j.id, state: 'error', error: String(e) })
        }
      }
      pollTimer.current = setTimeout(poll, 1000)
    } catch (e) {
      setJob({ id: '-', state: 'error', error: String(e) })
    }
  }
  const ti = Math.floor(t)
  const kl = rep && frameAt(rep.left.frames, t)?.k,
    kr = rep && frameAt(rep.right.frames, t)?.k
  const pairKey = rep && `${rep.left.controller}|${rep.right.controller}`
  const block = pairKey
    ? ev?.summary[
        pairKey === 'queue_heuristic|ml_forecast'
          ? 'ml_vs_heuristic'
          : pairKey === 'fixed_time|queue_heuristic'
            ? 'heuristic_vs_fixed'
            : pairKey === 'fixed_time|ml_forecast'
              ? 'ml_vs_fixed'
              : ''
      ]
    : undefined
  const scenarioName = (id: string) =>
    scenarios.find((s) => s.id === id)?.title ?? id
  return (
    <div className="compare">
      <div className="experiment-bar">
        <label className="recorded-label">
          Recorded experiment
          <select
            aria-label="Recorded experiment"
            value={rid ?? ''}
            onChange={(e) => setRid(e.target.value)}
          >
            {index.map((r) => (
              <option key={r.id} value={r.id}>
                {scenarioName(r.scenario)} · {r.intensity}× demand · seed{' '}
                {r.seed} · {sideName(r.left)} / {sideName(r.right)}
              </option>
            ))}
          </select>
        </label>
        <div className="experiment-meta">
          <span className="measured-dot" />
          Same demand & seed
          <span className="meta-divider" />
          <button
            className="quiet"
            aria-expanded={recordOpen}
            aria-controls="record-pair"
            onClick={() => setRecordOpen(!recordOpen)}
          >
            New experiment <span aria-hidden="true">+</span>
          </button>
        </div>
      </div>
      <details
        className="disclosure record"
        id="record-pair"
        open={recordOpen}
        onToggle={(e) => setRecordOpen(e.currentTarget.open)}
      >
        <summary>
          Configure a paired experiment{' '}
          <span>Scenario, demand, controllers & seed</span>
        </summary>
        <form
          className="disclosure-body toolbar"
          onSubmit={(e) => {
            e.preventDefault()
            record()
          }}
        >
          {(['left', 'right'] as const).map((s) => (
            <label key={s}>
              {s === 'left' ? 'Left controller' : 'Right controller'}
              <select
                value={form[s]}
                onChange={(e) => setForm({ ...form, [s]: e.target.value })}
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
          ))}
          <label>
            Scenario
            <select
              value={form.scenario}
              onChange={(e) => setForm({ ...form, scenario: e.target.value })}
            >
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Seed
            <input
              type="number"
              required
              min={0}
              max={99999}
              step={1}
              value={form.seed}
              onChange={(e) => setForm({ ...form, seed: +e.target.value })}
            />
          </label>
          <label>
            Demand
            <select
              value={form.intensity}
              onChange={(e) => setForm({ ...form, intensity: +e.target.value })}
            >
              <option value={0.7}>Light · 0.7×</option>
              <option value={1}>Standard · 1.0×</option>
              <option value={1.18}>Stress · 1.18×</option>
            </select>
          </label>
          <button
            className="primary"
            disabled={
              job?.state === 'running' ||
              form.left === form.right ||
              (!mlAvailable &&
                (form.left === 'ml_forecast' || form.right === 'ml_forecast'))
            }
          >
            Run both in SUMO <span aria-hidden="true">↗</span>
          </button>
          {form.left === form.right && (
            <p className="small">Choose two different controllers.</p>
          )}
          {job && (
            <p className={job.error ? 'error' : 'small muted'} role="status">
              {job.state === 'running'
                ? 'Recording two SUMO runs… The current replay stays available.'
                : job.state === 'done'
                  ? `Experiment ready · ${job.wall_s}s to record`
                  : job.error}
            </p>
          )}
        </form>
      </details>
      {error && (
        <div className="error" role="alert">
          Could not load the comparison. {error}
          <button
            onClick={() => {
              loadIndex().catch((e) => setError(String(e)))
              setRevision((v) => v + 1)
            }}
          >
            Retry
          </button>
        </div>
      )}
      {loading && (
        <div className="loading-state" role="status">
          Loading measured traffic…
        </div>
      )}
      {!loading && !index.length && !error && (
        <div className="loading-state">
          No recorded experiments yet. Configure a paired experiment above to
          begin.
        </div>
      )}
      {rep && (
        <>
          <div className="pair">
            {(['left', 'right'] as const).map((side, i) => (
              <section
                className={`side-run ${side}`}
                key={side}
                aria-label={`${side} simulation`}
              >
                <div className="sim-heading">
                  <div>
                    <span className="run-index">0{i + 1}</span>
                    <h2>{sideName(rep[side].controller)}</h2>
                    <span className="sim-subtitle">
                      {subName(rep[side].controller)}
                    </span>
                  </div>
                  <span
                    className={`controller-tag ${rep.mlInControl[side] ? 'ml' : ''}`}
                  >
                    {rep.mlInControl[side]
                      ? 'Forecast in control'
                      : rep[side].controller === 'fixed_time'
                        ? 'Scheduled control'
                        : 'Reactive control'}
                  </span>
                </div>
                <div className="simulation">
                  <MapCanvas
                    geom={geom}
                    source={sources[i]}
                    view={view}
                    accent={i ? RCOL : LCOL}
                    label={`${sideName(rep[side].controller)} traffic simulation`}
                  />
                  <div className="map-overlay">
                    <span>
                      <i />
                      Measured SUMO replay
                    </span>
                    <span>{geom.controlled.length} signals</span>
                  </div>
                  <span className="map-compass" aria-hidden="true">
                    N ↑
                  </span>
                </div>
                <div className="sim-footer">
                  <span>
                    <i
                      className="legend-line"
                      style={{ background: i ? RCOL : LCOL }}
                    />
                    {fmt((i ? kr : kl)?.[KPI.inNetwork], 0)} vehicles in network
                  </span>
                  <span>
                    {rep[side].frames.at(-1)!.t < t
                      ? 'Run complete'
                      : `${ti}s simulated`}
                  </span>
                </div>
              </section>
            ))}
          </div>
          <div className="playback">
            <button
              className="play-button"
              aria-label={playing ? 'Pause replay' : 'Play replay'}
              onClick={() => {
                if (t >= dur) seek(start)
                setPlaying(!playing)
              }}
            >
              <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>
              {playing ? 'Pause' : t >= dur ? 'Replay' : 'Play'}
            </button>
            <button
              className="restart-button"
              aria-label="Restart replay"
              onClick={() => {
                seek(start)
                setPlaying(false)
              }}
            >
              ↺
            </button>
            <div className="timeline">
              <label htmlFor="replay-time" className="sr-only">
                Simulation timeline
              </label>
              <input
                id="replay-time"
                type="range"
                min={start}
                max={dur}
                step={1}
                value={ti}
                onChange={(e) => seek(+e.target.value)}
              />
              <div className="timeline-labels">
                <span>
                  {ti.toLocaleString()}s{' '}
                  <span className="muted">/ {dur.toLocaleString()}s</span>
                </span>
                <span>Synchronized · Drag to explore</span>
              </div>
            </div>
            <label className="speed-label">
              <span className="sr-only">Playback speed</span>
              <select value={speed} onChange={(e) => setSpeed(+e.target.value)}>
                {[1, 2, 4, 8, 16, 32].map((s) => (
                  <option key={s} value={s}>
                    {s}× speed
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="map-legend compare-legend">
            <span>
              <i style={{ background: '#e9a85d' }} />
              Slow traffic
            </span>
            <span>
              <i style={{ background: '#e06f64' }} />
              Stopped traffic
            </span>
            <span>
              <i className="ring" />
              Logged ML intervention
            </span>
            <span className="legend-hint">
              Scroll to zoom · Drag to pan · Double-click to fit · Views linked
            </span>
          </div>
          <div className="section-heading metrics-heading">
            <h2>Measured outcomes</h2>
            <span>Selected experiment · Right minus left</span>
          </div>
          <div className="outcomes">
            <Outcome
              row={rep.diff.find((d) => d.key === 'mean_wait_s')}
              title="Average wait time"
              scope="Completed trips · Full run"
              unit="s"
            />
            <Outcome
              row={rep.diff.find((d) => d.key === 'throughput_vph')}
              title="Throughput"
              scope="900-second demand window"
              unit="veh/h"
              digits={0}
              lower={false}
            />
            <Outcome
              row={
                kl && kr
                  ? {
                      key: 'queue',
                      label: '',
                      unit: 'veh',
                      left: kl[KPI.halting],
                      right: kr[KPI.halting],
                      diff: kr[KPI.halting] - kl[KPI.halting],
                      pct: null,
                      verdict: '',
                    }
                  : undefined
              }
              title="Total queued vehicles"
              scope={`On approaches · At ${ti}s`}
              unit="veh"
              digits={0}
            />
            <Outcome
              row={rep.diff.find((d) => d.key === 'spillback_pct')}
              title="Blocked road time"
              scope="Spillback · Full run"
              unit="%"
              deltaUnit="pp"
            />
          </div>
          <div className="analysis-row">
            <section className="timeline-panel">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">Observed over time</span>
                  <h2>{CHART_METRICS[chartMetric].title}</h2>
                </div>
                <label className="chart-select">
                  <span className="sr-only">Comparison chart metric</span>
                  <select
                    value={chartMetric}
                    onChange={(e) =>
                      setChartMetric(e.target.value as ChartMetric)
                    }
                  >
                    <option value="queue">Total queue</option>
                    <option value="wait">Current wait</option>
                    <option value="throughput">Throughput</option>
                  </select>
                </label>
              </div>
              <p className="chart-units">{CHART_METRICS[chartMetric].label}</p>
              {series && (
                <Chart
                  series={series.lines}
                  cursor={t}
                  markers={series.markers}
                  xMax={dur}
                  onSeek={(x) => seek(Math.round(x))}
                  height={190}
                />
              )}
              <p className="chart-note">
                <span className="intervention-tick" />
                Top ticks mark logged ML decision changes. Click the graph to
                move both simulations.
              </p>
              <p className="chart-note">{CHART_METRICS[chartMetric].note}</p>
            </section>
            <Explanation rep={rep} geom={geom} t={t} />
          </div>
          <Evidence block={block} />
          <details className="disclosure">
            <summary>
              Technical details{' '}
              <span>Full results, controls, provenance & safety events</span>
            </summary>
            <div className="disclosure-body">
              <div className="provenance">
                <div>
                  <span className="eyebrow">Controlled comparison</span>
                  <h3>One demand file. Two independent runs.</h3>
                  <p>
                    Same network, route file, SUMO seed {rep.seed}, demand{' '}
                    {rep.intensity}×, and signal safety limits. Demand runs for
                    900 seconds, followed by a drain period. Early finishes hold
                    their last measured frame.
                  </p>
                  <p className="mono">Demand hash (SHA-256 prefix): {rep.demandSha}</p>
                </div>
                <div>
                  <h3>How to read the metrics</h3>
                  <p>
                    Average wait covers completed trips. Throughput counts
                    arrivals during the demand window. Queue is the total
                    stopped vehicles on approaches at the playhead. Blocked road
                    time is the percentage of block-seconds with a queue filling
                    at least half a block.
                  </p>
                  <p>
                    CO₂ is a SUMO HBEFA3 estimate with an assumed fleet.
                    Teleports and collisions remain visible below.
                  </p>
                </div>
              </div>
              <div className="table-scroll">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Full-run measurement</th>
                      <th className="num">Left</th>
                      <th className="num">Right</th>
                      <th className="num">Δ right − left</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rep.diff.map((d) => (
                      <tr key={d.key}>
                        <td>
                          {d.label} <span className="muted">{d.unit}</span>
                        </td>
                        <td className="num">{fmt(d.left, 2)}</td>
                        <td className="num">{fmt(d.right, 2)}</td>
                        <td className="num">
                          {signed(d.diff, 2)}
                          {d.pct !== null && (
                            <small className="muted"> ({signed(d.pct)}%)</small>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {(['left', 'right'] as const).map(
                (side) =>
                  rep[side].ml && (
                    <p className="small" key={side}>
                      {side === 'left' ? 'Left' : 'Right'} ML controller changed{' '}
                      {rep[side].ml!.changed} of {rep[side].ml!.decisions}{' '}
                      eligible decisions: {rep[side].ml!.switchedEarlier}{' '}
                      earlier switch requests, {rep[side].ml!.heldLonger} longer
                      holds.
                    </p>
                  ),
              )}
              {block && (
                <div className="table-scroll">
                  <h3>Held-out paired evaluation</h3>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th className="num">Left mean</th>
                        <th className="num">Right mean</th>
                        <th className="num">Mean Δ</th>
                        <th className="num">95% bootstrap CI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(block)
                        .filter(([, v]) => typeof v === 'object' && v.ci95)
                        .map(([key, v]) => (
                          <tr key={key}>
                            <td>
                              {rep.diff.find((d) => d.key === key)?.label ??
                                key}
                            </td>
                            <td className="num">{fmt(v.left_mean, 2)}</td>
                            <td className="num">{fmt(v.right_mean, 2)}</td>
                            <td className="num">{signed(v.mean_diff, 2)}</td>
                            <td className="num">
                              {fmt(v.ci95[0], 2)} to {fmt(v.ci95[1], 2)}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </details>
        </>
      )}
    </div>
  )
}

function Outcome({
  row,
  title,
  scope,
  unit,
  deltaUnit = unit,
  digits = 1,
  lower = true,
}: {
  row?: DiffRow
  title: string
  scope: string
  unit: string
  deltaUnit?: string
  digits?: number
  lower?: boolean
}) {
  digits = displayPrecision(row?.diff ?? 0, digits)
  const change = row ? Number(row.diff.toFixed(digits)) : 0
  return (
    <section className="outcome">
      <h3>{title}</h3>
      <p>{scope}</p>
      <div className="outcome-values">
        <div>
          <span className="value-label">Left</span>
          <b>
            {fmt(row?.left, digits)}
            <small>{unit}</small>
          </b>
        </div>
        <span className="value-arrow" aria-hidden="true">
          →
        </span>
        <div>
          <span className="value-label">Right</span>
          <b>
            {fmt(row?.right, digits)}
            <small>{unit}</small>
          </b>
        </div>
      </div>
      <div
        className={`metric-delta ${change === 0 ? 'neutral' : change < 0 === lower ? 'positive' : 'negative'}`}
      >
        {row
          ? change === 0
            ? 'No difference'
            : `${signed(row.diff, digits)} ${deltaUnit}`
          : 'Measurement unavailable'}
        <span>{change !== 0 ? 'vs left' : ''}</span>
      </div>
    </section>
  )
}

function Explanation({
  rep,
  geom,
  t,
}: {
  rep: Replay
  geom: Geometry
  t: number
}) {
  const side = rep.mlInControl.right
    ? 'right'
    : rep.mlInControl.left
      ? 'left'
      : null
  const run = rep[side ?? 'right']
  const o = obsAt(run.obs, t),
    p = o?.p30
  const peak = p?.length ? p.indexOf(Math.max(...p)) : -1
  const events = (side ? (run.mlLog ?? []) : []).filter(([ts]) => ts <= t)
  const latest = events.at(-1)
  const wait = rep.diff.find((d) => d.key === 'mean_wait_s')
  return (
    <section className="explanation">
      <span className="eyebrow">The decision, explained</span>
      <h2>Prediction → action → outcome</h2>
      <div className="explanation-item">
        <span>01</span>
        <div>
          <h3>What did the model predict?</h3>
          <p>
            {o && p && peak >= 0 ? (
              <>
                <b>{fmt(p[peak], 1)} queued vehicles</b> at{' '}
                {geom.approaches[peak]?.key} by {o.t + 30}s. Measured at {o.t}s:{' '}
                {fmt(o.q[peak], 0)} vehicles.
              </>
            ) : (
              'No forecast is available at this point in the recording.'
            )}
          </p>
          <small className="prediction-label">
            {side
              ? `${side === 'right' ? 'Right' : 'Left'} controller · 30-second forecast`
              : 'Shadow forecast · Not used for control'}
          </small>
        </div>
      </div>
      <div className="explanation-item">
        <span>02</span>
        <div>
          <h3>What did it change?</h3>
          <p>
            {latest ? (
              <>
                {geom.controlled[latest[1]]}:{' '}
                <b>
                  {latest[2] > 0
                    ? 'requested an earlier switch'
                    : 'held green longer'}
                </b>{' '}
                at {latest[0]}s. {events.length} logged changes so far.
              </>
            ) : side ? (
              'No ML decision changes logged yet at this playhead.'
            ) : (
              'Neither selected controller uses ML to change signals.'
            )}
          </p>
          <small>
            Logged against the heuristic’s decision; signal safety limits apply.
          </small>
        </div>
      </div>
      <div className="explanation-item">
        <span>03</span>
        <div>
          <h3>Did the change help?</h3>
          <p>
            {wait ? (
              <>
                <b>{waitFinding(wait.diff)}.</b> Average wait:{' '}
                {signed(wait.diff, 2)}s for {sideName(rep.right.controller)}{' '}
                versus {sideName(rep.left.controller)}.
              </>
            ) : (
              'No completed-run comparison is available.'
            )}
          </p>
          <small>
            Full-run measured difference. It does not isolate the effect of one
            intervention.
          </small>
        </div>
      </div>
    </section>
  )
}
function Evidence({ block }: { block?: Record<string, Pair> }) {
  const w = block?.mean_wait_s
  return (
    <section className="evidence">
      <span className="evidence-label">Beyond this replay</span>
      <div>
        <h3>
          {!w
            ? 'Multi-seed evidence unavailable for this pairing'
            : w.ci95[1] < 0
              ? 'Lower average wait across held-out runs'
              : w.ci95[0] > 0
                ? 'Higher average wait across held-out runs'
                : 'No reliable improvement in average wait'}
        </h3>
        <p>
          {w ? (
            <>
              {w.pairs} held-out paired episodes · Left {fmt(w.left_mean, 1)}s /
              Right {fmt(w.right_mean, 1)}s · Mean Δ {signed(w.mean_diff, 2)}s ·
              95% CI [{fmt(w.ci95[0], 2)}, {fmt(w.ci95[1], 2)}]s.
            </>
          ) : (
            'A single replay cannot establish a reliable controller benefit.'
          )}{' '}
          {w &&
            w.ci95[0] <= 0 &&
            w.ci95[1] >= 0 &&
            'The interval includes zero; individual runs can go either way.'}
        </p>
      </div>
    </section>
  )
}
