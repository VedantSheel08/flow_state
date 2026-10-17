// Interactive logs table — search, a slide-in filter panel and rows that expand
// into details. Adapted from the 21st.dev "interactive-logs-table-shadcnui":
// - generic entries (the caller maps its own records) instead of sample logs
// - CSS transitions instead of framer-motion, so no new dependency; every
//   transition stops under prefers-reduced-motion
// - the app's own Button/Badge and colour tokens instead of shadcn + Tailwind
//   palette classes, so it follows whatever surface it sits on (asphalt here)

import { Check, ChevronDown, Filter, Search, X } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { cn } from '../../lib/format'
import { Button } from './index'

export type LogTone = 'neutral' | 'warn' | 'muted'

export interface LogEntry {
  id: string
  /** Short time label shown in the row. */
  time: string
  /** Level chip: its key drives the filter, label and tone drive the look. */
  level: { key: string; label: string; tone: LogTone }
  /** Bold name column (a service, a scenario…). */
  service: string
  /** One-line summary, truncated in the row. */
  message: string
  /** Right-hand status code. */
  status: string
  /** Right-most figure, e.g. a duration. */
  duration: string
  tags: string[]
  /** Extra text the search box should match. */
  search?: string
  /** Rendered when the row is expanded. */
  details?: ReactNode
}

type Filters = { level: string[]; service: string[]; status: string[] }
const NO_FILTERS: Filters = { level: [], service: [], status: [] }

const TONE: Record<LogTone, string> = {
  neutral: 'bg-muted text-ink shadow-[inset_0_0_0_1px_var(--color-border-strong)]',
  warn: 'bg-warn-soft text-warn shadow-[inset_0_0_0_1px_var(--color-warn-line)]',
  muted: 'bg-transparent text-ink-3 shadow-[inset_0_0_0_1px_var(--color-border)]',
}

function LevelChip({ level }: { level: LogEntry['level'] }) {
  return <span className={cn('inline-flex h-[22px] w-[92px] shrink-0 items-center justify-center rounded-full px-2 text-[11.5px] font-[550] whitespace-nowrap', TONE[level.tone])}>{level.label}</span>
}

function LogRow({ log, expanded, onToggle }: { log: LogEntry; expanded: boolean; onToggle: () => void }) {
  return (
    <div className={cn('transition-colors duration-150', expanded && 'bg-white/[0.03]')}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="w-full px-5 py-3.5 text-left transition-colors duration-150 hover:bg-white/[0.04] active:bg-white/[0.06]"
      >
        <div className="flex items-center gap-4">
          <ChevronDown size={16} className={cn('shrink-0 text-ink-3 transition-transform duration-200 ease-out motion-reduce:transition-none', expanded && 'rotate-180')} />
          <LevelChip level={log.level} />
          <time className="tnum w-[112px] shrink-0 whitespace-nowrap text-[12px] text-ink-3">{log.time}</time>
          <span className="w-[150px] shrink-0 truncate text-[13px] font-semibold text-ink">{log.service}</span>
          <p className="m-0 min-w-0 flex-1 truncate text-[13px] text-ink-2">{log.message}</p>
          <span className="tnum w-[84px] shrink-0 text-right text-[12.5px] font-semibold text-ink-2">{log.status}</span>
          <span className="tnum w-14 shrink-0 text-right text-[12.5px] text-ink-3">{log.duration}</span>
        </div>
      </button>
      {/* grid-rows 0fr → 1fr animates to the content's real height */}
      <div className={cn('grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none', expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0')}>
        <div className="min-h-0 overflow-hidden">
          <div className="border-t border-border px-5 pb-5 pl-[52px] pt-4" hidden={!expanded}>
            {log.details}
            {log.tags.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {log.tags.map((t) => (
                  <span key={t} className="inline-flex h-5 items-center rounded-md px-1.5 text-[11px] font-medium text-ink-2 shadow-[inset_0_0_0_1px_var(--color-border-strong)]">
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function FilterGroup({ title, values, selected, onToggle, label }: { title: string; values: string[]; selected: string[]; onToggle: (v: string) => void; label?: (v: string) => string }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="m-0 text-[12px] font-semibold text-ink-3">{title}</p>
      <div className="flex flex-col gap-1">
        {values.map((v) => {
          const on = selected.includes(v)
          return (
            <button
              key={v}
              type="button"
              aria-pressed={on}
              onClick={() => onToggle(v)}
              className={cn(
                'flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] transition-[background-color,color,box-shadow] duration-150',
                on ? 'bg-ink/[0.12] font-medium text-ink shadow-[inset_0_0_0_1px_var(--color-border-strong)]' : 'text-ink-2 hover:bg-white/[0.05] hover:text-ink',
              )}
            >
              <span className="truncate">{label ? label(v) : v}</span>
              {on && <Check size={13} className="shrink-0" />}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function InteractiveLogsTable({
  logs,
  title = 'Logs',
  noun = 'logs',
  placeholder = 'Search…',
  filterTitles = { level: 'Level', service: 'Service', status: 'Status' },
  className,
}: {
  logs: LogEntry[]
  title?: ReactNode
  noun?: string
  placeholder?: string
  filterTitles?: { level: string; service: string; status: string }
  className?: string
}) {
  const [query, setQuery] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [showFilters, setShowFilters] = useState(false)
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)

  const levelLabel = useMemo(() => Object.fromEntries(logs.map((l) => [l.level.key, l.level.label])), [logs])
  const options = useMemo(
    () => ({
      level: [...new Set(logs.map((l) => l.level.key))],
      service: [...new Set(logs.map((l) => l.service))].sort(),
      status: [...new Set(logs.map((l) => l.status))],
    }),
    [logs],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return logs.filter((l) => {
      const text = `${l.service} ${l.message} ${l.status} ${l.search ?? ''}`.toLowerCase()
      return (
        (!q || text.includes(q)) &&
        (!filters.level.length || filters.level.includes(l.level.key)) &&
        (!filters.service.length || filters.service.includes(l.service)) &&
        (!filters.status.length || filters.status.includes(l.status))
      )
    })
  }, [logs, filters, query])

  const toggle = (k: keyof Filters, v: string) => setFilters((f) => ({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v] }))
  const active = filters.level.length + filters.service.length + filters.status.length

  return (
    <section className={cn('flex min-h-0 flex-col overflow-hidden', className)}>
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-4">
        <div className="mr-auto min-w-[180px]">
          <h2 className="m-0 text-[16px] font-semibold text-ink">{title}</h2>
          <p className="tnum m-0 text-[12.5px] text-ink-3">
            {filtered.length} of {logs.length} {noun}
          </p>
        </div>
        <label className="relative min-w-[220px] flex-[1_1_320px]">
          <span className="sr-only">Search {noun}</span>
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={placeholder}
            className="h-9 w-full rounded-lg border border-border-strong bg-white/[0.04] pl-9 pr-8 text-[13px] text-ink outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-ink-3 focus:border-flow focus:shadow-[0_0_0_3px_rgb(42_120_214/0.25)]"
          />
          {query && (
            <button type="button" aria-label="Clear search" onClick={() => setQuery('')} className="absolute right-2 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-ink-3 hover:bg-white/[0.06] hover:text-ink">
              <X size={13} />
            </button>
          )}
        </label>
        <Button variant={showFilters ? 'primary' : 'secondary'} className="relative h-9" aria-pressed={showFilters} onClick={() => setShowFilters((s) => !s)}>
          <Filter size={14} /> Filters
          {active > 0 && <span className="tnum ml-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-flow px-1 text-[11px] font-semibold text-white">{active}</span>}
        </Button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div
          className={cn('shrink-0 overflow-hidden border-border transition-[width,opacity] duration-200 ease-out motion-reduce:transition-none', showFilters ? 'w-[260px] border-r opacity-100' : 'w-0 opacity-0')}
          aria-hidden={!showFilters}
          inert={!showFilters}
        >
          <div className="flex w-[260px] flex-col gap-5 p-4">
            <div className="flex h-6 items-center justify-between">
              <span className="text-[13px] font-semibold text-ink">Filters</span>
              {active > 0 && (
                <button type="button" onClick={() => setFilters(NO_FILTERS)} className="rounded-md px-1.5 py-0.5 text-[12px] font-medium text-ink-2 hover:bg-white/[0.06] hover:text-ink">
                  Clear
                </button>
              )}
            </div>
            <FilterGroup title={filterTitles.level} values={options.level} selected={filters.level} onToggle={(v) => toggle('level', v)} label={(v) => levelLabel[v] ?? v} />
            <FilterGroup title={filterTitles.service} values={options.service} selected={filters.service} onToggle={(v) => toggle('service', v)} />
            <FilterGroup title={filterTitles.status} values={options.status} selected={filters.status} onToggle={(v) => toggle('status', v)} />
          </div>
        </div>

        <div className="min-w-0 flex-1 overflow-x-auto">
          <div className="min-w-[760px] divide-y divide-border">
            {filtered.map((l) => (
              <LogRow key={l.id} log={l} expanded={expandedId === l.id} onToggle={() => setExpandedId((c) => (c === l.id ? null : l.id))} />
            ))}
            {filtered.length === 0 && (
              <div className="px-6 py-14 text-center">
                <p className="m-0 text-[13px] font-medium text-ink">No {noun} match.</p>
                <p className="m-0 mt-1 text-[12.5px] text-ink-3">Clear the search or filters to see them all.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
