import { useEffect, useState } from 'react'
import { fmt, getJSON } from '../lib/api'

interface Score { mae: number; rmse: number; r2: number; spill_precision: number; spill_recall: number; spill_f1: number }
interface Eval {
  kind: string; features: string[]; chosen: Record<string, Record<string, number>>; rows: Record<string, number>; train_wall_s: number; split: string
  test: Record<string, Record<string, Score> & { improvement_vs_persistence_mae_pct?: number }>
  inference_ms_per_decision: { mean: number; p95: number }; permutation_importance_p30: Record<string, number>; trained_at: string
}
const NAMES: Record<string, string> = { model: 'Gradient-boosted trees (ours)', ridge: 'Ridge regression', persistence: 'Persistence (queue stays the same)',
  linear_trend: 'Linear trend (last 30 s)', approach_mean: 'Historical mean per approach' }

export default function ForecastView() {
  const [ev, setEv] = useState<Eval | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { getJSON<Eval>('/api/lab/forecast').then(setEv).catch((e) => setErr(String(e))) }, [])
  if (err) return <div className="card error">Forecaster not available: {err}</div>
  if (!ev) return <div className="card">Loading…</div>
  const imp = Object.entries(ev.permutation_importance_p30).sort((a, b) => b[1] - a[1]).slice(0, 10)
  const im = Math.max(...imp.map((x) => x[1]), 1e-9)
  return (
    <div className="forecast">
      <div className="card">
        <h4>Queue forecaster <small>{ev.kind} · trained {ev.trained_at} in {ev.train_wall_s}s on CPU</small></h4>
        <p className="small">Predicts halting vehicles on each of the 16 signalised approaches 30 s and 60 s ahead from detector-like inputs (counts, halts, waiting, downstream occupancy,
          signal state, upstream platoons, 10–30 s history). Data: real SUMO runs. Split {ev.split}. Rows — train {ev.rows.train.toLocaleString()},
          validation {ev.rows.val.toLocaleString()}, test {ev.rows.test.toLocaleString()}. Hyper-parameters chosen on validation seeds only.</p>
        <p className="small">Inference: <b>{fmt(ev.inference_ms_per_decision.mean, 2)} ms</b> per control decision (16 approaches × 2 horizons, p95 {fmt(ev.inference_ms_per_decision.p95, 2)} ms).</p>
      </div>
      <div className="two">
        {(['p30', 'p60'] as const).map((h) => (
          <div className="card" key={h}>
            <h4>Held-out test, {h === 'p30' ? '30 s' : '60 s'} ahead <small>{ev.test[h].improvement_vs_persistence_mae_pct}% lower MAE than persistence</small></h4>
            <table className="tbl"><thead><tr><th>Method</th><th className="num">MAE (veh)</th><th className="num">RMSE</th><th className="num">R²</th><th className="num">Spillback F1</th><th className="num">Prec / Rec</th></tr></thead>
              <tbody>{Object.keys(NAMES).map((k) => { const s = ev.test[h][k] as Score; return s && <tr key={k} className={k === 'model' ? 'sel' : ''}>
                <td>{NAMES[k]}</td><td className="num">{fmt(s.mae, 2)}</td><td className="num">{fmt(s.rmse, 2)}</td><td className="num">{fmt(s.r2, 2)}</td><td className="num">{fmt(s.spill_f1, 2)}</td>
                <td className="num">{fmt(s.spill_precision, 2)} / {fmt(s.spill_recall, 2)}</td></tr> })}</tbody></table>
            <p className="small muted">Spillback = queue covering at least half the approach. Scored on the same test rows for every method.</p>
          </div>
        ))}
      </div>
      <div className="two">
        {ev.test.rush_ramp_only_p30 && <div className="card"><h4>Unseen scenario only (rush-hour ramp, 30 s)</h4>
          <table className="tbl"><tbody>{(['model', 'persistence'] as const).map((k) => { const s = ev.test.rush_ramp_only_p30[k] as Score; return <tr key={k}><td>{NAMES[k]}</td>
            <td className="num">MAE {fmt(s.mae, 2)}</td><td className="num">R² {fmt(s.r2, 2)}</td><td className="num">F1 {fmt(s.spill_f1, 2)}</td></tr> })}</tbody></table>
          <p className="small muted">This scenario never appears in training or validation data.</p></div>}
        <div className="card"><h4>What the 30 s model relies on <small>permutation importance on test rows (MAE increase)</small></h4>
          {imp.map(([f, v]) => <div className="bar-row" key={f}><span>{f}</span><div><i style={{ width: `${(100 * Math.max(0, v)) / im}%` }} /></div><b>{fmt(v, 3)}</b></div>)}
        </div>
      </div>
    </div>
  )
}
