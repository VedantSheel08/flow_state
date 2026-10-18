import { CircleCheck, CircleMinus, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { VERDICT_LABEL, type ControllerId, type Verdict } from '../lib/controllerResults'
import { cn } from '../lib/format'
import { COLOR } from '../lib/resultsTheme'

// Shared building blocks for the Results tab and the (not yet shown) live comparison.
export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('text-[11px] font-semibold uppercase leading-[1.45] tracking-[0.05em] text-ink-3', className)}>{children}</div>
}

export function H2({ children, className }: { children: ReactNode; className?: string }) {
  return <h2 className={cn('m-0 text-balance text-[18px] font-semibold leading-[1.44] tracking-[-0.01em] text-ink', className)}>{children}</h2>
}

export function Cap({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn('m-0 text-pretty text-[13px] leading-[1.54] text-ink-3', className)}>{children}</p>
}

const VTONE: Record<Verdict, string> = {
  better: 'bg-good-soft text-good shadow-[inset_0_0_0_1px_var(--color-good-line)]',
  worse: 'bg-bad-soft text-bad shadow-[inset_0_0_0_1px_var(--color-bad-line)]',
  unclear: 'bg-muted text-ink-2 shadow-[inset_0_0_0_1px_#e1e4e9]',
}

export function Pill({ tone, children }: { tone: Verdict | 'warn' | 'live'; children: ReactNode }) {
  const t =
    tone === 'warn'
      ? 'bg-warn-soft text-warn shadow-[inset_0_0_0_1px_var(--color-warn-line)]'
      : tone === 'live'
        ? 'bg-flow-soft text-flow-ink shadow-[inset_0_0_0_1px_var(--color-flow-line)]'
        : VTONE[tone]
  return <span className={cn('inline-flex h-[22px] shrink-0 items-center gap-[5px] whitespace-nowrap rounded-full pl-1.5 pr-2 text-[12px] font-[550]', t)}>{children}</span>
}

export function VerdictBadge({ v, label }: { v: Verdict; label?: string }) {
  const Icon = v === 'better' ? CircleCheck : v === 'worse' ? TriangleAlert : CircleMinus
  return (
    <Pill tone={v}>
      <Icon size={13} strokeWidth={2.5} />
      {label ?? VERDICT_LABEL[v]}
    </Pill>
  )
}

export function Swatch({ c, size = 8 }: { c: ControllerId; size?: number }) {
  return <span className="inline-block shrink-0 rounded-[2px]" style={{ width: size, height: size, background: COLOR[c] }} />
}

export function Th({ children, left }: { children: ReactNode; left?: boolean }) {
  return <th className={cn('whitespace-nowrap border-b border-border px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.05em] text-ink-3 first:pl-6 last:pr-6', left ? 'text-left' : 'text-right')}>{children}</th>
}

export function Td({ children, left, last, className }: { children: ReactNode; left?: boolean; last?: boolean; className?: string }) {
  return <td className={cn('whitespace-nowrap px-4 py-[11px] first:pl-6 last:pr-6', !last && 'border-b border-muted', left ? 'text-left' : 'text-right', className)}>{children}</td>
}
