import { Expand, PanelLeft, PanelRight, Shrink } from 'lucide-react'
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
    <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id="fs-logo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1b2537" />
          <stop offset="1" stopColor="#0b111d" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="url(#fs-logo)" />
      <path d="M8 21.5c4.5 0 5.5-11 10-11h6" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" />
      <path d="M8 10.5h4M20 21.5h4" stroke="#60A5FA" strokeWidth="2.6" strokeLinecap="round" />
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
    <header className="relative z-20 flex h-12 shrink-0 items-center border-b border-border bg-surface/95 px-3.5 backdrop-blur">
      <div className="flex items-center gap-2.5">
        <Logo />
        <span className="text-[14.5px] font-semibold tracking-[-0.02em] text-ink">FlowState</span>
        <span className="hidden rounded-[5px] border border-border px-1.5 py-px text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-3 lg:inline">SUMO twin</span>
      </div>
      <div className="mx-4 h-5 w-px bg-border" />
      <nav className="flex items-center gap-0.5 rounded-lg bg-muted p-0.5">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => set({ view: t.id })}
            className={cn(
              'h-7 rounded-md px-3 text-[12.5px] font-medium transition-all duration-150',
              view === t.id ? 'bg-surface text-ink shadow-[0_1px_2px_rgba(16,24,40,0.08),0_0_0_1px_rgba(16,24,40,0.04)]' : 'text-ink-2 hover:text-ink',
            )}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3 text-[12px] text-ink-2">
        <span className="hidden items-center gap-1.5 md:flex">
          <span className="text-ink-3">Network</span>
          <span className="font-medium text-ink">{netTitle}</span>
        </span>
        <div className="h-4 w-px bg-border" />
        <Tip label={conn === 'open' ? `Connected to the simulation server (${health?.backend ?? 'TraCI'})` : 'Simulation server unreachable — retrying'}>
          <span className="flex items-center gap-1.5 rounded-full border border-border bg-subtle px-2 py-[3px]">
            <Dot tone={conn === 'open' ? 'good' : conn === 'connecting' ? 'warn' : 'bad'} />
            <span className="font-medium text-ink">{conn === 'open' ? `SUMO ${health?.sumo ?? ''}` : conn === 'connecting' ? 'Connecting…' : 'Offline'}</span>
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
