import { ArrowRight, Play } from 'lucide-react'
import HalftoneWordmarkFooter, { type FooterColumn } from '../components/ui/halftone-wordmark-footer'
import { METRICS, results, verdict, type MetricKey } from '../lib/controllerResults'
import { useStore, type View } from '../lib/store'
import { MessageSign } from '../components/ui/traffic'

const pct = (p: number) => `${p > 0 ? '+' : p < 0 ? '−' : ''}${Math.abs(p).toFixed(1)}%`

/** The honest one-liner, read from the recorded evaluation rather than typed in. */
function headline() {
  const d = results.demand.standard
  const o = d.overall[results.flowstate]?.metrics ?? {}
  const keys: MetricKey[] = ['queue_max_veh', 'mean_travel_s', 'mean_wait_s', 'throughput_vph']
  const wins = keys.filter((k) => verdict(METRICS[k], o[k]?.vs) === 'better').map((k) => `${METRICS[k].label.toLowerCase()} ${pct(o[k]?.vs?.pct ?? 0)}`)
  const losses = results.scenarios.filter((s) => verdict(METRICS.mean_wait_s, d.perScenario[s.id]?.[results.flowstate]?.metrics.mean_wait_s?.vs) === 'worse').map((s) => s.title.toLowerCase())
  return { runs: o.mean_wait_s?.vs?.paired ?? 0, wins, losses }
}

export function HomeView() {
  const set = useStore((s) => s.set)
  const conn = useStore((s) => s.connection)
  const health = useStore((s) => s.health)
  const go = (view: View) => () => set({ view })
  const h = headline()
  const columns: FooterColumn[] = [
    { title: 'Simulate', links: [{ label: 'Open the city twin', onSelect: go('simulate') }, { label: '11 scenarios', onSelect: go('simulate') }] },
    { title: 'Evidence', links: [{ label: 'Results', onSelect: go('results') }, { label: 'Analytics', onSelect: go('analyze') }] },
    { title: 'Archive', links: [{ label: 'Run history', onSelect: go('runs') }] },
  ]
  return (
    <div className="view-in min-w-0 flex-1 overflow-hidden">
      <HalftoneWordmarkFooter as="section" preset="asphalt" wordmark="flowstate" crop={0.16} dotGap={6} radius={90} grain={0.5} height="calc(100dvh - 48px)" className="asphalt">
        <div className="hwf-top">
          <div className="hwf-brand">
            <strong>FlowState</strong>
            <span>Adaptive signals · Eclipse SUMO</span>
          </div>
          {columns.map((col) => (
            <nav className="hwf-col" key={col.title} aria-label={col.title}>
              <h3>{col.title}</h3>
              <ul>
                {col.links.map((l) => (
                  <li key={l.label}>
                    <button type="button" className="hwf-link" onClick={l.onSelect}>
                      {l.label}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="relative z-[1] flex max-w-[820px] flex-col gap-6 px-[clamp(20px,3.4vw,44px)] pt-[clamp(40px,9vh,112px)]">
          <h1 className="m-0 text-balance text-[clamp(40px,5.4vw,72px)] font-[650] leading-[1.02] tracking-[-0.035em] text-ink">Traffic signals that read the queue.</h1>
          <p className="m-0 max-w-[620px] text-pretty text-[16px] leading-[1.6] text-ink-2">
            FlowState switches each light by the traffic actually waiting, and we test it against ordinary fixed-time signals on identical vehicles in a SUMO twin of the city.
          </p>
          <div className="flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={go('simulate')}
              className="inline-flex h-10 items-center gap-2 rounded-[10px] bg-ink pl-3.5 pr-4 text-[14px] font-semibold text-on-ink transition-[background-color,scale] duration-150 ease-out hover:bg-ink/85 active:scale-[0.96] motion-reduce:active:scale-100"
            >
              <Play size={14} fill="currentColor" /> Open simulation
            </button>
            <button
              type="button"
              onClick={go('results')}
              className="inline-flex h-10 items-center gap-2 rounded-[10px] px-4 text-[14px] font-semibold text-ink shadow-[inset_0_0_0_1px_var(--color-border-strong)] transition-[background-color,scale] duration-150 ease-out hover:bg-white/[0.06] active:scale-[0.96] motion-reduce:active:scale-100"
            >
              See the results <ArrowRight size={15} />
            </button>
          </div>
        </div>

        <div className="relative z-[1] mx-[clamp(20px,3.4vw,44px)] mt-10 max-w-[760px]">
          <MessageSign
            messages={[
              `${h.runs} paired SUMO runs vs fixed-time`,
              ...h.wins,
              ...h.losses.map((l) => `clearly worse: ${l} (held out)`),
              'same seed = identical vehicles',
            ]}
            lead={<span className="hwf-kicker shrink-0 !text-[9.5px] !text-white/45">Results</span>}
          />
        </div>
        <div className="hwf-rule !justify-end">
          <span className="flex items-center gap-2">
            <span className={conn === 'open' ? 'h-1.5 w-1.5 rounded-full bg-good' : 'h-1.5 w-1.5 rounded-full bg-warn'} />
            {conn === 'open' ? `SUMO ${health?.sumo ?? ''} connected` : 'Connecting to SUMO'}
          </span>
        </div>
      </HalftoneWordmarkFooter>
    </div>
  )
}
