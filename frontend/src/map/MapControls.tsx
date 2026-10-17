import { Home, Layers, Maximize2, Minus, Plus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Switch, Tip } from '../components/ui'
import { useStore, type Layers as L } from '../lib/store'
import { mapApi } from './MapView'

const LAYER_LABELS: [keyof L, string, string][] = [
  ['buildings', 'Buildings & trees', 'Context layer'],
  ['labels', 'Street & place labels', ''],
  ['pedestrians', 'Pedestrians', 'SUMO striping model'],
  ['signals', 'Signal heads', 'Live phase per lane'],
  ['flow', 'Traffic flow', 'Edge speed vs. limit'],
]

function ToolButton({ label, onClick, children, active }: { label: string; onClick: () => void; children: React.ReactNode; active?: boolean }) {
  return (
    <Tip label={label} side="left">
      <button
        aria-label={label}
        onClick={onClick}
        className={`flex h-8 w-8 items-center justify-center rounded-lg text-ink-2 transition-colors hover:bg-black/[0.05] hover:text-ink ${active ? 'bg-accent-soft text-accent-strong' : ''}`}
      >
        {children}
      </button>
    </Tip>
  )
}

export function MapControls() {
  const [open, setOpen] = useState(false)
  const layers = useStore((s) => s.layers)
  const toggle = useStore((s) => s.toggleLayer)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('pointerdown', close)
    return () => window.removeEventListener('pointerdown', close)
  }, [open])

  return (
    <div className="absolute right-3 top-3 z-10 flex flex-col items-end gap-2" ref={ref}>
      <div className="glass flex flex-col gap-0.5 rounded-xl p-1">
        <ToolButton label="Zoom in (+)" onClick={() => mapApi.current?.zoom(1.6)}>
          <Plus size={15} />
        </ToolButton>
        <ToolButton label="Zoom out (−)" onClick={() => mapApi.current?.zoom(1 / 1.6)}>
          <Minus size={15} />
        </ToolButton>
      </div>
      <div className="glass flex flex-col gap-0.5 rounded-xl p-1">
        <ToolButton label="Home · Main & King (H)" onClick={() => mapApi.current?.home()}>
          <Home size={15} />
        </ToolButton>
        <ToolButton label="Full network (F)" onClick={() => mapApi.current?.fit()}>
          <Maximize2 size={14} />
        </ToolButton>
        <ToolButton label="Map layers" onClick={() => setOpen((o) => !o)} active={open}>
          <Layers size={15} />
        </ToolButton>
      </div>
      {open && (
        <div className="glass anim-in w-60 rounded-xl p-1.5">
          <div className="label-caps px-2 pb-1 pt-1">Layers</div>
          {LAYER_LABELS.map(([k, label, hint]) => (
            <label key={k} className="flex cursor-pointer items-center justify-between rounded-lg px-2 py-1.5 hover:bg-black/[0.035]">
              <span>
                <span className="block text-[12.5px] font-medium text-ink">{label}</span>
                {hint && <span className="block text-[11px] text-ink-3">{hint}</span>}
              </span>
              <Switch checked={layers[k]} onChange={() => toggle(k)} />
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
