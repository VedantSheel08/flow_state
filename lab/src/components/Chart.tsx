export interface Series {
  name: string
  color: string
  points: [number, number][]
  dash?: boolean
  width?: number
}

interface Props {
  series: Series[]
  height?: number
  yLabel?: string
  cursor?: number // vertical marker at this x (e.g. playback time)
  markers?: { x: number; color: string; title?: string }[]
  xMax?: number
  onSeek?: (x: number) => void
}

/** Minimal SVG line chart (no dependencies). x is simulated seconds. */
export default function Chart({
  series,
  height = 140,
  yLabel,
  cursor,
  markers,
  xMax,
  onSeek,
}: Props) {
  const W = 600,
    H = height,
    L = 38,
    B = 18,
    T = 8,
    Rr = 8
  const all = series.flatMap((s) => s.points)
  const xm = xMax ?? Math.max(60, ...all.map((p) => p[0]))
  const ym = Math.max(1, ...all.map((p) => p[1])) * 1.1
  const X = (x: number) => L + (x / xm) * (W - L - Rr)
  const Y = (y: number) => H - B - (y / ym) * (H - B - T)
  const ticks = [0, ym / 2, ym]
  const seek = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!onSeek) return
    const r = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * W
    onSeek(Math.max(0, Math.min(xm, ((x - L) / (W - L - Rr)) * xm)))
  }
  return (
    <div className="chart">
      <svg
        role="img"
        aria-label={
          yLabel ??
          `Measured timeline: ${series.map((s) => s.name).join(' and ')}`
        }
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ height, cursor: onSeek ? 'pointer' : undefined }}
        onClick={seek}
        onMouseDown={seek}
        onMouseMove={(e) => e.buttons === 1 && seek(e)}
      >
        {ticks.map((v, i) => (
          <g key={i}>
            <line
              x1={L}
              x2={W - Rr}
              y1={Y(v)}
              y2={Y(v)}
              stroke="#e0e6e8"
              strokeDasharray="2 4"
            />
            <text
              x={L - 4}
              y={Y(v) + 3}
              fontSize="9"
              fill="#64748b"
              textAnchor="end"
            >
              {v.toFixed(ym < 10 ? 1 : 0)}
            </text>
          </g>
        ))}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <text
            key={f}
            x={X(f * xm)}
            y={H - 4}
            fontSize="9"
            fill="#64748b"
            textAnchor="middle"
          >
            {Math.round(f * xm)}s
          </text>
        ))}
        {markers?.map((m, i) => (
          <line
            key={i}
            x1={X(m.x)}
            x2={X(m.x)}
            y1={T}
            y2={T + 6}
            stroke={m.color}
            strokeWidth="1.5"
          >
            <title>{m.title}</title>
          </line>
        ))}
        {series.map((s) => (
          <polyline
            key={s.name}
            fill="none"
            stroke={s.color}
            strokeWidth={s.width ?? 1.6}
            strokeDasharray={s.dash ? '4 3' : undefined}
            points={s.points
              .map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`)
              .join(' ')}
          />
        ))}
        {cursor !== undefined && (
          <line
            x1={X(cursor)}
            x2={X(cursor)}
            y1={T}
            y2={H - B}
            stroke="#58747c"
            strokeWidth="1"
            strokeDasharray="3 3"
          />
        )}
        {yLabel && (
          <text x={L + 4} y={T + 9} fontSize="9" fill="#94a3b8">
            {yLabel}
          </text>
        )}
      </svg>
      <div className="legend">
        {series.map((s) => (
          <span key={s.name}>
            <i
              style={{
                background: s.dash ? 'transparent' : s.color,
                borderColor: s.color,
              }}
              className={s.dash ? 'dash' : ''}
            />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  )
}
