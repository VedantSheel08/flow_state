// Where the FlowState API lives.
//
// By default the API is same-origin: in development the Vite server proxies
// /api and /ws to the backend, and any deployment that serves both from one
// host needs nothing else. When the frontend is deployed on its own (e.g. a
// static Vercel deployment) set VITE_FLOWSTATE_API_URL at build time to the
// backend's public origin, such as https://flowstate-api.example.com. The
// backend must then be served over HTTPS so the browser allows wss:// and the
// API calls from an https:// page; it already allows cross-origin requests.

export interface Endpoints {
  /** Absolute or root-relative URL for an API path such as "/api/health". */
  api: (path: string) => string
  /** WebSocket URL of the live stream. */
  ws: () => string
}

export function resolveEndpoints(base: string | undefined, loc: { protocol: string; host: string }): Endpoints {
  const origin = (base ?? '').trim().replace(/\/+$/, '')
  if (!origin) {
    return {
      api: (path) => path,
      ws: () => `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/ws`,
    }
  }
  const url = new URL(origin)
  const wsProto = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return {
    api: (path) => `${origin}${path}`,
    ws: () => `${wsProto}//${url.host}${url.pathname.replace(/\/+$/, '')}/ws`,
  }
}

const endpoints = resolveEndpoints(import.meta.env.VITE_FLOWSTATE_API_URL, typeof location !== 'undefined' ? location : { protocol: 'http:', host: 'localhost' })

export const apiUrl = endpoints.api
export const wsUrl = endpoints.ws
