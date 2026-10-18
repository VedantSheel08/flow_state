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
  const [assistant, setAssistant] = useState(false)
  const [technical, setTechnical] = useState(false)
  const [diagnostic, setDiagnostic] = useState<'forecast' | 'perf'>('forecast')
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
  if (err)
    return (
      <div className="boot">
        <span className="eyebrow">FlowState / Intelligence Lab</span>
        <h1>The lab is offline.</h1>
        <p>Start the local lab API on port 8015, then reconnect.</p>
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
            aria-expanded={assistant}
            aria-controls="assistant"
            onClick={() => {
              setAssistant(!assistant)
              if (!assistant)
                requestAnimationFrame(() =>
                  document
                    .getElementById('assistant')
                    ?.scrollIntoView({
                      behavior: matchMedia('(prefers-reduced-motion: reduce)')
                        .matches
                        ? 'instant'
                        : 'smooth',
                      block: 'start',
                    }),
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
            <h1>Can prediction keep traffic moving?</h1>
            <p>
              The same traffic. Two signal controllers. See what machine
              learning changes, and what the measurements say.
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
          />
        </section>
        <section hidden={tab !== 'live'} aria-label="Live simulation">
          <LiveView
            geom={geom}
            scenarios={status.scenarios}
            mlAvailable={status.mlAvailable}
            onSnapshot={setLiveOn}
          />
        </section>
        <section
          id="assistant"
          className="assistant-section"
          hidden={!assistant}
        >
          <div className="section-heading">
            <div>
              <span className="eyebrow">Interpret the evidence</span>
              <h2>Ask FlowState</h2>
            </div>
            <button className="quiet" onClick={() => setAssistant(false)}>
              Close
            </button>
          </div>
          <AskPanel
            replay={tab === 'compare' ? replay : null}
            live={tab === 'live' && liveOn}
            gemini={status.gemini}
            onProposal={(p) => {
              setProposal(p)
              setTab('compare')
            }}
          />
        </section>
        <details
          className="disclosure diagnostics"
          open={technical}
          onToggle={(e) => setTechnical(e.currentTarget.open)}
        >
          <summary>
            Model & runtime details{' '}
            <span>Forecast accuracy, feature importance, worker timing</span>
          </summary>
          {technical && (
            <div className="disclosure-body">
              <div className="segmented" aria-label="Diagnostics">
                <button
                  className={diagnostic === 'forecast' ? 'on' : ''}
                  onClick={() => setDiagnostic('forecast')}
                >
                  Forecast model
                </button>
                <button
                  className={diagnostic === 'perf' ? 'on' : ''}
                  onClick={() => setDiagnostic('perf')}
                >
                  Runtime performance
                </button>
              </div>
              {diagnostic === 'forecast' ? (
                <ForecastView />
              ) : (
                <PerfView replay={replay} />
              )}
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
