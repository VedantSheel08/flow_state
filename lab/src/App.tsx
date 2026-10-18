import { useCallback, useEffect, useState } from 'react'
import AskPanel from './components/AskPanel'
import { getJSON } from './lib/api'
import type { Geometry } from './lib/types'
import CompareView from './views/CompareView'
import ForecastView from './views/ForecastView'
import LiveView from './views/LiveView'
import PerfView from './views/PerfView'

interface Status {
  scenarios: { id: string; title: string; description: string }[]
  mlAvailable: boolean
  gemini: { configured?: boolean; restClientInstalled?: boolean }
}
const TABS = [['compare', 'ML vs non-ML comparison'], ['live', 'Live simulation'], ['forecast', 'Forecast model'], ['perf', 'Performance']] as const
type Tab = (typeof TABS)[number][0]

export default function App() {
  const [tab, setTab] = useState<Tab>('compare')
  const [geom, setGeom] = useState<Geometry | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [replay, setReplay] = useState<string | null>(null)
  const [liveOn, setLiveOn] = useState(false)
  const [proposal, setProposal] = useState<Record<string, unknown> | null>(null)
  useEffect(() => {
    Promise.all([getJSON<Geometry>('/api/lab/geometry'), getJSON<Status>('/api/lab/status')])
      .then(([g, s]) => { setGeom(g); setStatus(s) }).catch((e) => setErr(String(e)))
  }, [])
  const onReplay = useCallback((id: string | null) => setReplay(id), [])
  if (err) return <div className="boot error">Cannot reach the lab backend on :8015 — {err}</div>
  if (!geom || !status) return <div className="boot">Connecting to FlowState Intelligence Lab…</div>
  return (
    <div className="app">
      <header>
        <div className="brand"><b>FlowState</b> Intelligence Lab <small>SUMO · 2×2 signalised grid · every number measured</small></div>
        <nav>{TABS.map(([k, l]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>)}</nav>
      </header>
      <main>
        <section className="content">
          <div style={{ display: tab === 'compare' ? 'block' : 'none' }}>
            <CompareView geom={geom} scenarios={status.scenarios} mlAvailable={status.mlAvailable} onReplay={onReplay} pending={proposal} />
          </div>
          <div style={{ display: tab === 'live' ? 'block' : 'none' }}>
            <LiveView geom={geom} scenarios={status.scenarios} mlAvailable={status.mlAvailable} onSnapshot={setLiveOn} />
          </div>
          {tab === 'forecast' && <ForecastView />}
          {tab === 'perf' && <PerfView replay={replay} />}
        </section>
        <aside>
          <AskPanel replay={tab === 'compare' ? replay : null} live={tab === 'live' && liveOn} gemini={status.gemini}
            onProposal={(p) => { setProposal(p); setTab('compare') }} />
        </aside>
      </main>
    </div>
  )
}
