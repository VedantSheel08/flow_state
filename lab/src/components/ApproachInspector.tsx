import { useState } from 'react'
import { fmt } from '../lib/api'
import type { Geometry, Obs } from '../lib/types'

export default function ApproachInspector({
  geom,
  left,
  right,
  selected,
  onSelect,
}: {
  geom: Geometry
  left?: Obs
  right?: Obs
  selected: number | null
  onSelect: (index: number) => void
}) {
  const [junction, setJunction] = useState('all')
  return (
    <section className="approach-inspector">
      <div className="section-heading">
        <h3>Individual intersections</h3>
        <label>
          Intersection{' '}
          <select
            aria-label="Intersection"
            value={junction}
            onChange={(e) => setJunction(e.target.value)}
          >
            <option value="all">All intersections</option>
            {geom.controlled.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="small muted">
        Observed queues: left at {left?.t ?? '—'}s, right at {right?.t ?? '—'}s.
        Forecasts are made at those observation times for the labeled future
        horizon. Values are vehicles. Select an approach to highlight it on both
        maps.
      </p>
      <div className="table-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>Approach</th>
              <th className="num">Observed left</th>
              <th className="num">Observed right</th>
              <th className="num">Predicted left +30s</th>
              <th className="num">Predicted right +30s</th>
              <th className="num">Predicted right +60s</th>
            </tr>
          </thead>
          <tbody>
            {geom.approaches
              .map((a, i) => ({ a, i }))
              .filter(
                ({ a }) =>
                  junction === 'all' || a.key.split(':')[0] === junction,
              )
              .map(({ a, i }) => (
                <tr key={a.key} className={selected === i ? 'sel' : ''}>
                  <td>
                    <button
                      className="table-link"
                      aria-label={`Inspect ${a.key}`}
                      aria-pressed={selected === i}
                      onClick={() => onSelect(i)}
                    >
                      {a.key}
                    </button>
                  </td>
                  <td className="num">{fmt(left?.q[i], 0)}</td>
                  <td className="num">{fmt(right?.q[i], 0)}</td>
                  <td className="num pred">{fmt(left?.p30?.[i], 1)}</td>
                  <td className="num pred">{fmt(right?.p30?.[i], 1)}</td>
                  <td className="num pred">{fmt(right?.p60?.[i], 1)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
