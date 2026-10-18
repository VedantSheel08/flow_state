export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url}: ${r.status} ${(await r.text()).slice(0, 160)}`)
  return r.json() as Promise<T>
}
export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`${url}: ${r.status} ${(await r.text()).slice(0, 160)}`)
  return r.json() as Promise<T>
}
export const wsURL = (path: string) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`
export const fmt = (v: number | null | undefined, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(d))
export const signed = (v: number, d = 1) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`
