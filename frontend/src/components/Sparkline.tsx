export function Sparkline({ values, width = 64, height = 20, color = 'var(--color-ink-2)' }: { values: (number | null)[]; width?: number; height?: number; color?: string }) {
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] != null && isFinite(p[1]))
  if (pts.length < 2) return <svg width={width} height={height} />
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  const x0 = Math.min(...xs)
  const x1 = Math.max(...xs)
  const y0 = Math.min(...ys)
  const y1 = Math.max(...ys)
  const sx = (x: number) => ((x - x0) / Math.max(1, x1 - x0)) * (width - 2) + 1
  const sy = (y: number) => height - 2 - ((y - y0) / Math.max(1e-9, y1 - y0 || 1)) * (height - 4)
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join('')
  const area = `${d}L${sx(pts[pts.length - 1][0]).toFixed(1)},${height}L${sx(pts[0][0]).toFixed(1)},${height}Z`
  return (
    <svg width={width} height={height} className="block overflow-visible">
      <path d={area} fill={color} opacity={0.06} />
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={sx(pts[pts.length - 1][0])} cy={sy(pts[pts.length - 1][1])} r={2} fill={color} />
    </svg>
  )
}
