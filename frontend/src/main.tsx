import { Profiler, StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { frames } from './lib/frames'
import { perf } from './lib/perf'
import { useStore } from './lib/store'
import { mapApi } from './map/MapView'

// dev builds, and production builds made with VITE_FS_DEBUG=1 for profiling
if (import.meta.env.DEV || import.meta.env.VITE_FS_DEBUG) Object.assign(window as object, { __fs: { store: useStore, frames, map: mapApi, perf } })
// read-only profiling hook, also in production builds (no state access)
Object.assign(window as object, { __flowstatePerf: perf })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Profiler id="app" onRender={(_id, _phase, ms) => perf.reactCommit(ms)}>
      <App />
    </Profiler>
  </StrictMode>,
)
