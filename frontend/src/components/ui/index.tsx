import * as RSlider from '@radix-ui/react-slider'
import * as RSwitch from '@radix-ui/react-switch'
import * as RTooltip from '@radix-ui/react-tooltip'
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { cn } from '../../lib/format'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'icon' | 'icon-sm'

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }>(
  ({ variant = 'secondary', size = 'md', className, ...rest }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-[background-color,box-shadow,color,transform] duration-100 active:translate-y-px disabled:pointer-events-none disabled:opacity-45',
        size === 'md' && 'h-8 px-3 text-[13px]',
        size === 'sm' && 'h-7 px-2.5 text-[12px]',
        size === 'icon' && 'h-8 w-8',
        size === 'icon-sm' && 'h-7 w-7',
        variant === 'primary' &&
          'bg-[linear-gradient(180deg,#222d40,#0c1322)] text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_1px_2px_rgba(16,24,40,0.24),0_0_0_1px_rgba(12,19,34,0.9)] hover:bg-[linear-gradient(180deg,#2b3850,#141d2e)]',
        variant === 'secondary' && 'border border-border-strong bg-surface text-ink shadow-xs hover:border-[#c5ccd5] hover:bg-subtle',
        variant === 'ghost' && 'text-ink-2 hover:bg-muted hover:text-ink',
        variant === 'danger' && 'border border-border-strong bg-surface text-bad hover:bg-bad-soft',
        className,
      )}
      {...rest}
    />
  ),
)
Button.displayName = 'Button'

export function Tip({ label, children, side = 'bottom' }: { label: ReactNode; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  return (
    <RTooltip.Root delayDuration={350}>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content side={side} sideOffset={6} className="anim-in z-50 rounded-md bg-ink px-2 py-1 text-[11.5px] font-medium text-white shadow-float">
          {label}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  )
}

export const TooltipProvider = RTooltip.Provider

export function Switch({ checked, onChange, disabled, id }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; id?: string }) {
  return (
    <RSwitch.Root
      id={id}
      checked={checked}
      onCheckedChange={onChange}
      disabled={disabled}
      className="relative h-[18px] w-[30px] shrink-0 rounded-full bg-border-strong transition-colors data-[state=checked]:bg-accent disabled:opacity-50"
    >
      <RSwitch.Thumb className="block h-[14px] w-[14px] translate-x-[2px] rounded-full bg-white shadow-xs transition-transform data-[state=checked]:translate-x-[14px]" />
    </RSwitch.Root>
  )
}

export function Slider({ value, min, max, step, onChange, onCommit, disabled }: { value: number; min: number; max: number; step: number; onChange: (v: number) => void; onCommit?: (v: number) => void; disabled?: boolean }) {
  return (
    <RSlider.Root
      className="relative flex h-4 w-full touch-none select-none items-center"
      value={[value]}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onValueChange={(v) => onChange(v[0])}
      onValueCommit={(v) => onCommit?.(v[0])}
    >
      <RSlider.Track className="relative h-[3px] grow rounded-full bg-border">
        <RSlider.Range className="absolute h-full rounded-full bg-accent" />
      </RSlider.Track>
      <RSlider.Thumb className="block h-3.5 w-3.5 rounded-full border border-border-strong bg-white shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-accent/40" />
    </RSlider.Root>
  )
}

export function Segmented<T extends string | number>({ value, options, onChange, size = 'md', className }: { value: T; options: { value: T; label: ReactNode; title?: string }[]; onChange: (v: T) => void; size?: 'sm' | 'md'; className?: string }) {
  return (
    <div className={cn('inline-flex rounded-lg border border-black/[0.06] bg-black/[0.035] p-0.5', className)}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-md px-2 font-medium tnum transition-all duration-100',
            size === 'sm' ? 'h-6 text-[11.5px]' : 'h-7 text-[12px]',
            o.value === value ? 'bg-surface text-ink shadow-[0_1px_2px_rgba(16,24,40,0.1),0_0_0_1px_rgba(16,24,40,0.04)]' : 'text-ink-2 hover:text-ink',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Dot({ tone }: { tone: 'good' | 'warn' | 'bad' | 'idle' | 'accent' }) {
  const c = { good: 'bg-good', warn: 'bg-[#f79009]', bad: 'bg-bad', idle: 'bg-ink-3', accent: 'bg-accent' }[tone]
  return <span className={cn('inline-block h-1.5 w-1.5 shrink-0 rounded-full', c)} />
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'accent' | 'good' | 'warn' | 'bad' }) {
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center rounded-full px-2 text-[11px] font-medium',
        tone === 'neutral' && 'bg-muted text-ink-2',
        tone === 'accent' && 'bg-accent-soft text-accent-strong',
        tone === 'good' && 'bg-good-soft text-good',
        tone === 'warn' && 'bg-warn-soft text-warn',
        tone === 'bad' && 'bg-bad-soft text-bad',
      )}
    >
      {children}
    </span>
  )
}

export function Section({ title, right, children, className }: { title: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('border-b border-border px-4 py-3.5', className)}>
      <div className="mb-2.5 flex h-5 items-center justify-between">
        <h3 className="label-caps">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <div className="text-[13px] font-medium text-ink">{title}</div>
      {children && <div className="mt-1 max-w-[260px] text-[12px] text-ink-3">{children}</div>}
    </div>
  )
}
