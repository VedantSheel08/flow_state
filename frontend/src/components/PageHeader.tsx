import type { ReactNode } from 'react'

/** Title block shared by the full-page views (Analytics, Run history). */
export function PageHeader({ title, children, right }: { title: ReactNode; children?: ReactNode; right?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-6">
      <div className="flex min-w-0 flex-[999_1_520px] flex-col gap-2">
        <h1 className="m-0 text-balance text-[30px] font-[650] leading-[1.15] tracking-[-0.022em] text-ink">{title}</h1>
        {children && <div className="max-w-[720px] text-pretty text-[14px] leading-[1.6] text-ink-2">{children}</div>}
      </div>
      {right && <div className="flex shrink-0 flex-wrap items-center gap-2">{right}</div>}
    </header>
  )
}
