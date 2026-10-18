import { useEffect, useState } from 'react'
import { fmt, getJSON, signed } from '../lib/api'
import type { Comparison, Pair, PairBlock } from '../lib/types'

interface Metric { key: string; label: string; unit: string; lowerBetter: boolean; group: 'traffic' | 'ambulance' }
const METRICS: Metric[] = [
  { key: 'mean_wait_civilian_s', label: 'Mean wait, everyone except ambulances', unit: 's', lowerBetter: true, group: 'traffic' },
  { key: 'mean_travel_s', label: 'Mean travel time', unit: 's', lowerBetter: true, group: 'traffic' },
  { key: 'throughput_vph', label: 'Throughput', unit: 'veh/h', lowerBetter: false, group: 'traffic' },
  { key: 'queue_total_mean_veh', label: 'Mean queued vehicles', unit: 'veh', lowerBetter: true, group: 'traffic' },
  { key: 'co2_kg', label: 'CO₂ estimate (HBEFA3, assumed fleet)', unit: 'kg', lowerBetter: true, group: 'traffic' },
  { key: 'teleports', label: 'Teleports per run', unit: '', lowerBetter: true, group: 'traffic' },
  { key: 'ev_travel_mean_s', label: 'Ambulance travel time', unit: 's', lowerBetter: true, group: 'ambulance' },
  { key: 'ev_stopped_red_s', label: 'Ambulance stopped at red lights', unit: 's', lowerBetter: true, group: 'ambulance' },
  { key: 'ev_stopped_queue_s', label: 'Ambulance stuck behind queues', unit: 's', lowerBetter: true, group: 'ambulance' },
]
const EFFECTS: { key: keyof Comparison['summary']; title: string; sub: string }[] = [
  { key: 'ml_ev_vs_heuristic', title: 'The demo pair', sub: 'No ML, no ambulance priority  →  ML forecast + lights cleared ahead of ambulances' },
  { key: 'ml_vs_heuristic', title: 'ML forecast alone', sub: 'Does the forecast improve traffic when neither side gives ambulances priority?' },
  { key: 'ev_rule_effect_on_heuristic', title: 'Ambulance green rule alone', sub: 'Same heuristic, with and without turning the lights ahead of ambulances green (no ML either side)' },
  { key: 'heuristic_vs_fixed', title: 'Reference: adaptive heuristic vs fixed-time', sub: 'How much any adaptive control helps on this grid' },
]

type Verdict = 'better' | 'worse' | 'unclear'
function verdict(p: Pair, m: Metric): Verdict {
  const [lo, hi] = p.ci95
  if (lo > 0) return m.lowerBetter ? 'worse' : 'better'
  if (hi < 0) return m.lowerBetter ? 'better' : 'worse'
  return 'unclear'
}
const COLOR = { better: '#34d399', worse: '#f87171', unclear: '#94a3b8' }

function Forest({ block, metrics }: { block: PairBlock; metrics: Metric[] }) {
  const rows = metrics.filter((m) => block[m.key] && block[m.key].left_mean > 1e-9)
  const pct = (p: Pair, v: number) => (100 * v) / p.left_mean
  const lim = Math.max(8, ...rows.map((m) => Math.max(Math.abs(pct(block[m.key], block[m.key].ci95[0])), Math.abs(pct(block[m.key], block[m.key].ci95[1])))))
  const ext = Math.min(100, Math.ceil(lim / 5) * 5)
  const W = 520, L = 230, R = 14, X = (v: number) => L + ((v + ext) / (2 * ext)) * (W - L - R)
  return (
    <svg viewBox={`0 0 ${W} ${rows.length * 26 + 30}`} className="forest">
      <line x1={X(0)} x2={X(0)} y1="6" y2={rows.length * 26 + 8} stroke="#475569" />
      {[-ext, -ext / 2, ext / 2, ext].map((v) => <text key={v} x={X(v)} y={rows.length * 26 + 24} fontSize="9" fill="#64748b" textAnchor="middle">{v > 0 ? '+' : ''}{v}%</text>)}
      <text x={X(-ext / 2)} y="12" fontSize="9" fill="#64748b" textAnchor="middle">lower</text><text x={X(ext / 2)} y="12" fontSize="9" fill="#64748b" textAnchor="middle">higher</text>
      {rows.map((m, i) => {
        const p = block[m.key], y = 24 + i * 26, c = COLOR[verdict(p, m)]
        return (
          <g key={m.key}>
            <text x={L - 10} y={y + 4} fontSize="11" fill="#cbd5e1" textAnchor="end">{m.label.replace(', everyone except ambulances', ' (non-ambulance)')}</text>
            <line x1={X(pct(p, p.ci95[0]))} x2={X(pct(p, p.ci95[1]))} y1={y} y2={y} stroke={c} strokeWidth="3" strokeLinecap="round" />
            <circle cx={X(pct(p, p.mean_diff))} cy={y} r="4.5" fill={c} stroke="#0b1019" strokeWidth="1.5" />
          </g>
        )
      })}
    </svg>
  )
}

function Reading({ block, n }: { block: PairBlock; n: number }) {
  const by: Record<Verdict, string[]> = { better: [], worse: [], unclear: [] }
  for (const m of METRICS) if (block[m.key]) by[verdict(block[m.key], m)].push(m.label.replace(', everyone except ambulances', ' (non-ambulance)'))
  return (
    <p className="small reading">
      Across <b>{n}</b> paired held-out runs{by.better.length > 0 && <>, <b className="good">clearly better</b>: {by.better.join('; ')}</>}
      {by.worse.length > 0 && <>; <b className="bad">clearly worse</b>: {by.worse.join('; ')}</>}
      {by.unclear.length > 0 && <>; <b>no clear difference</b>: {by.unclear.join('; ')}</>}.
    </p>
  )
}

export default function Results() {
  const [c, setC] = useState<Comparison | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { getJSON<Comparison>('/api/lab/comparison').then(setC).catch((e) => setErr(String(e))) }, [])
  if (err) return <div className="error">No evaluation yet: {err}</div>
  if (!c) return <div className="card">Loading…</div>
  const s = c.summary
  return (
    <div className="results">
      <div className="card intro">
        <h3>How the simulation treats ambulances</h3>
        <p>The simulator gives an ambulance <b>no special treatment</b>, matching the main FlowState simulation: it stops at red lights and waits in the same queues as everyone else, and no driver pulls over.
          Any help comes only from the signal controller. On the ML side the controller turns the lights <b>ahead of an ambulance green</b> as soon as the safety layer allows (minimum green, amber and all-red are never skipped).
          The ambulance still cannot pass queued cars, so congestion in front of it still delays it. That is what the ambulance bars on the Compare tab show.</p>
        <p className="small muted">Held-out test: {s.episodes} paired runs (5 scenarios × {s.seeds.length} unseen seeds). Intervals are 95% bootstrap CIs of the paired difference, shown as % of the left side's mean. A bar that crosses zero means no reliable difference.
          The ambulance green rule is the controller's existing emergency rule, not machine learning, which is why its effect is reported separately from the forecast's.</p>
      </div>
      {EFFECTS.map((e) => {
        const block = s[e.key] as PairBlock | undefined
        if (!block) return <div className="card muted" key={e.key}><h4>{e.title}</h4>Not in the stored evaluation yet. Run <code>python -m flowstate.lab.cli evaluate</code>.</div>
        return (
          <div className="card" key={e.key}>
            <h4>{e.title} <small>{e.sub}</small></h4>
            <Reading block={block} n={block.mean_wait_s?.pairs ?? s.episodes} />
            <div className="two">
              <Forest block={block} metrics={METRICS} />
              <table className="tbl">
                <thead><tr><th>Metric</th><th className="num">Left</th><th className="num">Right</th><th className="num">Δ</th><th className="num">95% CI</th><th className="num">right better / worse</th></tr></thead>
                <tbody>{METRICS.filter((m) => block[m.key]).map((m) => {
                  const p = block[m.key], v = verdict(p, m)
                  return <tr key={m.key}><td>{m.label}</td><td className="num">{fmt(p.left_mean, 1)}</td><td className="num">{fmt(p.right_mean, 1)}</td>
                    <td className="num" style={{ color: COLOR[v] }}>{signed(p.mean_diff, 2)}</td><td className="num">[{fmt(p.ci95[0], 2)}, {fmt(p.ci95[1], 2)}]</td>
                    <td className="num">{p.better} / {p.worse}</td></tr>
                })}</tbody>
              </table>
            </div>
          </div>
        )
      })}
      <div className="card small muted">ML influence in the forecast-only arm: the forecast changed {s.ml_influence.changed.toLocaleString()} of {s.ml_influence.decisions.toLocaleString()} eligible signal decisions
        ({fmt((100 * s.ml_influence.changed) / Math.max(1, s.ml_influence.decisions), 1)}%).</div>
    </div>
  )
}
