// Quiet backdrop: a very light pastel gradient, soft glows that drift slowly, faint curved streets with a few light-trail cars.
// Built from CSS gradients and composited transforms (no SVG filters), so it costs almost nothing per frame. Decorative only.
const GREEN = '#34d399', AMBER = '#fbbf24', ROSE = '#fb7185'

const ROADS = [
  'M-120 840 C 240 700, 440 1000, 790 810 S 1270 570, 1720 660',
  'M-120 190 C 290 330, 520 10, 930 180 S 1370 440, 1720 140',
  'M1270 -120 C 1170 260, 1520 420, 1350 720 S 1090 970, 1270 1120',
  'M-120 520 C 210 410, 390 570, 650 470 S 1010 290, 1190 410',
]
const CARS: { road: number; color: string; dur: number; delay: number }[] = [
  { road: 0, color: GREEN, dur: 26, delay: 0 }, { road: 1, color: AMBER, dur: 30, delay: -6 }, { road: 1, color: GREEN, dur: 38, delay: -22 },
  { road: 2, color: ROSE, dur: 28, delay: -10 }, { road: 3, color: AMBER, dur: 32, delay: -4 },
]
// soft colour and white glows at irregular places; each drifts on its own slow loop
const GLOWS: { x: number; y: number; w: number; h: number; color: string; o: number; dx: number; dy: number; dur: number }[] = [
  { x: -8, y: -12, w: 50, h: 60, color: '#cfe0ff', o: 0.5, dx: 60, dy: 40, dur: 38 },
  { x: 68, y: -16, w: 48, h: 56, color: '#e0d8ff', o: 0.45, dx: -60, dy: 50, dur: 44 },
  { x: 70, y: 40, w: 44, h: 80, color: '#ffdbe8', o: 0.4, dx: -50, dy: -40, dur: 41 },
  { x: -10, y: 62, w: 54, h: 56, color: '#ffe6d2', o: 0.42, dx: 60, dy: -30, dur: 47 },
  { x: 14, y: 20, w: 44, h: 40, color: '#ffffff', o: 0.95, dx: 50, dy: 25, dur: 33 },
  { x: 60, y: 6, w: 28, h: 54, color: '#ffffff', o: 0.85, dx: -40, dy: 40, dur: 39 },
  { x: 34, y: 54, w: 48, h: 34, color: '#ffffff', o: 0.9, dx: 55, dy: -20, dur: 45 },
  { x: 72, y: 76, w: 32, h: 26, color: '#ffffff', o: 0.85, dx: -40, dy: -20, dur: 42 },
]

export function FlowBackdrop() {
  const still = typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  return (
    <div className="absolute inset-0 overflow-hidden bg-[linear-gradient(135deg,#ffffff_0%,#f6f8fd_55%,#f3f5fb_100%)]" aria-hidden>
      <style>{GLOWS.map((g, i) => `@keyframes fb-drift-${i}{50%{transform:translate3d(${g.dx}px,${g.dy}px,0)}}`).join('')}</style>
      {GLOWS.map((g, i) => (
        <div key={i} className="absolute rounded-full will-change-transform"
          style={{ left: `${g.x}%`, top: `${g.y}%`, width: `${g.w}%`, height: `${g.h}%`, opacity: g.o, background: `radial-gradient(closest-side, ${g.color}, transparent)`,
            animation: still ? undefined : `fb-drift-${i} ${g.dur}s ease-in-out infinite` }} />
      ))}
      <svg className="absolute inset-0 h-full w-full opacity-80" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice">
        <defs>
          {[GREEN, AMBER, ROSE].map((c) => (
            <linearGradient key={c} id={`fb-trail-${c.slice(1)}`} x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor={c} stopOpacity="0" /><stop offset="1" stopColor={c} stopOpacity="0.8" /></linearGradient>
          ))}
        </defs>
        {ROADS.map((d, i) => (
          <g key={i} fill="none" strokeLinecap="round">
            <path d={d} stroke="#ffffff" strokeOpacity="0.7" strokeWidth="60" />
            <path d={d} stroke="#dfe6f1" strokeOpacity="0.6" strokeWidth="44" />
            <path d={d} stroke="#ffffff" strokeOpacity="0.9" strokeWidth="2" strokeDasharray="16 24" />
          </g>
        ))}
        {CARS.map((c, i) => {
          const id = `fb-trail-${c.color.slice(1)}`
          const car = (
            <g opacity="0.75">
              <rect x="-92" y="-4" width="92" height="8" rx="4" fill={`url(#${id})`} opacity="0.35" />
              <rect x="-92" y="-1.4" width="92" height="2.8" rx="1.4" fill={`url(#${id})`} />
              <rect x="-10" y="-5" width="20" height="10" rx="3.5" fill="#ffffff" stroke={c.color} strokeOpacity="0.5" strokeWidth="1.1" />
            </g>
          )
          return still
            ? <g key={i} transform={`translate(${200 + i * 260} ${540 - (i % 3) * 120})`}>{car}</g>
            : <g key={i}>{car}<animateMotion path={ROADS[c.road]} dur={`${c.dur}s`} begin={`${c.delay}s`} repeatCount="indefinite" rotate="auto" /></g>
        })}
      </svg>
    </div>
  )
}
