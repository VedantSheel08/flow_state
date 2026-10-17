import type { ReactNode } from 'react'
import HalftoneWordmarkFooter from './ui/halftone-wordmark-footer'

/**
 * Asphalt masthead that opens every full-page view: the page name printed as
 * a halftone wordmark hung off the right edge, with the title block on the left.
 * The same print as the Home page, at page-header scale.
 */
export function PageMasthead({ word, kicker, title, children, right }: { word: string; kicker?: ReactNode; title: ReactNode; children?: ReactNode; right?: ReactNode }) {
  return (
    <HalftoneWordmarkFooter
      as="header"
      preset="asphalt"
      wordmark={word}
      fit={0.5}
      align="right"
      crop={0.3}
      dotGap={5}
      radius={64}
      grain={0.4}
      height="248px"
      className="asphalt shrink-0"
    >
      <div className="relative z-[1] mx-auto flex h-full max-w-[1312px] flex-wrap content-start items-start justify-between gap-x-6 gap-y-4 px-6 pt-9 min-[1100px]:px-8">
        <div className="flex min-w-0 max-w-[min(600px,52%)] flex-col gap-2.5 max-[900px]:max-w-full">
          {kicker && <div className="hwf-kicker">{kicker}</div>}
          <h1 className="m-0 text-balance text-[34px] font-[650] leading-[1.1] tracking-[-0.025em] text-ink">{title}</h1>
          {children && <div className="text-pretty text-[14px] leading-[1.6] text-ink-2">{children}</div>}
        </div>
        {right && <div className="flex shrink-0 flex-wrap items-start gap-4">{right}</div>}
      </div>
    </HalftoneWordmarkFooter>
  )
}
