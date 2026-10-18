import { useMemo, useState } from 'react'
import { METRICS, pairs, results, scale, type ControllerId, type DemandResults, type MetricDef, type MetricKey, type MetricStats } from '../lib/controllerResults'
import { cn, num } from '../lib/format'
import { BASE, COLOR, FS, HERO, card, signed } from '../lib/resultsTheme'
import { Cap, Eyebrow, H2, Swatch } from './resultsUi'
import { FlowBackdrop } from './FlowBackdrop'
import { SideBySide } from './SideBySide'

// Recorded SUMO evaluation, standard peak demand; over-saturated only confirms the headline.
const D = results.demand.standard
const STRESS = results.demand.stress

const HEADLINE: { k: MetricKey; what: string }[] = [
  { k: 'mean_wait_s', what: 'less waiting per vehicle' },
  { k: 'mean_travel_s', what: 'less travel time per vehicle' },
]

const stats = (d: DemandResults, k: MetricKey) => {
  const base = d.overall[BASE]?.metrics[k]
  const fs = d.overall[FS]?.metrics[k]
  return base && fs ? { base: base.mean, fs: fs.mean, vs: fs.vs, up: improved(METRICS[k], fs.vs?.pct) } : null
}

/** True when the change points the good way for this measure (lower wait, higher throughput). */
const improved = (m: MetricDef, pct: number | null | undefined) => pct != null && isFinite(pct) && (m.better === 'lower' ? pct < 0 : pct > 0)

const secs = (k: MetricKey, v: number) => `${num(scale(METRICS[k], v), METRICS[k].digits)} s`

// ---- headline --------------------------------------------------------------
function Headline() {
  const stress = HEADLINE.map(({ k }) => ({ k, s: stats(STRESS, k) }))
  return (
    <section className={cn(card, 'flex flex-col gap-8 p-8')}>
      <div className="grid gap-8 min-[720px]:grid-cols-2">
        {HEADLINE.map(({ k, what }) => {
          const s = stats(D, k)
          if (!s) return null
          return (
            <div key={k} className="flex flex-col gap-2">
              <div className="tnum flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-[56px] font-[650] leading-none tracking-[-0.04em]">{secs(k, Math.abs(s.base - s.fs))}</span>
                {s.vs?.pct != null && (
                  <span className={cn('rounded-full px-2 py-0.5 text-[13px] font-semibold', s.up ? 'bg-good-soft text-good' : 'bg-muted text-ink-2')}>{signed(s.vs.pct)}</span>
                )}
              </div>
              <div className="text-[17px] font-semibold">{s.up ? what : what.replace('less', 'more')}</div>
              <Cap className="tnum">
                {secs(k, s.base)} → {secs(k, s.fs)} · fixed-time → FlowState
              </Cap>
            </div>
          )
        })}
      </div>
      {stress.every((x) => x.s?.vs?.pct != null) && (
        <p className="tnum m-0 border-t border-muted pt-5 text-[13px] text-ink-2">
          {stress.every((x) => x.s?.up) ? 'Still holds when the network is over-saturated: ' : 'When the network is over-saturated: '}
          {stress.map((x, i) => (
            <span key={x.k}>
              {i > 0 && ' · '}
              <span className={cn(x.s!.up && 'font-semibold text-good')}>{signed(x.s!.vs!.pct!)}</span> {x.k === 'mean_wait_s' ? 'wait' : 'travel'}
            </span>
          ))}
        </p>
      )}
    </section>
  )
}

// ---- headline measures -----------------------------------------------------
/** Change text, green whenever FlowState moved the measure the right way. */
function Delta({ m, s, className }: { m: MetricDef; s?: MetricStats; className?: string }) {
  const pct = s?.vs?.pct
  if (pct == null || !isFinite(pct)) return <span className={cn('text-ink-3', className)}>—</span>
  return <span className={cn('tnum', improved(m, pct) ? 'font-[550] text-good' : 'text-ink-3', className)}>{signed(pct)}</span>
}

function Bars({ m, base, fs }: { m: MetricDef; base: number; fs: number }) {
  const max = Math.max(base, fs) * 1.087 || 1
  const rows: [ControllerId, string, number][] = [
    [BASE, 'Fixed', base],
    [FS, 'FlowState', fs],
  ]
  return (
    <div className="flex flex-col gap-1.5 text-[12px]">
      {rows.map(([c, label, v]) => (
        <div key={c} className="flex items-center gap-2">
          <span className={cn('flex w-[78px] shrink-0 items-center gap-2', c === FS ? 'font-medium text-ink-2' : 'text-ink-3')}>
            <Swatch c={c} />
            {label}
          </span>
          <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
            <div className="h-2 rounded-r-[4px]" style={{ width: `${(v / max) * 100}%`, background: COLOR[c] }} />
          </div>
          <span className={cn('tnum w-12 shrink-0 text-right', c === FS ? 'font-semibold text-ink' : 'text-ink-2')}>{num(scale(m, v), m.digits)}</span>
        </div>
      ))}
    </div>
  )
}

function Tile({ k, d }: { k: MetricKey; d: DemandResults }) {
  const m = METRICS[k]
  const base = d.overall[BASE]?.metrics[k]
  const fs = d.overall[FS]?.metrics[k]
  if (!base || !fs) return null
  return (
    <article className={cn(card, 'flex flex-col gap-3.5 p-5')}>
      <span className="font-medium text-ink-2">{m.label}</span>
      <div className="flex items-baseline gap-2.5">
        <span className="tnum text-[34px] font-[650] leading-[1.12] tracking-[-0.03em]">
          {num(scale(m, fs.mean), m.digits)}
          <span className="text-[16px] font-medium tracking-normal text-ink-3"> {m.unit}</span>
        </span>
        <Delta m={m} s={fs} className="text-[14px]" />
      </div>
      <Bars m={m} base={base.mean} fs={fs.mean} />
      {fs.vs && (
        <div className="tnum border-t border-muted pt-3 text-[12px] text-ink-3">
          Better in <b className="font-semibold text-ink">{fs.vs.better}</b> of {fs.vs.paired} paired runs, worse in {fs.vs.worse}
        </div>
      )}
    </article>
  )
}

function HeadlineMeasures() {
  const levels: [string, DemandResults][] = [
    ['Standard peak', D],
    ['Over-saturated', STRESS],
  ]
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <H2>Headline measures</H2>
        <Cap>Lower is better, except throughput. Green marks an improvement over fixed-time.</Cap>
      </div>
      {levels.map(([label, d]) => (
        <div key={label} className="flex flex-col gap-2.5">
          <Eyebrow>{label}</Eyebrow>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(232px,1fr))] gap-4">
            {HERO.map((k) => (
              <Tile key={k} k={k} d={d} />
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

// ---- head to head ----------------------------------------------------------
function HeadToHead({ d }: { d: DemandResults }) {
  const titles = Object.fromEntries(results.scenarios.map((s) => [s.id, s.title]))
  const held = new Set(results.scenarios.filter((s) => s.heldOut).map((s) => s.id))
  const pts = useMemo(() => pairs(d, BASE, FS).map((p) => ({ x: p.a.wait, y: p.b.wait, s: p.s, seed: p.seed })), [d])
  const [hover, setHover] = useState<number | null>(null)
  if (!pts.length) return null
  const wins = pts.filter((p) => p.y < p.x).length
  const all = pts.flatMap((p) => [p.x, p.y])
  const lo = Math.max(0, Math.floor(Math.min(...all) / 10) * 10 - 10)
  const hi = Math.ceil(Math.max(...all) / 10) * 10 + 10
  const step = hi - lo > 300 ? 100 : 50
  const ticks: number[] = []
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t)
  // same range on both axes, so the diagonal stays y = x; the plot is just wider than it is tall
  const S = 440
  const W = 700
  const px = (v: number) => ((v - lo) / (hi - lo)) * W
  const py = (v: number) => ((v - lo) / (hi - lo)) * S
  const hp = hover != null ? pts[hover] : null
  return (
    <section className={cn(card, 'flex flex-col gap-4 p-6')}>
      <H2>Every run, head to head</H2>
      <div className="overflow-x-auto">
        <div className="relative" style={{ width: 60 + W + 16 + 200, height: 500 }}>
          <svg width={60 + W + 10} height={500} role="img" aria-label={`Paired mean waits: FlowState waited less in ${wins} of ${pts.length} runs`} onMouseLeave={() => setHover(null)}>
            <text x={14} y={4 + S / 2} transform={`rotate(-90 14 ${4 + S / 2})`} textAnchor="middle" fontSize={12} fontWeight={500} fill="#46505f">
              Mean wait (s)
            </text>
            <text x={60 + W / 2} y={488} textAnchor="middle" fontSize={12} fontWeight={500} fill="#46505f">
              Fixed-time wait (s)
            </text>
            <g transform="translate(60 4)">
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={0} x2={W} y1={S - py(t)} y2={S - py(t)} stroke="#f1f3f6" />
                  <line y1={0} y2={S} x1={px(t)} x2={px(t)} stroke="#f1f3f6" />
                  <text x={-8} y={S - py(t) + 4} textAnchor="end" fontSize={11} fill="#687284" className="tnum">
                    {t}
                  </text>
                  <text x={px(t)} y={S + 18} textAnchor="middle" fontSize={11} fill="#687284" className="tnum">
                    {t}
                  </text>
                </g>
              ))}
              <polygon points={`0,${S} ${W},${S} ${W},0`} fill="rgba(42,120,214,0.045)" />
              <line x1={0} y1={S} x2={W} y2={0} stroke="#8a93a3" strokeWidth={1.5} strokeDasharray="5 5" />
              <line x1={0} y1={0} x2={0} y2={S} stroke="#d3d8df" />
              <line x1={0} y1={S} x2={W} y2={S} stroke="#d3d8df" />
              <text x={W - 12} y={S - 14} textAnchor="end" fontSize={12} fontWeight={600} fill="#46505f">
                FlowState waited less
              </text>
              <text x={12} y={22} fontSize={12} fontWeight={600} fill="#46505f">
                Fixed-time waited less
              </text>
              {([BASE, FS] as const).map((c) =>
                pts.map((p, i) => {
                  const h = held.has(p.s)
                  const cy = S - py(c === BASE ? p.x : p.y)
                  return (
                    <g
                      key={`${c}/${p.s}/${p.seed}`}
                      onMouseEnter={() => setHover(i)}
                      className="transition-opacity duration-150"
                      style={{ cursor: 'default', opacity: hover != null && hover !== i ? 0.3 : 1 }}
                    >
                      <circle cx={px(p.x)} cy={cy} r={12} fill="transparent" />
                      <circle cx={px(p.x)} cy={cy} r={5.5} fill={h ? '#fff' : COLOR[c]} stroke={h ? COLOR[c] : '#fff'} strokeWidth={h ? 2.5 : 2} />
                    </g>
                  )
                }),
              )}
              {hp && (
                <g pointerEvents="none">
                  <line x1={px(hp.x)} x2={px(hp.x)} y1={S - py(hp.x)} y2={S - py(hp.y)} stroke="#0c1322" strokeWidth={1.5} strokeDasharray="3 3" />
                  {([BASE, FS] as const).map((c) => {
                    const cy = S - py(c === BASE ? hp.x : hp.y)
                    return (
                      <g key={c}>
                        <circle cx={px(hp.x)} cy={cy} r={7} fill={held.has(hp.s) ? '#fff' : COLOR[c]} stroke="#0c1322" strokeWidth={2} />
                        {held.has(hp.s) && <circle cx={px(hp.x)} cy={cy} r={3.5} fill={COLOR[c]} />}
                      </g>
                    )
                  })}
                </g>
              )}
            </g>
          </svg>
          {hp && (
            <div
              className="tnum pointer-events-none absolute z-10 w-[200px] rounded-[10px] bg-ink/70 px-3 py-2.5 text-[12px] leading-[1.5] text-white shadow-[0_4px_16px_rgba(12,19,34,0.12)] backdrop-blur-sm"
              style={{ left: 60 + W + 16, top: Math.min(Math.max(0, 4 + S - py(hp.y) - 40), 500 - 110) }}
            >
              <div className="font-semibold">
                {titles[hp.s]} · seed {hp.seed}
              </div>
              <div className="flex justify-between text-[#dfe3ea]">
                <span className="flex items-center gap-1.5">
                  <Swatch c={BASE} /> Fixed-time
                </span>
                <span>{hp.x.toFixed(1)} s</span>
              </div>
              <div className="flex justify-between text-[#dfe3ea]">
                <span className="flex items-center gap-1.5">
                  <Swatch c={FS} /> FlowState
                </span>
                <span>{hp.y.toFixed(1)} s</span>
              </div>
              <div className="mt-1 flex justify-between border-t border-white/15 pt-1">
                <span>FlowState</span>
                <span className="font-semibold">
                  {hp.y - hp.x > 0 ? '+' : '−'}
                  {Math.abs(hp.y - hp.x).toFixed(1)} s ({signed(((hp.y - hp.x) / hp.x) * 100, 0)})
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-[12px] text-ink-2">
        <span className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR[BASE] }} />
          Fixed-time (before FlowState)
        </span>
        <span className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR[FS] }} />
          FlowState (after)
        </span>
        {[...held].some((h) => d.perScenario[h]) && (
          <span className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full shadow-[inset_0_0_0_2px_#46505f]" />
            Hollow = {results.scenarios.find((s) => s.heldOut)?.title.toLowerCase()} (held out of tuning)
          </span>
        )}
      </div>
    </section>
  )
}

// ---- live comparison (placeholder) -----------------------------------------
function LivePlaceholder() {
  return (
    <section className="flex flex-col gap-1 rounded-[12px] border border-dashed border-border-strong px-6 py-5">
      <H2>Live comparison</H2>
      <Cap>Run your own SUMO comparison — coming soon.</Cap>
    </section>
  )
}

// ---- page ------------------------------------------------------------------
export function ResultsView() {
  const patterns = Object.keys(D.perScenario).length
  return (
    <div className="scroll-y min-w-0 flex-1 bg-canvas">
      <div className="pointer-events-none sticky top-0 z-0 h-0" aria-hidden>
        <div className="absolute inset-x-0 top-0 h-[calc(100vh-48px)] overflow-hidden">
          <FlowBackdrop />
        </div>
      </div>
      <main className="relative z-10 mx-auto flex max-w-[1100px] flex-col gap-8 px-4 pb-[72px] pt-10 min-[720px]:px-8">
        <h1 className="m-0 text-balance pt-2 text-center text-[40px] font-[650] leading-[1.1] tracking-[-0.03em]">Fixed-time <span className="text-ink-3">vs</span> FlowState</h1>
        <SideBySide />
        <Headline />
        <HeadlineMeasures />
        <HeadToHead d={D} />
        <LivePlaceholder />
        <p className="m-0 text-[12px] leading-[1.5] text-ink-3">
          {D.seeds.length} seeds × {patterns} traffic patterns per controller · same network and safety timings for both · rush-hour ramp held out of tuning.
        </p>
      </main>
    </div>
  )
}
