// WebSocket client: one connection, automatic reconnect with backoff.
import { wsUrl } from './endpoints'
import { frames } from './frames'
import { perf } from './perf'
import { useStore } from './store'
import type { CompareJob, FrameMessage, RunInfo, RunResults, RunStatus, SimEvent, StatsMessage } from './types'

type Msg =
  | ({ type: 'hello'; run: RunInfo | null; status: RunStatus; events?: SimEvent[]; results?: RunResults | null })
  | FrameMessage
  | StatsMessage
  | ({ type: 'status' } & RunStatus)
  | { type: 'event'; run: string; event: SimEvent }
  | { type: 'run'; run: RunInfo }
  | { type: 'results'; run: string; results: RunResults }
  | { type: 'job'; job: Partial<CompareJob> & { id: string } }

let socket: WebSocket | null = null
let retry = 0
let timer: ReturnType<typeof setTimeout> | null = null
let ping: ReturnType<typeof setInterval> | null = null

function handle(msg: Msg) {
  const st = useStore.getState()
  switch (msg.type) {
    case 'frame':
      frames.push(msg)
      break
    case 'stats':
      st.onStats(msg)
      break
    case 'event':
      if (!st.run || msg.run === st.run.id) st.onEvent(msg.event)
      break
    case 'status': {
      const { type: _t, ...status } = msg
      void _t
      st.set({ status: status as RunStatus })
      frames.paused = !!status.paused || status.state !== 'running'
      if (status.speed) frames.speed = status.speed
      if (status.state === 'idle') st.set({ run: null })
      break
    }
    case 'run':
      frames.reset(msg.run.id)
      st.onRun(msg.run)
      break
    case 'results':
      if (st.run && msg.run === st.run.id) st.set({ results: msg.results })
      break
    case 'job': {
      const cur = st.compare
      if (cur && cur.id === msg.job.id) st.set({ compare: { ...cur, ...msg.job } as CompareJob })
      break
    }
    case 'hello':
      if (msg.run) {
        if (frames.run !== msg.run.id) frames.reset(msg.run.id)
        st.set({ run: msg.run, status: msg.status, events: msg.events ?? [], results: msg.results ?? null })
        frames.paused = !!msg.status.paused || msg.status.state !== 'running'
        if (msg.status.speed) frames.speed = msg.status.speed
      } else {
        st.set({ run: null, status: msg.status })
      }
      break
  }
}

export function connect() {
  if (socket && socket.readyState <= 1) return
  useStore.getState().set({ connection: 'connecting' })
  const ws = new WebSocket(wsUrl())
  socket = ws
  ws.onopen = () => {
    retry = 0
    useStore.getState().set({ connection: 'open' })
    ping = setInterval(() => ws.readyState === 1 && ws.send('ping'), 15000)
  }
  ws.onmessage = (ev) => {
    try {
      const t0 = performance.now()
      const raw = ev.data as string
      const msg = JSON.parse(raw) as Msg
      handle(msg)
      perf.wsMessage(msg.type, raw.length, performance.now() - t0)
    } catch (e) {
      console.error('bad message', e)
    }
  }
  ws.onclose = () => {
    if (ping) clearInterval(ping)
    useStore.getState().set({ connection: 'closed' })
    socket = null
    const delay = Math.min(8000, 500 * 2 ** retry++)
    if (timer) clearTimeout(timer)
    timer = setTimeout(connect, delay)
  }
  ws.onerror = () => ws.close()
}
