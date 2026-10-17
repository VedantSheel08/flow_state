import { MapPin, Shuffle, X } from 'lucide-react'
import { num } from '../lib/format'
import { useStore } from '../lib/store'
import type { NetworkGeom, ParamSpec } from '../lib/types'
import { Button, Segmented, Slider, Switch } from './ui'

function edgeLabel(net: NetworkGeom | undefined, id: string): string {
  const e = net?.edges.find((x) => x.id === id)
  if (!e) return id
  const dir = { e: 'eastbound', w: 'westbound', n: 'northbound', s: 'southbound' }[e.id.split('_')[1]?.[0] ?? ''] ?? ''
  return `${e.name} ${dir}`
}

export function ParamField({ spec, value, onChange, net }: { spec: ParamSpec; value: unknown; onChange: (v: unknown) => void; net?: NetworkGeom }) {
  const pick = useStore((s) => s.pick)
  const set = useStore((s) => s.set)
  const label = (
    <div className="flex items-baseline justify-between gap-2">
      <label className="text-[12.5px] font-medium text-ink" title={spec.description ?? undefined}>
        {spec.label}
      </label>
      {(spec.type === 'int' || spec.type === 'float') && (
        <span className="tnum text-[12px] text-ink-2">
          {num(Number(value), spec.type === 'float' ? (spec.step && spec.step < 0.1 ? 2 : 1) : 0)}
          {spec.unit && <span className="ml-0.5 text-ink-3">{spec.unit === '×' ? '×' : ` ${spec.unit}`}</span>}
        </span>
      )}
    </div>
  )
  if (spec.type === 'bool') {
    return (
      <div className="flex items-center justify-between gap-3 py-0.5">
        <div className="min-w-0">
          <div className="text-[12.5px] font-medium text-ink">{spec.label}</div>
          {spec.description && <div className="text-[11.5px] leading-snug text-ink-3">{spec.description}</div>}
        </div>
        <Switch checked={!!value} onChange={onChange} />
      </div>
    )
  }
  if (spec.type === 'select') {
    const opts = spec.options ?? []
    const short = opts.length <= 3 && opts.every((o) => String(o.label).length <= 14)
    return (
      <div className="space-y-1.5">
        {label}
        {short ? (
          <Segmented className="w-full [&>button]:flex-1" value={value as string} options={opts.map((o) => ({ value: o.value as string, label: o.label }))} onChange={onChange} />
        ) : (
          <select
            value={String(value)}
            onChange={(e) => {
              const o = opts.find((x) => String(x.value) === e.target.value)
              onChange(o ? o.value : e.target.value)
            }}
            className="h-8 w-full rounded-md border border-border-strong bg-surface px-2 text-[12.5px] text-ink shadow-xs outline-none focus:border-accent"
          >
            {opts.map((o) => (
              <option key={String(o.value)} value={String(o.value)}>
                {o.label}
              </option>
            ))}
          </select>
        )}
      </div>
    )
  }
  if (spec.type === 'edge') {
    const random = value === 'random' || !value
    const picking = pick === spec.key
    return (
      <div className="space-y-1.5">
        {label}
        <div className="flex items-center gap-1.5">
          <div className={`flex h-8 min-w-0 flex-1 items-center gap-1.5 rounded-md border px-2 text-[12.5px] ${picking ? 'border-accent bg-accent-soft' : 'border-border-strong bg-surface'}`}>
            {random ? <Shuffle size={13} className="text-ink-3" /> : <MapPin size={13} className="text-accent" />}
            <span className="truncate text-ink">{picking ? 'Click a street on the map…' : random ? 'Random valid street' : edgeLabel(net, String(value))}</span>
          </div>
          {!random && (
            <Button size="icon" variant="ghost" aria-label="Use random location" onClick={() => onChange('random')}>
              <X size={14} />
            </Button>
          )}
          <Button size="sm" variant={picking ? 'primary' : 'secondary'} onClick={() => set({ pick: picking ? null : spec.key, view: 'simulate' })}>
            {picking ? 'Cancel' : 'Pick'}
          </Button>
        </div>
      </div>
    )
  }
  const min = spec.min ?? 0
  const max = spec.max ?? 100
  const step = spec.step ?? (spec.type === 'int' ? 1 : 0.1)
  return (
    <div className="space-y-1.5">
      {label}
      <Slider value={Number(value)} min={min} max={max} step={step} onChange={(v) => onChange(spec.type === 'int' ? Math.round(v) : Math.round(v / step) * step)} />
    </div>
  )
}
