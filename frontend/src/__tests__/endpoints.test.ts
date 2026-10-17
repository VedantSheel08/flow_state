import { describe, expect, it } from 'vitest'
import { resolveEndpoints } from '../lib/endpoints'

describe('API endpoints', () => {
  it('stays same-origin when no API URL is configured (dev proxy, single-host deploys)', () => {
    const e = resolveEndpoints(undefined, { protocol: 'https:', host: 'flowstate.vercel.app' })
    expect(e.api('/api/health')).toBe('/api/health')
    expect(e.ws()).toBe('wss://flowstate.vercel.app/ws')
    expect(resolveEndpoints('', { protocol: 'http:', host: 'localhost:5173' }).ws()).toBe('ws://localhost:5173/ws')
  })

  it('targets a separately hosted backend over https/wss', () => {
    const e = resolveEndpoints('https://api.flowstate.example/', { protocol: 'https:', host: 'flowstate.vercel.app' })
    expect(e.api('/api/runs')).toBe('https://api.flowstate.example/api/runs')
    expect(e.ws()).toBe('wss://api.flowstate.example/ws')
  })

  it('keeps a path prefix and plain http for a local backend', () => {
    const e = resolveEndpoints('http://127.0.0.1:8000/sim', { protocol: 'http:', host: 'localhost:5173' })
    expect(e.api('/api/health')).toBe('http://127.0.0.1:8000/sim/api/health')
    expect(e.ws()).toBe('ws://127.0.0.1:8000/sim/ws')
  })
})
