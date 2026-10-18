import { useCallback, useEffect, useState } from 'react'
import AskPanel from './components/AskPanel'
import { getJSON } from './lib/api'
import type { Geometry } from './lib/types'
import CompareView from './views/CompareView'
import ForecastView from './views/ForecastView'
import LiveView from './views/LiveView'
import PerfView from './views/PerfView'
import flowstateLogo from './assets/flowstate-logo.png'

interface Status {
  scenarios: { id: string; title: string; description: string }[]
  mlAvailable: boolean
  gemini: { configured?: boolean; restClientInstalled?: boolean }
}
export default function App() {
  const [tab, setTab] = useState<'compare' | 'live'>('compare')
  const [geom, setGeom] = useState<Geometry | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [replay, setReplay] = useState<string | null>(null)
  const [liveOn, setLiveOn] = useState(false)
  const [proposal, setProposal] = useState<Record<string, unknown> | null>(null)
  const [advanced, setAdvanced] = useState(false)
  const [advancedTab, setAdvancedTab] = useState<
    'run' | 'forecast' | 'perf' | 'assistant'
  >('run')
  const [advancedTarget, setAdvancedTarget] = useState<HTMLDivElement | null>(
    null,
  )
  useEffect(() => {
    Promise.all([
      getJSON<Geometry>('/api/lab/geometry'),
      getJSON<Status>('/api/lab/status'),
    ])
      .then(([g, s]) => {
        setGeom(g)
        setStatus(s)
      })
      .catch((e) => setErr(String(e)))
  }, [])
  const onReplay = useCallback((id: string | null) => setReplay(id), [])
  const inspectApproach = useCallback(() => {
    setAdvanced(true)
    setAdvancedTab('run')
    requestAnimationFrame(() =>
      document.getElementById('advanced')?.scrollIntoView({ block: 'start' }),
    )
  }, [])
  if (err)
    return (
      <div className="boot">
        <span className="eyebrow">FlowState / Intelligence Lab</span>
        <h1>The lab is offline.</h1>
        <p>Start the configured local lab API, then reconnect.</p>
        <p className="small muted">{err}</p>
        <button onClick={() => location.reload()}>Reconnect</button>
      </div>
    )
  if (!geom || !status)
    return (
      <div className="boot" role="status">
        <span className="eyebrow">FlowState / Intelligence Lab</span>
        <h1>Connecting to the lab…</h1>
      </div>
    )
  return (
    <div className="app">
      <header className="site-header">
        <a
          className="brand"
          href="#main"
          aria-label="FlowState Intelligence Lab"
        >
          <span className="brand-logo">
            <img
              src={flowstateLogo}
              alt="FlowState"
              width="2172"
              height="724"
            />
          </span>
          <span className="brand-divider" />
          <span className="lab-name">Intelligence Lab</span>
        </a>
        <div className="header-end">
          <span className="connection">
            <i />
            Local SUMO
          </span>
          <button
            className="quiet"
            aria-expanded={advanced && advancedTab === 'assistant'}
            aria-controls="advanced"
            onClick={() => {
              setAdvanced(true)
              setAdvancedTab('assistant')
              requestAnimationFrame(() =>
                document
                  .getElementById('advanced')
                  ?.scrollIntoView({ block: 'start' }),
              )
            }}
          >
            Ask FlowState <span aria-hidden="true">↗</span>
          </button>
        </div>
      </header>
      <main id="main">
        <section className="intro">
          <div>
            <span className="eyebrow">
              Traffic intelligence · Experimental lab
            </span>
            <h1>Does forecasting improve traffic?</h1>
            <p>
              Compare the same traffic with and without ML. Predict queues,
              adjust signals, and measure whether the change helps.
            </p>
          </div>
          <div className="lab-caption">
            <b>{geom.controlled.length} intersections</b>
            <span>One controlled SUMO network</span>
            <span>Measured traffic · Forecast-assisted signals</span>
          </div>
        </section>
        <nav className="workspace-nav" aria-label="Lab views">
          <button
            className={tab === 'compare' ? 'on' : ''}
            aria-pressed={tab === 'compare'}
            onClick={() => setTab('compare')}
          >
            Compare controllers
          </button>
          <button
            className={tab === 'live' ? 'on' : ''}
            aria-pressed={tab === 'live'}
            onClick={() => setTab('live')}
          >
            Live simulation
          </button>
          <span className="nav-note">
            {tab === 'compare'
              ? 'Paired recordings · Synchronized playback'
              : 'Live SUMO worker · Measured frames'}
          </span>
        </nav>
        <section hidden={tab !== 'compare'} aria-label="Controller comparison">
          <CompareView
            geom={geom}
            scenarios={status.scenarios}
            mlAvailable={status.mlAvailable}
            onReplay={onReplay}
            pending={proposal}
            active={tab === 'compare'}
            advancedTarget={
              tab === 'compare' && advancedTab === 'run' ? advancedTarget : null
            }
            onInspect={inspectApproach}
          />
        </section>
        <section hidden={tab !== 'live'} aria-label="Live simulation">
          <LiveView
            geom={geom}
            scenarios={status.scenarios}
            mlAvailable={status.mlAvailable}
            onSnapshot={setLiveOn}
            advancedTarget={
              tab === 'live' && advancedTab === 'run' ? advancedTarget : null
            }
            onInspect={inspectApproach}
          />
        </section>
        <details
          id="advanced"
          className="disclosure diagnostics"
          open={advanced}
          onToggle={(e) => setAdvanced(e.currentTarget.open)}
        >
          <summary>
            Advanced{' '}
            <span>Run details, intersections, model, runtime & Gemini</span>
          </summary>
          {advanced && (
            <div className="disclosure-body">
              <div className="segmented" aria-label="Diagnostics">
                <button
                  className={advancedTab === 'run' ? 'on' : ''}
                  aria-pressed={advancedTab === 'run'}
                  onClick={() => setAdvancedTab('run')}
                >
                  Run & intersections
                </button>
                <button
                  className={advancedTab === 'forecast' ? 'on' : ''}
                  aria-pressed={advancedTab === 'forecast'}
                  onClick={() => setAdvancedTab('forecast')}
                >
                  Forecast model
                </button>
                <button
                  className={advancedTab === 'perf' ? 'on' : ''}
                  aria-pressed={advancedTab === 'perf'}
                  onClick={() => setAdvancedTab('perf')}
                >
                  Runtime performance
                </button>
                <button
                  className={advancedTab === 'assistant' ? 'on' : ''}
                  aria-pressed={advancedTab === 'assistant'}
                  onClick={() => setAdvancedTab('assistant')}
                >
                  Ask FlowState
                </button>
              </div>
              <div ref={setAdvancedTarget} hidden={advancedTab !== 'run'} />
              {advancedTab === 'forecast' && <ForecastView />}
              {advancedTab === 'perf' && <PerfView replay={replay} />}
              <div hidden={advancedTab !== 'assistant'}>
                <AskPanel
                  replay={tab === 'compare' ? replay : null}
                  live={tab === 'live' && liveOn}
                  gemini={status.gemini}
                  onProposal={(p) => {
                    setProposal(p)
                    setTab('compare')
                    setAdvanced(false)
                    requestAnimationFrame(() =>
                      document
                        .getElementById('record-pair')
                        ?.scrollIntoView({ block: 'start' }),
                    )
                  }}
                />
              </div>
            </div>
          )}
        </details>
        <footer>
          <span>FlowState Intelligence Lab</span>
          <span>Local research demonstration · SUMO 2 × 2 grid</span>
          <span>Prediction is a hypothesis. Measurement is the test.</span>
        </footer>
      </main>
    </div>
  )
}
