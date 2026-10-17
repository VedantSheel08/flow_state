import { Expand, PanelLeft, PanelRight, Shrink } from 'lucide-react'
import { useLayoutEffect, useRef, useState } from 'react'
import { useStore, type View } from '../lib/store'
import { cn } from '../lib/format'
import { Dot, Tip } from './ui'

const TABS: { id: View; label: string }[] = [
  { id: 'simulate', label: 'Simulation' },
  { id: 'results', label: 'Results' },
  { id: 'analyze', label: 'Analytics' },
  { id: 'runs', label: 'Run history' },
]

export function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#0c1322" />
      <path d="M6 9h7.5a2.5 2.5 0 0 0 0-5" stroke="#2a78d6" strokeWidth="2" fill="none" strokeLinecap="round" />
      <path d="M6 15h12" stroke="#ffffff" strokeWidth="2" strokeLinecap="round" />
      <path d="M6 20h5" stroke="#eb6834" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function PanelToggle({ label, active, onClick, children }: { label: string; active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tip label={label}>
      <button
        aria-label={label}
        aria-pressed={active}
        onClick={onClick}
        className={cn('flex h-7 w-7 items-center justify-center rounded-md transition-colors', active ? 'text-ink hover:bg-muted' : 'text-ink-3 hover:bg-muted hover:text-ink')}
      >
        {children}
      </button>
    </Tip>
  )
}

/** Segmented tab bar; the white thumb slides to the tab you pick. */
function TabNav({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  const refs = useRef<Partial<Record<View, HTMLButtonElement | null>>>({})
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null)
  useLayoutEffect(() => {
    const measure = () => {
      const el = refs.current[view]
      setThumb(el ? { x: el.offsetLeft, w: el.offsetWidth } : null)
    }
    measure()
    document.fonts?.ready.then(measure)
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [view])
  return (
    <nav aria-label="Main" className="relative flex h-8 items-center gap-0.5 rounded-[10px] bg-track p-0.5">
      {thumb && (
        <span
          aria-hidden
          className="absolute left-0 top-0.5 h-7 rounded-lg bg-surface shadow-thumb transition-[translate,width] duration-[260ms] ease-[var(--ease-out-quint)] motion-reduce:transition-none"
          style={{ translate: `${thumb.x}px 0`, width: thumb.w }}
        />
      )}
      {TABS.map((t) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[t.id] = el
          }}
          aria-current={view === t.id ? 'page' : undefined}
          onClick={() => onChange(t.id)}
          className={cn(
            'relative inline-flex h-7 items-center rounded-lg px-3 text-[13px] font-medium transition-colors duration-150 ease-out',
            view === t.id ? 'text-ink' : 'text-ink-3 hover:text-ink',
          )}
        >
          {t.label}
        </button>
      ))}
    </nav>
  )
}

export function Header() {
  const view = useStore((s) => s.view)
  const set = useStore((s) => s.set)
  const conn = useStore((s) => s.connection)
  const health = useStore((s) => s.health)
  const run = useStore((s) => s.run)
  const networks = useStore((s) => s.networks)
  const panels = useStore((s) => s.panels)
  const togglePanel = useStore((s) => s.togglePanel)
  const netTitle = networks[run?.network ?? 'district']?.title ?? 'Riverside District'
  const mapOnly = !panels.left && !panels.right
  return (
    <header className="relative z-20 flex h-12 shrink-0 items-center gap-6 border-b border-border bg-surface pl-5 pr-4">
      <div className="flex shrink-0 items-center gap-2.5">
        <button
          type="button"
          aria-label="FlowState home"
          aria-current={view === 'home' ? 'page' : undefined}
          onClick={() => set({ view: 'home' })}
          className="-ml-1 flex items-center gap-2.5 rounded-lg py-1 pl-1 pr-1.5 transition-[background-color,scale] duration-150 ease-out hover:bg-muted active:scale-[0.96] motion-reduce:active:scale-100"
        >
          <Logo />
          <span className="text-[14px] font-[650] tracking-[-0.01em] text-ink">FlowState</span>
        </button>
        <span className="hidden h-[18px] items-center rounded-[6px] px-[7px] text-[10.5px] font-semibold text-ink-3 shadow-[inset_0_0_0_1px_var(--color-border-strong)] lg:inline-flex">SUMO twin</span>
      </div>
      <TabNav view={view} onChange={(v) => set({ view: v })} />
      <div className="ml-auto flex items-center gap-2.5 text-[12px] text-ink-2">
        <span className="hidden text-ink-3 md:inline">{view === 'results' ? 'rl_grid · 2×2 signalised' : netTitle}</span>
        <Tip label={conn === 'open' ? `Connected to the simulation server (${health?.backend ?? 'TraCI'})` : 'Simulation server unreachable — retrying'}>
          <span className="flex h-6 items-center gap-1.5 rounded-full bg-subtle px-[9px] shadow-[inset_0_0_0_1px_var(--color-border)]">
            <Dot tone={conn === 'open' ? 'good' : conn === 'connecting' ? 'warn' : 'bad'} />
            <span className="font-medium text-ink-2">{conn === 'open' ? `SUMO ${health?.sumo ?? ''}` : conn === 'connecting' ? 'Connecting…' : 'Offline'}</span>
          </span>
        </Tip>
        {view === 'simulate' && (
          <>
            <div className="h-4 w-px bg-border" />
            <div className="flex items-center gap-0.5">
              <PanelToggle label={panels.left ? 'Hide scenarios ( [ )' : 'Show scenarios ( [ )'} active={panels.left} onClick={() => togglePanel('left')}>
                <PanelLeft size={15} />
              </PanelToggle>
              <PanelToggle label={panels.right ? 'Hide inspector ( ] )' : 'Show inspector ( ] )'} active={panels.right} onClick={() => togglePanel('right')}>
                <PanelRight size={15} />
              </PanelToggle>
              <PanelToggle
                label={mapOnly ? 'Restore panels ( \\ )' : 'Map only ( \\ )'}
                active={mapOnly}
                onClick={() => {
                  togglePanel('left', mapOnly)
                  togglePanel('right', mapOnly)
                }}
              >
                {mapOnly ? <Shrink size={14} /> : <Expand size={14} />}
              </PanelToggle>
            </div>
          </>
        )}
      </div>
    </header>
  )
}
