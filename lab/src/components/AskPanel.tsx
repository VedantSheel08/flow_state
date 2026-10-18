import { useState } from 'react'
import { postJSON } from '../lib/api'

interface Answer { answer: string; source: string; note: string | null; rejectedNumbers: string[]; scenarioRequest: Record<string, unknown> | null }
interface Props { replay: string | null; live: boolean; gemini?: { configured?: boolean; restClientInstalled?: boolean }; onProposal: (p: Record<string, unknown>) => void }

const SUGGEST = ['Why is congestion happening right now?', 'Did machine learning improve traffic?', 'Explain this comparison', 'Suggest a heavy rush-hour scenario to test']

export default function AskPanel({ replay, live, gemini, onProposal }: Props) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [hist, setHist] = useState<{ q: string; a?: Answer; err?: string }[]>([])
  const ask = async (text: string) => {
    if (!text.trim() || busy) return
    setBusy(true); setQ('')
    try {
      const a = await postJSON<Answer>('/api/lab/ask', { question: text, replay, live })
      setHist((h) => [{ q: text, a }, ...h].slice(0, 6))
    } catch (e) { setHist((h) => [{ q: text, err: String(e) }, ...h]) }
    setBusy(false)
  }
  return (
    <div className="ask">
      <h4>Ask FlowState <small>{gemini?.configured ? 'Gemini (grounded)' : 'offline: answers from measurements'}</small></h4>
      <p className="small muted">Gemini only explains measured results and proposes scenarios. It is never in the signal-control loop.</p>
      <div className="chips">{SUGGEST.map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}</div>
      <form onSubmit={(e) => { e.preventDefault(); ask(q) }}>
        <input value={q} maxLength={1000} placeholder="Ask about congestion, a scenario, or the comparison…" onChange={(e) => setQ(e.target.value)} />
        <button className="primary" disabled={busy}>{busy ? '…' : 'Ask'}</button>
      </form>
      {hist.map((h, i) => (
        <div key={i} className="qa">
          <div className="q">{h.q}</div>
          {h.err && <div className="error">{h.err}</div>}
          {h.a && <>
            <div className="a">{h.a.answer}</div>
            <div className="small muted"><span className={`tag ${h.a.source === 'gemini' ? 'ok' : ''}`}>{h.a.source === 'gemini' ? 'Gemini, numbers verified' : 'deterministic, from measurements'}</span> {h.a.note}</div>
            {h.a.scenarioRequest && <button className="chip" onClick={() => onProposal(h.a!.scenarioRequest!)}>Load proposal into “Record a new paired run” →</button>}
          </>}
        </div>
      ))}
    </div>
  )
}
