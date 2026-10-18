// Quiet backdrop: a very light pastel gradient, a few soft white glows, and faint curved streets with small light-trail cars.
// The streets are drawn twice (sharp and blurred) and a soft random mask decides where each shows, so the surface is blurred in patches. Pure SVG, decorative only.
const GREEN = '#34d399', AMBER = '#fbbf24', ROSE = '#fb7185'

const ROADS = [
  'M-120 840 C 240 700, 440 1000, 790 810 S 1270 570, 1720 660',
  'M-120 190 C 290 330, 520 10, 930 180 S 1370 440, 1720 140',
  'M1270 -120 C 1170 260, 1520 420, 1350 720 S 1090 970, 1270 1120',
  'M-120 520 C 210 410, 390 570, 650 470 S 1010 290, 1190 410',
]

const CARS: { road: number; color: string; dur: number; delay: number }[] = [
  { road: 0, color: GREEN, dur: 26, delay: 0 }, { road: 0, color: ROSE, dur: 34, delay: -15 },
  { road: 1, color: AMBER, dur: 30, delay: -6 }, { road: 1, color: GREEN, dur: 38, delay: -22 },
  { road: 2, color: ROSE, dur: 28, delay: -10 }, { road: 2, color: GREEN, dur: 36, delay: -26 },
  { road: 3, color: AMBER, dur: 32, delay: -4 },
]

// where the streets are crisp (white in the mask) – irregular on purpose
const CRISP: [number, number, number, number][] = [[1240, 260, 260, 200], [330, 760, 300, 150], [820, 130, 190, 110], [980, 880, 170, 120]]

const SOFT: { cx: number; cy: number; rx: number; ry: number; color: string; o: number; dx: number; dy: number; dur: number }[] = [
  { cx: 140, cy: 140, rx: 420, ry: 300, color: '#cfe0ff', o: 0.42, dx: 110, dy: 60, dur: 38 },
  { cx: 1480, cy: 100, rx: 380, ry: 280, color: '#e0d8ff', o: 0.4, dx: -100, dy: 80, dur: 44 },
  { cx: 1500, cy: 640, rx: 340, ry: 400, color: '#ffdbe8', o: 0.36, dx: -80, dy: -70, dur: 41 },
  { cx: 260, cy: 900, rx: 420, ry: 280, color: '#ffe6d2', o: 0.38, dx: 100, dy: -50, dur: 47 },
  { cx: 520, cy: 380, rx: 340, ry: 200, color: '#ffffff', o: 0.9, dx: 80, dy: 40, dur: 33 },
  { cx: 1150, cy: 330, rx: 210, ry: 270, color: '#ffffff', o: 0.8, dx: -60, dy: 60, dur: 39 },
  { cx: 880, cy: 700, rx: 380, ry: 170, color: '#ffffff', o: 0.85, dx: 90, dy: -30, dur: 45 },
  { cx: 1380, cy: 880, rx: 240, ry: 130, color: '#ffffff', o: 0.8, dx: -60, dy: -30, dur: 42 },
]

function Street({ still }: { still: boolean }) {
  return (
    <>
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
            <rect x="-92" y="-3" width="92" height="6" rx="3" fill={`url(#${id})`} filter="url(#fb-glow)" opacity="0.8" />
            <rect x="-92" y="-1.4" width="92" height="2.8" rx="1.4" fill={`url(#${id})`} />
            <rect x="-10" y="-5" width="20" height="10" rx="3.5" fill="#ffffff" stroke={c.color} strokeOpacity="0.5" strokeWidth="1.1" />
          </g>
        )
        return still
          ? <g key={i} transform={`translate(${200 + i * 190} ${540 - (i % 3) * 120})`}>{car}</g>
          : <g key={i}>{car}<animateMotion path={ROADS[c.road]} dur={`${c.dur}s`} begin={`${c.delay}s`} repeatCount="indefinite" rotate="auto" /></g>
      })}
    </>
  )
}

export function FlowBackdrop() {
  const still = typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  return (
    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden>
      <defs>
        <linearGradient id="fb-base" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#ffffff" /><stop offset="0.55" stopColor="#f6f8fd" /><stop offset="1" stopColor="#f3f5fb" /></linearGradient>
        <filter id="fb-soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="80" /></filter>
        <filter id="fb-glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="4" /></filter>
        <filter id="fb-street-blur" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="13" /></filter>
        <filter id="fb-mask-blur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="60" /></filter>
        {[GREEN, AMBER, ROSE].map((c) => (
          <linearGradient key={c} id={`fb-trail-${c.slice(1)}`} x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor={c} stopOpacity="0" /><stop offset="1" stopColor={c} stopOpacity="0.85" /></linearGradient>
        ))}
        <mask id="fb-crisp" maskUnits="userSpaceOnUse" x="0" y="0" width="1600" height="1000">
          <rect width="1600" height="1000" fill="#000" />
          <g filter="url(#fb-mask-blur)" fill="#fff">{CRISP.map(([cx, cy, rx, ry], i) => <ellipse key={i} cx={cx} cy={cy} rx={rx} ry={ry} />)}</g>
        </mask>
        <mask id="fb-hazy" maskUnits="userSpaceOnUse" x="0" y="0" width="1600" height="1000">
          <rect width="1600" height="1000" fill="#fff" />
          <g filter="url(#fb-mask-blur)" fill="#000">{CRISP.map(([cx, cy, rx, ry], i) => <ellipse key={i} cx={cx} cy={cy} rx={rx} ry={ry} />)}</g>
        </mask>
      </defs>
      <rect width="1600" height="1000" fill="url(#fb-base)" />
      <g filter="url(#fb-soft)">
        {SOFT.map((b, i) => (
          <ellipse key={i} cx={b.cx} cy={b.cy} rx={b.rx} ry={b.ry} fill={b.color} opacity={b.o}>
            {!still && <animateTransform attributeName="transform" type="translate" values={`0 0; ${b.dx} ${b.dy}; 0 0`} dur={`${b.dur}s`} repeatCount="indefinite" calcMode="spline" keySplines="0.45 0 0.55 1; 0.45 0 0.55 1" keyTimes="0; 0.5; 1" />}
          </ellipse>
        ))}
      </g>
      <g opacity="0.8">
        <g mask="url(#fb-hazy)" filter="url(#fb-street-blur)"><Street still={still} /></g>
        <g mask="url(#fb-crisp)"><Street still={still} /></g>
      </g>
    </svg>
  )
}
