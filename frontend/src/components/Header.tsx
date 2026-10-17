import { useStore, type View } from '../lib/store'
import { cn } from '../lib/format'
import { Dot, Tip } from './ui'

const TABS: { id: View; label: string }[] = [
  { id: 'simulate', label: 'Simulation' },
  { id: 'analyze', label: 'Analytics' },
  { id: 'runs', label: 'Run history' },
]

export function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#101828" />
      <path d="M8 21.5c4.5 0 5.5-11 10-11h6" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" />
      <path d="M8 10.5h4M20 21.5h4" stroke="#60A5FA" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  )
}

export function Header() {
  const view = useStore((s) => s.view)
  const set = useStore((s) => s.set)
  const conn = useStore((s) => s.connection)
  const health = useStore((s) => s.health)
  const run = useStore((s) => s.run)
  const networks = useStore((s) => s.networks)
  const netTitle = networks[run?.network ?? 'district']?.title ?? 'Riverside District'
  return (
    <header className="flex h-12 shrink-0 items-center border-b border-border bg-surface px-4">
      <div className="flex items-center gap-2.5">
        <Logo />
        <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">FlowState</span>
      </div>
      <div className="mx-4 h-5 w-px bg-border" />
      <nav className="flex h-full items-center gap-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => set({ view: t.id })}
            className={cn(
              'relative h-full px-2.5 text-[13px] font-medium transition-colors',
              view === t.id ? 'text-ink after:absolute after:inset-x-2.5 after:bottom-0 after:h-[2px] after:rounded-full after:bg-ink' : 'text-ink-3 hover:text-ink',
            )}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-4 text-[12px] text-ink-2">
        <span className="hidden items-center gap-1.5 md:flex">
          <span className="text-ink-3">Network</span>
          <span className="font-medium text-ink">{netTitle}</span>
        </span>
        <div className="h-4 w-px bg-border" />
        <Tip label={conn === 'open' ? `Connected to the simulation server (${health?.backend ?? 'TraCI'})` : 'Simulation server unreachable — retrying'}>
          <span className="flex items-center gap-1.5">
            <Dot tone={conn === 'open' ? 'good' : conn === 'connecting' ? 'warn' : 'bad'} />
            <span className="font-medium text-ink">{conn === 'open' ? `SUMO ${health?.sumo ?? ''}` : conn === 'connecting' ? 'Connecting…' : 'Offline'}</span>
          </span>
        </Tip>
      </div>
    </header>
  )
}
