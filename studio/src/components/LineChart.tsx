export interface Series { name: string; color: string; points: [number, number][]; dash?: boolean; area?: boolean }
export interface Marker { x: number; color: string; title?: string }
interface Props { series: Series[]; height?: number; xMax?: number; cursor?: number; markers?: Marker[]; onSeek?: (x: number) => void; unit?: string }

/** Small dependency-free SVG line chart; x is simulated seconds. Click or drag to seek. */
export default function LineChart({ series, height = 130, xMax, cursor, markers, onSeek, unit }: Props) {
  const W = 640, H = height, L = 40, B = 18, T = 10, R = 10
  const all = series.flatMap((s) => s.points)
  const xm = xMax ?? Math.max(60, ...all.map((p) => p[0]))
  const ym = Math.max(1, ...all.map((p) => p[1])) * 1.12
  const X = (x: number) => L + (x / xm) * (W - L - R)
  const Y = (y: number) => H - B - (y / ym) * (H - B - T)
  const seek = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!onSeek) return
    const r = e.currentTarget.getBoundingClientRect()
    onSeek(Math.max(0, Math.min(xm, ((((e.clientX - r.left) / r.width) * W - L) / (W - L - R)) * xm)))
  }
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height, cursor: onSeek ? 'pointer' : undefined }} onMouseDown={seek} onMouseMove={(e) => e.buttons === 1 && seek(e)}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={L} x2={W - R} y1={Y(f * ym)} y2={Y(f * ym)} stroke="#1c2536" />
            <text x={L - 5} y={Y(f * ym) + 3} fontSize="9" fill="#64748b" textAnchor="end">{(f * ym).toFixed(ym < 10 ? 1 : 0)}</text>
          </g>
        ))}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => <text key={f} x={X(f * xm)} y={H - 4} fontSize="9" fill="#64748b" textAnchor="middle">{Math.round(f * xm)}s</text>)}
        {series.map((s) => {
          const pts = s.points.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ')
          return (
            <g key={s.name}>
              {s.area && s.points.length > 1 && <polygon points={`${X(s.points[0][0])},${Y(0)} ${pts} ${X(s.points[s.points.length - 1][0])},${Y(0)}`} fill={s.color} opacity="0.12" />}
              <polyline fill="none" stroke={s.color} strokeWidth="1.8" strokeDasharray={s.dash ? '5 3' : undefined} points={pts} />
            </g>
          )
        })}
        {markers?.map((m, i) => <line key={i} x1={X(m.x)} x2={X(m.x)} y1={T} y2={T + 7} stroke={m.color} strokeWidth="1.6"><title>{m.title}</title></line>)}
        {cursor !== undefined && <line x1={X(cursor)} x2={X(cursor)} y1={T} y2={H - B} stroke="#e2e8f0" strokeWidth="1" />}
      </svg>
      <div className="legend">{series.map((s) => <span key={s.name}><i style={{ background: s.dash ? 'transparent' : s.color, borderColor: s.color }} className={s.dash ? 'dash' : ''} />{s.name}</span>)}{unit && <em>{unit}</em>}</div>
    </div>
  )
}
