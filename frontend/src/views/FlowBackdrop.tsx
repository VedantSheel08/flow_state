// Soft, uneven gradient backdrop (pastel mesh + scattered white glows) that drifts slowly. Pure SVG, decorative only.
interface Blob { cx: number; cy: number; rx: number; ry: number; color: string; o: number; dx: number; dy: number; dur: number }

const COLOR_BLOBS: Blob[] = [
  { cx: 120, cy: 120, rx: 420, ry: 320, color: '#8fbaff', o: 0.75, dx: 120, dy: 70, dur: 38 },
  { cx: 1480, cy: 90, rx: 380, ry: 300, color: '#b7a6ff', o: 0.6, dx: -110, dy: 90, dur: 44 },
  { cx: 1500, cy: 620, rx: 360, ry: 420, color: '#ffb7d2', o: 0.6, dx: -90, dy: -80, dur: 41 },
  { cx: 260, cy: 900, rx: 430, ry: 300, color: '#ffcfa8', o: 0.6, dx: 110, dy: -60, dur: 47 },
  { cx: 820, cy: 1040, rx: 360, ry: 220, color: '#9ee6d2', o: 0.45, dx: 90, dy: -40, dur: 52 },
  { cx: 640, cy: 140, rx: 260, ry: 170, color: '#a8c8ff', o: 0.5, dx: -70, dy: 50, dur: 36 },
]

// white highlights at deliberately irregular places and sizes, so the surface is not even
const WHITE_BLOBS: Blob[] = [
  { cx: 430, cy: 330, rx: 340, ry: 210, color: '#ffffff', o: 0.95, dx: 80, dy: 40, dur: 33 },
  { cx: 1180, cy: 260, rx: 220, ry: 280, color: '#ffffff', o: 0.85, dx: -60, dy: 70, dur: 39 },
  { cx: 900, cy: 640, rx: 400, ry: 190, color: '#ffffff', o: 0.9, dx: 100, dy: -30, dur: 45 },
  { cx: 240, cy: 640, rx: 150, ry: 210, color: '#ffffff', o: 0.8, dx: 50, dy: 60, dur: 31 },
  { cx: 1360, cy: 860, rx: 260, ry: 150, color: '#ffffff', o: 0.85, dx: -70, dy: -40, dur: 42 },
  { cx: 640, cy: 900, rx: 120, ry: 90, color: '#ffffff', o: 0.9, dx: 40, dy: -50, dur: 28 },
  { cx: 1040, cy: 80, rx: 130, ry: 70, color: '#ffffff', o: 0.9, dx: -50, dy: 30, dur: 30 },
]

export function FlowBackdrop() {
  const still = typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const blob = (b: Blob, i: number, tag: string) => (
    <ellipse key={`${tag}${i}`} cx={b.cx} cy={b.cy} rx={b.rx} ry={b.ry} fill={b.color} opacity={b.o}>
      {!still && <animateTransform attributeName="transform" type="translate" values={`0 0; ${b.dx} ${b.dy}; 0 0`} dur={`${b.dur}s`} repeatCount="indefinite" calcMode="spline" keySplines="0.45 0 0.55 1; 0.45 0 0.55 1" keyTimes="0; 0.5; 1" />}
    </ellipse>
  )
  return (
    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden>
      <defs>
        <linearGradient id="fb-base" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#dbe9ff" /><stop offset="0.45" stopColor="#ece6ff" /><stop offset="0.75" stopColor="#ffe6ef" /><stop offset="1" stopColor="#fff0e2" />
        </linearGradient>
        <filter id="fb-soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="85" /></filter>
        <filter id="fb-soft-s" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="55" /></filter>
        <filter id="fb-grain" x="0" y="0" width="100%" height="100%">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch" />
          <feColorMatrix values="0 0 0 0 0.35  0 0 0 0 0.4  0 0 0 0 0.55  0 0 0 0.5 0" />
        </filter>
      </defs>
      <rect width="1600" height="1000" fill="url(#fb-base)" />
      <g filter="url(#fb-soft)">{COLOR_BLOBS.map((b, i) => blob(b, i, 'c'))}</g>
      <g filter="url(#fb-soft-s)">{WHITE_BLOBS.map((b, i) => blob(b, i, 'w'))}</g>
      <rect width="1600" height="1000" filter="url(#fb-grain)" opacity="0.07" />
    </svg>
  )
}
