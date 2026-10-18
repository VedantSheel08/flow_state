import { useEffect, useState } from 'react'
import { getJSON } from './lib/api'
import type { Geometry } from './lib/types'
import Compare from './views/Compare'
import Inspector, { type Meta } from './views/Inspector'
import Results from './views/Results'

const TABS = [['results', 'Results'], ['compare', 'Compare'], ['inspect', 'What the AI sees']] as const
type Tab = (typeof TABS)[number][0]

export default function App() {
  const [tab, setTab] = useState<Tab>('compare')
  const [geom, setGeom] = useState<Geometry | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    Promise.all([getJSON<Geometry>('/api/lab/geometry'), getJSON<Meta>('/api/studio/meta')]).then(([g, m]) => { setGeom(g); setMeta(m) }).catch((e) => setErr(String(e)))
  }, [])
  if (err) return <div className="boot error">Cannot reach the Studio backend on :8017 — {err}</div>
  if (!geom || !meta) return <div className="boot">Connecting to FlowState Studio…</div>
  return (
    <div className="app">
      <header>
        <div className="brand"><b>FlowState</b> Studio <small>real SUMO runs · ambulances get no simulator priority · every number measured</small></div>
        <nav>{TABS.map(([k, l]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>)}</nav>
      </header>
      <main>
        <div style={{ display: tab === 'results' ? 'block' : 'none' }}><Results /></div>
        <div style={{ display: tab === 'compare' ? 'block' : 'none' }}><Compare geom={geom} /></div>
        <div style={{ display: tab === 'inspect' ? 'block' : 'none' }}><Inspector geom={geom} meta={meta} /></div>
      </main>
    </div>
  )
}
