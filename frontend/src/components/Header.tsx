import { Expand, PanelLeft, PanelRight, Shrink } from 'lucide-react'
import { useStore, type View } from '../lib/store'
import { cn } from '../lib/format'
import { Dot, Tip } from './ui'

const TABS: { id: View; label: string }[] = [
  { id: 'simulate', label: 'Simulation' },
  { id: 'analyze', label: 'Analytics' },
  { id: 'runs', label: 'Run history' },
  { id: 'results', label: 'Results' },
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

export function Header() {
  const view = useStore((s) => s.view)
  const set = useStore((s) => s.set)
  const conn = useStore((s) => s.connection)
  const health = useStore((s) => s.health)
  const run = useStore((s) => s.run)
  const networks = useStore((s) => s.networks)
  const panels = useStore((s) => s.panels)
  const togglePanel = useStore((s) => s.togglePanel)
  const netTitle = networks[run?.network ?? 'district']?.title ?? 'Downtown Toronto · King West'
  const mapOnly = !panels.left && !panels.right
  return (
    <header className="relative z-20 flex h-12 shrink-0 items-center gap-7 border-b border-border bg-surface pl-5 pr-4">
      <div className="flex shrink-0 items-center gap-2.5">
        <Logo />
        <span className="text-[14px] font-[650] tracking-[-0.01em] text-ink">FlowState</span>
        <span className="hidden h-[18px] items-center rounded-[6px] px-[7px] text-[10.5px] font-semibold text-ink-3 shadow-[inset_0_0_0_1px_var(--color-border-strong)] lg:inline-flex">SUMO twin</span>
      </div>
      <nav aria-label="Main" className="flex gap-[22px] self-stretch">
        {TABS.map((t) => (
          <button
            key={t.id}
            aria-current={view === t.id ? 'page' : undefined}
            onClick={() => set({ view: t.id })}
            className={cn(
              'inline-flex h-12 items-center px-0.5 text-[13px] font-medium transition-[color,box-shadow] duration-150 ease-out',
              view === t.id ? 'text-ink shadow-[inset_0_-2px_0_var(--color-ink)]' : 'text-ink-3 hover:text-ink-2',
            )}
          >
            {t.label}
          </button>
        ))}
      </nav>
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
