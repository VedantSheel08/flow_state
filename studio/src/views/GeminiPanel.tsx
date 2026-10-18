import { useState } from 'react'
import type { Meta } from './Inspector'
import { postJSON } from '../lib/api'

interface Reply {
  answer: string; source: string; note: string | null; rejectedNumbers: string[]; scenarioRequest: Record<string, unknown> | null
  prompt: { system: string; user: string; chars: number; approxTokens: number }; sentToGemini: boolean; geminiDraft: string | null
  grounding: { numbersInDraft: number; ungrounded: string[] } | null; facts: Record<string, unknown>
}
const SUGGEST = ['Why is congestion happening right now?', 'Did machine learning improve traffic?', 'How accurate is the forecast and where is it weak?', 'Explain what happens to ambulances']

export default function GeminiPanel({ meta, live }: { meta: Meta; live: boolean }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [r, setR] = useState<Reply | null>(null)
  const [asked, setAsked] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const ask = async (text: string) => {
    if (!text.trim() || busy) return
    setBusy(true); setAsked(text); setQ(''); setErr(null)
    try { setR(await postJSON<Reply>('/api/studio/gemini', { question: text, live })) } catch (e) { setErr(String(e)) }
    setBusy(false)
  }
  const g = meta.gemini.status as { configured?: boolean; restClientInstalled?: boolean; available?: boolean; limits?: { requestsPerMinute?: number; timeoutSeconds?: number } }
  const highlight = (text: string, bad: string[]) => text.split(/(\s+)/).map((w, i) => (bad.some((b) => w.includes(b)) ? <mark key={i}>{w}</mark> : w))
  return (
    <div className="card">
      <h4>Gemini <span className="badge ok">outside the control loop</span> <small>explains measured results; never sees raw simulation state or any control API</small></h4>
      <div className="small muted">Key configured on the server: <b>{g.configured ? 'yes' : 'no'}</b> · REST client present: <b>{g.restClientInstalled ? 'yes' : 'no (task/integrations)'}</b> · limit {g.limits?.requestsPerMinute ?? '—'} requests/min, {g.limits?.timeoutSeconds ?? '—'} s timeout.
        Without a key, or on any error, rate limit or timeout, the answer is built from the same measured facts and labelled as such.</div>
      <div className="chips">{SUGGEST.map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}</div>
      <form onSubmit={(e) => { e.preventDefault(); ask(q) }}><input value={q} maxLength={1000} placeholder="Ask about congestion, the forecast, the comparison or ambulances…" onChange={(e) => setQ(e.target.value)} /><button className="primary" disabled={busy}>{busy ? '…' : 'Ask'}</button></form>
      {err && <div className="error">{err}</div>}
      {r && (
        <div className="gem">
          <div className="q">{asked}</div>
          <div className="gem-grid">
            <div>
              <h5>1 · What Gemini is sent <small>{r.prompt.chars.toLocaleString()} characters ≈ {r.prompt.approxTokens.toLocaleString()} tokens</small></h5>
              <details><summary>System instructions</summary><pre>{r.prompt.system}</pre></details>
              <details open><summary>Facts (measured numbers only)</summary><pre>{JSON.stringify(JSON.parse(r.prompt.user).FACTS, null, 1).slice(0, 6000)}</pre></details>
              <div className="small muted">{r.sentToGemini ? 'This was sent to Gemini.' : 'Not sent: Gemini is unavailable, so nothing left the server.'}</div>
            </div>
            <div>
              <h5>2 · What came back</h5>
              {r.geminiDraft !== null ? <div className={`draft ${r.grounding?.ungrounded.length ? 'rej' : ''}`}><div className="small muted">Gemini draft</div>{highlight(r.geminiDraft, r.grounding?.ungrounded ?? [])}</div> : <div className="small muted">No Gemini draft.</div>}
              <h5>3 · Number check</h5>
              {r.grounding ? (r.grounding.ungrounded.length ? <div className="gate fail">Rejected: {r.grounding.ungrounded.join(', ')} ({r.grounding.ungrounded.length} of {r.grounding.numbersInDraft} numbers) do not appear in the measured facts.</div>
                : <div className="gate ok">All {r.grounding.numbersInDraft} numbers in the draft appear in the measured facts.</div>) : <div className="small muted">Nothing to check.</div>}
              <h5>4 · Answer shown to the user <span className={`tag ${r.source === 'gemini' ? 'ok' : ''}`}>{r.source === 'gemini' ? 'Gemini, numbers verified' : 'built from measurements'}</span></h5>
              <div className="answer">{r.answer}</div>
              {r.note && <div className="small muted">{r.note}</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
