export function clock(t: number | null | undefined): string {
  if (t == null || !isFinite(t)) return '--:--'
  const s = Math.max(0, Math.floor(t))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

export function num(v: number | null | undefined, digits = 0, fallback = '—'): string {
  if (v == null || !isFinite(v)) return fallback
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function secs(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—'
  if (v >= 120) return `${Math.floor(v / 60)}m ${String(Math.round(v % 60)).padStart(2, '0')}s`
  return `${v.toFixed(v < 10 ? 1 : 0)} s`
}

export const WEATHER_LABEL: Record<string, string> = { light_rain: 'Light rain', heavy_rain: 'Heavy rain', snow: 'Snow', fog: 'Dense fog', clear: 'Clear' }

export const HEADING: Record<string, string> = { N: 'Northbound', S: 'Southbound', E: 'Eastbound', W: 'Westbound' }

export function cn(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(' ')
}
