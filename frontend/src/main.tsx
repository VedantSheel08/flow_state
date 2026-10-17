import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { frames } from './lib/frames'
import { useStore } from './lib/store'
import { mapApi } from './map/MapView'

if (import.meta.env.DEV) Object.assign(window as object, { __fs: { store: useStore, frames, map: mapApi } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
