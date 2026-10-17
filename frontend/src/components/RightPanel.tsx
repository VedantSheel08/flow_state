import { Activity, Ambulance, BusFront, Car, Copy, Crosshair, ListTree, Navigation, PanelRightClose, PanelRightOpen, ScanSearch, Siren, TrafficCone, TriangleAlert, Video, VideoOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { clock, cn, HEADING, num, secs } from '../lib/format'
import { useStore } from '../lib/store'
import type { JunctionDetail, Kpi, SimEvent, Tracked, VehicleDetail } from '../lib/types'
import { mapApi } from '../map/MapView'
import { Sparkline } from './Sparkline'
import { SlidingNumber } from './ui/traffic'
import { Badge, Button, Empty, Section, Slider, Tip } from './ui'

const TABS = [
  { id: 'overview', label: 'Overview', icon: Activity },
  { id: 'inspect', label: 'Inspector', icon: ScanSearch },
  { id: 'events', label: 'Events', icon: ListTree },
] as const

export function RightPanel() {
  const tab = useStore((s) => s.rightTab)
  const set = useStore((s) => s.set)
  const events = useStore((s) => s.events)
  const selection = useStore((s) => s.selection)
  const open = useStore((s) => s.panels.right)
  const togglePanel = useStore((s) => s.togglePanel)
  if (!open) {
    return (
      <aside className="flex w-[56px] shrink-0 flex-col items-center gap-1 border-l border-border bg-surface py-2.5" aria-label="Inspector (collapsed)">
        <Tip label="Show panel ( ] )" side="left">
          <Button size="icon" variant="ghost" aria-label="Show inspector panel" onClick={() => togglePanel('right', true)}>
            <PanelRightOpen size={16} />
          </Button>
        </Tip>
        <div className="my-1 h-px w-7 bg-border" />
        {TABS.map((t) => (
          <Tip key={t.id} label={t.label} side="left">
            <button
              aria-label={t.label}
              onClick={() => {
                set({ rightTab: t.id })
                togglePanel('right', true)
              }}
              className="relative flex h-9 w-9 items-center justify-center rounded-lg text-ink-3 transition-colors hover:bg-muted hover:text-ink"
            >
              <t.icon size={16} strokeWidth={1.8} />
              {t.id === 'inspect' && selection && <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-accent ring-2 ring-surface" />}
              {t.id === 'events' && events.length > 0 && (
                <span className="tnum absolute -right-0.5 -top-0.5 min-w-[16px] rounded-full bg-ink px-1 text-center text-[9.5px] font-semibold leading-[16px] text-white">{events.length > 99 ? '99+' : events.length}</span>
              )}
            </button>
          </Tip>
        ))}
      </aside>
    )
  }
  return (
    <aside className="anim-fade flex w-[340px] shrink-0 flex-col border-l border-border bg-surface">
      <div className="flex h-11 shrink-0 items-end gap-4 border-b border-border pl-4 pr-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => set({ rightTab: t.id })}
            className={cn('relative flex h-full items-center gap-1.5 text-[12.5px] font-medium', tab === t.id ? 'text-ink after:absolute after:inset-x-0 after:-bottom-px after:h-[2px] after:bg-ink' : 'text-ink-3 hover:text-ink')}
          >
            {t.label}
            {t.id === 'events' && events.length > 0 && <span className="tnum rounded bg-muted px-1 text-[10.5px] text-ink-2">{events.length}</span>}
            {t.id === 'inspect' && selection && <span className="h-1.5 w-1.5 rounded-full bg-accent" />}
          </button>
        ))}
        <div className="ml-auto flex h-full items-center">
          <Tip label="Collapse panel ( ] )" side="left">
            <Button size="icon-sm" variant="ghost" aria-label="Collapse inspector panel" onClick={() => togglePanel('right', false)}>
              <PanelRightClose size={15} />
            </Button>
          </Tip>
        </div>
      </div>
      <div className="scroll-y min-h-0 flex-1">
        {tab === 'overview' && <Overview />}
        {tab === 'inspect' && <Inspector />}
        {tab === 'events' && <EventList />}
      </div>
    </aside>
  )
}

// ---------------------------------------------------------------------------
function Metric({ label, value, unit, series, color, hint }: { label: string; value: string; unit?: string; series: (number | null)[]; color?: string; hint?: string }) {
  return (
    <div className="flex items-end justify-between gap-2 py-2" title={hint}>
      <div className="min-w-0">
        <div className="truncate text-[11.5px] text-ink-3">{label}</div>
        <div className="tnum text-[17px] font-semibold leading-tight tracking-[-0.01em] text-ink">
          <SlidingNumber value={value} />
          {unit && <span className="ml-1 text-[11.5px] font-normal text-ink-3">{unit}</span>}
        </div>
      </div>
      <Sparkline values={series} color={color} width={48} />
    </div>
  )
}

function Overview() {
  const run = useStore((s) => s.run)
  const stats = useStore((s) => s.stats)
  const history = useStore((s) => s.history)
  const status = useStore((s) => s.status)
  const toast = useStore((s) => s.toast)
  const k: Kpi | undefined = stats?.kpi
  const tail = history.slice(-180)
  const ser = (f: (h: Kpi) => number | null) => tail.map(f)
  if (!run) {
    return (
      <Empty title="No simulation running">
        Choose a scenario on the left and press <span className="font-medium text-ink-2">Generate &amp; Run</span>. SUMO generates a fresh, seeded variation of it.
      </Empty>
    )
  }
  const tracked = (stats?.tracked ?? []).filter((t) => t.kind !== 'bus')
  const buses = (stats?.tracked ?? []).filter((t) => t.kind === 'bus')
  const incidents = stats?.incidents ?? []
  const scen = stats?.scenario ?? {}
  return (
    <div className="anim-in">
      <Section
        title="Run"
        right={
          <Badge tone={status.state === 'running' ? (status.paused ? 'warn' : 'good') : status.state === 'finished' ? 'neutral' : status.state === 'error' ? 'bad' : 'accent'}>
            {status.state === 'running' ? (status.paused ? 'Paused' : 'Running') : status.state === 'warming' ? 'Warm-up' : status.state === 'finished' ? 'Complete' : status.state === 'error' ? 'Error' : 'Starting'}
          </Badge>
        }
      >
        <div className="text-[15px] font-semibold text-ink">{run.title}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-2">
          <span className="flex items-center gap-1">
            Seed <span className="tnum font-medium text-ink">{run.seed}</span>
            <Tip label="Copy seed">
              <button
                className="text-ink-3 hover:text-ink"
                aria-label="Copy seed"
                onClick={() => {
                  navigator.clipboard?.writeText(String(run.seed))
                  useStore.getState().set({ seed: String(run.seed) })
                  toast('info', `Seed ${run.seed} copied to the seed field`)
                }}
              >
                <Copy size={12} />
              </button>
            </Tip>
          </span>
          <span>{run.signalMode === 'actuated' ? 'Actuated signals' : 'Fixed-time signals'}</span>
          <span>{run.duration ? `${run.duration / 60} min` : ''}</span>
        </div>
        {run.congestionPreset && (
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <Badge tone={run.congestionPreset.stressTest ? 'bad' : 'neutral'}>{run.congestionPreset.label}</Badge>
            <Badge>
              {num(Number(run.params.demand))}/h × {Number(run.params.traffic ?? 1).toFixed(2)}
            </Badge>
            {run.warmup != null && <Badge>Warm-up {run.warmup} s</Badge>}
            {!run.congestionPreset.calibrated && <Badge tone="warn">Not calibrated</Badge>}
          </div>
        )}
        {status.error && <div className="mt-2 rounded-md bg-bad-soft px-2.5 py-2 text-[12px] text-bad">{status.error}</div>}
        {run.briefing.length > 0 && (
          <dl className="mt-3 space-y-2 rounded-md border border-border bg-subtle px-3 py-2.5">
            {run.briefing.map((b, i) => (
              <div key={i}>
                <dt className="text-[11px] text-ink-3">{b.label}</dt>
                <dd className="text-[12.5px] font-medium leading-snug text-ink">{b.value}</dd>
                {b.detail && <dd className="text-[11.5px] leading-snug text-ink-2">{b.detail}</dd>}
              </div>
            ))}
          </dl>
        )}
      </Section>

      <Section title="Network performance" right={<span className="text-[11px] text-ink-3">trailing 5 min</span>}>
        <div className="grid grid-cols-2 gap-x-5 divide-y-0">
          <Metric label="In network" value={num(k?.active)} series={ser((h) => h.active)} hint="Vehicles currently driving in SUMO (excluding pedestrians)" />
          <Metric label="Mean speed" value={num(k?.meanSpeed, 1)} unit="km/h" series={ser((h) => h.meanSpeed)} hint="Space-mean speed of motor vehicles now in the network" />
          <Metric label="Delay / trip" value={num(k?.timeLoss, 0)} unit="s" series={ser((h) => h.timeLoss)} hint="SUMO time loss of trips completed in the last 5 minutes" />
          <Metric label="Travel time" value={num(k?.travelTime, 0)} unit="s" series={ser((h) => h.travelTime)} hint="Mean trip duration, trips completed in the last 5 minutes" />
          <Metric label="Throughput" value={num(k?.throughput)} unit="veh/h" series={ser((h) => h.throughput)} hint="Completed trips per hour over the last 5 minutes" />
          <Metric label="Queued" value={num(k?.queued)} unit="veh" series={ser((h) => h.queued)} hint="Halting vehicles on intersection approaches" />
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2 border-t border-border pt-2.5 text-[11.5px]">
          <div>
            <div className="text-ink-3">Completed</div>
            <div className="tnum font-medium text-ink">{num(k?.completed)}</div>
          </div>
          <div>
            <div className="text-ink-3">Pedestrians</div>
            <div className="tnum font-medium text-ink">{num(k?.persons)}</div>
          </div>
          <div>
            <Tip label="Vehicles waiting to enter the network (demand above entry capacity) · SUMO teleports for gridlock">
              <div className="text-ink-3">Waiting / tele.</div>
            </Tip>
            <div className="tnum font-medium text-ink">
              {num(k?.pending)} / <span className={k?.teleports ? 'text-warn' : ''}>{num(k?.teleports)}</span>
            </div>
          </div>
        </div>
      </Section>

      {(tracked.length > 0 || incidents.length > 0) && (
        <Section title="Operations">
          <div className="space-y-1.5">
            {incidents.map((inc) => (
              <button
                key={inc.id}
                onClick={() => mapApi.current?.focus(inc.x, inc.y, 7)}
                className="flex w-full items-start gap-2.5 rounded-md border border-border px-2.5 py-2 text-left hover:bg-subtle"
              >
                <span className={cn('mt-0.5', inc.kind === 'accident' ? 'text-bad' : 'text-warn')}>{inc.kind === 'accident' ? <TriangleAlert size={14} /> : <TrafficCone size={14} />}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] font-medium text-ink">{inc.description}</span>
                  <span className="block text-[11.5px] text-ink-3">
                    {inc.cleared ? `Cleared at ${clock(inc.tEnd)}` : inc.tEnd != null ? `Since ${clock(inc.tStart)} · clears ${clock(inc.tEnd)}` : `Since ${clock(Math.max(0, inc.tStart))}`}
                  </span>
                </span>
              </button>
            ))}
            {tracked.map((tr) => (
              <TrackedRow key={tr.id} tr={tr} />
            ))}
          </div>
        </Section>
      )}

      {buses.length > 0 && (
        <Section title="Transit" right={<span className="text-[11px] text-ink-3">{buses.filter((b) => b.status === 'completed').length}/{buses.length} trips complete</span>}>
          <div className="grid grid-cols-3 gap-2 text-[11.5px]">
            <div>
              <div className="text-ink-3">Mean delay</div>
              <div className="tnum text-[14px] font-semibold text-ink">{secs(scen.busMeanDelay as number | null)}</div>
            </div>
            <div>
              <div className="text-ink-3">Green ext.</div>
              <div className="tnum text-[14px] font-semibold text-ink">{num(stats?.signals.tspExtensions)}</div>
            </div>
            <div>
              <div className="text-ink-3">Extra green</div>
              <div className="tnum text-[14px] font-semibold text-ink">{num(stats?.signals.tspSeconds)} s</div>
            </div>
          </div>
          <div className="mt-2.5 space-y-1">
            {buses.slice(-6).map((b) => (
              <TrackedRow key={b.id} tr={b} />
            ))}
          </div>
        </Section>
      )}

      {typeof scen.corridor === 'object' && scen.corridor && (
        <Section title="King Street corridor">
          <div className="grid grid-cols-2 gap-2 text-[11.5px]">
            <div>
              <div className="text-ink-3">Car speed</div>
              <div className="tnum text-[14px] font-semibold text-ink">{num((scen.corridor as Record<string, number>).carSpeed, 1)} km/h</div>
            </div>
            <div>
              <div className="text-ink-3">Cyclist speed</div>
              <div className="tnum text-[14px] font-semibold text-ink">{num((scen.corridor as Record<string, number>).bikeSpeed, 1)} km/h</div>
            </div>
          </div>
        </Section>
      )}

      <LiveControls />

      <Section title="Signals">
        <div className="grid grid-cols-2 gap-2 text-[11.5px]">
          <div>
            <div className="text-ink-3">Control</div>
            <div className="font-medium text-ink">{stats?.signals.mode === 'actuated' ? 'Actuated' : 'Fixed-time'}</div>
          </div>
          <div>
            <div className="text-ink-3">Emergency pre-emptions</div>
            <div className="tnum font-medium text-ink">{num(stats?.signals.preemptions ?? 0)}</div>
          </div>
        </div>
      </Section>
    </div>
  )
}

function TrackedRow({ tr }: { tr: Tracked }) {
  const select = useStore((s) => s.select)
  const set = useStore((s) => s.set)
  const Icon = tr.kind === 'ambulance' ? Ambulance : tr.kind === 'police' || tr.kind === 'responder' ? Siren : tr.kind === 'bus' ? BusFront : Car
  const resp = tr.responseTime as number | undefined
  const delay = tr.delay as number | undefined
  return (
    <button
      onClick={() => {
        select({ kind: 'vehicle', id: tr.id })
        set({ follow: true })
      }}
      className="flex w-full items-center gap-2.5 rounded-md border border-border px-2.5 py-2 text-left hover:bg-subtle"
    >
      <span className={cn(tr.kind === 'suspect' ? 'text-bad' : tr.kind === 'bus' ? 'text-[#7A5AF8]' : 'text-accent')}>
        <Icon size={14} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] font-medium text-ink">{tr.label}</span>
        <span className="block text-[11.5px] capitalize text-ink-3">
          {tr.status}
          {resp != null && ` · response ${secs(resp)}${tr.freeFlow ? ` (free-flow ${secs(tr.freeFlow as number)})` : ''}`}
          {delay != null && ` · ${delay >= 0 ? '+' : ''}${secs(delay)} vs. timetable`}
        </span>
      </span>
      <Navigation size={12} className="text-ink-3" />
    </button>
  )
}

function LiveControls() {
  const run = useStore((s) => s.run)
  const status = useStore((s) => s.status)
  const toast = useStore((s) => s.toast)
  const [traffic, setTraffic] = useState<number>((run?.params.traffic as number) ?? 1)
  useEffect(() => setTraffic((run?.params.traffic as number) ?? 1), [run?.id, run?.params.traffic])
  if (!run || status.state === 'finished') return null
  return (
    <Section title="Live control">
      <div className="space-y-1.5">
        <div className="flex items-baseline justify-between">
          <span className="text-[12.5px] font-medium text-ink">Traffic level</span>
          <span className="tnum text-[12px] text-ink-2">{traffic.toFixed(2)}×</span>
        </div>
        <Slider
          value={traffic}
          min={0.4}
          max={1.6}
          step={0.05}
          onChange={setTraffic}
          onCommit={(v) =>
            api
              .liveParams({ traffic: v })
              .then(() => toast('info', `Background demand set to ${v.toFixed(2)}× from now on`))
              .catch((e) => toast('error', String(e.message ?? e)))
          }
        />
        <p className="text-[11px] text-ink-3">Applies to new trips from the current simulated time. Recorded in the run as a live change.</p>
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------
function Inspector() {
  const selection = useStore((s) => s.selection)
  if (!selection || selection.kind === 'edge')
    return <Empty title="Nothing selected">Click a vehicle to see its SUMO state and driver profile, or an intersection to see its signal phases and approach queues.</Empty>
  return selection.kind === 'vehicle' ? <VehicleInspector id={selection.id} /> : <JunctionInspector id={selection.id} />
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px] text-[12px]">
      <span className="text-ink-3">{k}</span>
      <span className="tnum text-right font-medium text-ink">{v}</span>
    </div>
  )
}

const TYPE_NAMES: Record<string, string> = {
  sedan: 'Sedan', hatch: 'Hatchback', suv: 'SUV', pickup: 'Pickup truck', van: 'Delivery van', truck: 'Box truck', bus: 'City bus',
  bike: 'Bicycle', ambulance: 'Ambulance', police: 'Police cruiser', suspect: 'Suspect vehicle', wreck: 'Crashed vehicle',
}

function VehicleInspector({ id }: { id: string }) {
  const [d, setD] = useState<VehicleDetail | null>(null)
  const [gone, setGone] = useState(false)
  const follow = useStore((s) => s.follow)
  const set = useStore((s) => s.set)
  useEffect(() => {
    let alive = true
    setGone(false)
    const load = () =>
      api
        .vehicle(id)
        .then((r) => alive && (setD(r), setGone(false)))
        .catch(() => alive && setGone(true))
    load()
    const iv = setInterval(load, 1000)
    return () => {
      alive = false
      clearInterval(iv)
    }
  }, [id])
  if (!d) return <Empty title={gone ? 'Vehicle has left the network' : 'Loading…'}>{gone ? 'It reached its destination or was removed by the scenario.' : null}</Empty>
  const sig = d.nextSignal
  return (
    <div className="anim-in">
      <Section
        title="Vehicle"
        right={
          <div className="flex items-center gap-1">
            <Tip label={follow ? 'Stop following' : 'Follow with camera'}>
              <Button size="icon-sm" variant={follow ? 'primary' : 'secondary'} aria-label="Follow vehicle" onClick={() => set({ follow: !follow })}>
                {follow ? <VideoOff size={13} /> : <Video size={13} />}
              </Button>
            </Tip>
          </div>
        }
      >
        <div className="flex items-baseline justify-between">
          <div className="text-[15px] font-semibold text-ink">{d.tracked?.label ?? TYPE_NAMES[d.type] ?? d.type}</div>
          <span className="tnum text-[11.5px] text-ink-3">{d.id}</span>
        </div>
        {gone && <div className="mt-1 text-[11.5px] text-warn">Left the network — showing last known state.</div>}
        <div className="mt-3 grid grid-cols-3 gap-2">
          <div>
            <div className="text-[11px] text-ink-3">Speed</div>
            <div className="tnum text-[17px] font-semibold text-ink">
              {num(d.speed, 0)}
              <span className="ml-0.5 text-[11px] font-normal text-ink-3">km/h</span>
            </div>
          </div>
          <div>
            <div className="text-[11px] text-ink-3">Accel.</div>
            <div className={cn('tnum text-[17px] font-semibold', d.accel < -0.5 ? 'text-bad' : 'text-ink')}>
              {d.accel > 0 ? '+' : ''}
              {num(d.accel, 1)}
              <span className="ml-0.5 text-[11px] font-normal text-ink-3">m/s²</span>
            </div>
          </div>
          <div>
            <div className="text-[11px] text-ink-3">Limit</div>
            <div className="tnum text-[17px] font-semibold text-ink">
              {num(d.allowedSpeed, 0)}
              <span className="ml-0.5 text-[11px] font-normal text-ink-3">km/h</span>
            </div>
          </div>
        </div>
      </Section>
      <Section title="Position">
        <Row k="Road" v={<span className="font-medium">{d.roadName}</span>} />
        <Row k="Lane index" v={d.lane} />
        <Row k="Next signal" v={sig ? `${sig.junction} · ${num(sig.distance)} m · ${({ G: 'green', g: 'green', y: 'amber', r: 'red' } as Record<string, string>)[sig.state] ?? sig.state}` : '—'} />
        <Row k="Leader gap" v={d.leader ? `${num(d.leader.gap, 1)} m` : 'free road'} />
        <Row k="Destination" v={<span className="block max-w-[190px] truncate">{d.destination ?? '—'}</span>} />
        <Row k="Remaining route" v={`${d.route.length} edges`} />
      </Section>
      <Section title="Trip so far">
        <Row k="Departed" v={clock(d.departed)} />
        <Row k="Distance" v={`${num(d.travelled)} m`} />
        <Row k="Time loss" v={secs(d.timeLoss)} />
        <Row k="Waiting (stopped)" v={secs(d.waiting)} />
      </Section>
      <Section title="Driver profile (IDM)">
        <Row k="Desired speed factor" v={`${num(d.speedFactor, 2)} × limit`} />
        <Row k="Time headway τ" v={`${num(d.tau, 2)} s`} />
        <Row k="Max. acceleration" v={`${num(d.maxAccel, 2)} m/s²`} />
        <Row k="Comfort deceleration" v={`${num(d.decel, 2)} m/s²`} />
        <Row k="Standstill gap" v={`${num(d.minGap, 2)} m`} />
        <div className="mt-1 text-[11px] text-ink-3">Profile {d.profile} · drawn from bounded distributions with the run seed.</div>
      </Section>
    </div>
  )
}

const PHASE_COLOR: Record<string, string> = { G: '#16A34A', g: '#86EFAC', y: '#F5B700', Y: '#F5B700', r: '#E5E7EB', R: '#E5E7EB', u: '#F5B700', o: '#D1D5DB', O: '#D1D5DB' }

function JunctionInspector({ id }: { id: string }) {
  const [d, setD] = useState<JunctionDetail | null>(null)
  const networks = useStore((s) => s.networks)
  const run = useStore((s) => s.run)
  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .junction(id)
        .then((r) => alive && setD(r))
        .catch(() => alive && setD(null))
    load()
    const iv = setInterval(load, 1000)
    return () => {
      alive = false
      clearInterval(iv)
    }
  }, [id, run?.id])
  const geo = networks[run?.network ?? 'district']?.junctions.find((j) => j.id === id)
  const phases = d?.signal?.phases ?? []
  const cycle = phases.reduce((a, p) => a + p.duration, 0)
  return (
    <div className="anim-in">
      <Section
        title="Intersection"
        right={
          geo && (
            <Tip label="Focus on map">
              <Button size="icon-sm" variant="secondary" aria-label="Focus intersection" onClick={() => useStore.getState().focusOn(id)}>
                <Crosshair size={13} />
              </Button>
            </Tip>
          )
        }
      >
        <div className="text-[15px] font-semibold text-ink">{geo?.name ?? d?.name ?? id}</div>
        <div className="mt-0.5 text-[12px] text-ink-2">
          {d?.control === 'traffic_light' ? 'Signalised' : d?.control === 'allway_stop' ? 'All-way stop' : d?.control === 'priority_stop' ? 'Two-way stop' : (d?.control ?? '')}
          {d?.signal && ` · ${d.signal.program === 'actuated' ? 'actuated' : 'fixed-time'} · ${num(cycle)} s nominal cycle`}
        </div>
        {!d && <div className="mt-2 text-[12px] text-ink-3">Live details appear while a simulation is running.</div>}
      </Section>
      {d?.signal && (
        <Section title="Signal timing" right={<span className="tnum text-[11.5px] text-ink-2">next change in {num(Math.max(0, d.signal.nextSwitch), 0)} s</span>}>
          <div className="flex h-6 overflow-hidden rounded border border-border">
            {phases.map((p, i) => {
              const dominant = p.state.includes('y') ? 'y' : p.state.includes('G') ? 'G' : p.state.includes('g') ? 'g' : 'r'
              return (
                <Tip key={i} label={`Phase ${i + 1} · ${p.duration} s · ${p.state}`} side="top">
                  <div
                    className={cn('relative h-full border-r border-white/70 last:border-r-0', i === d.signal!.phase && 'ring-2 ring-inset ring-ink')}
                    style={{ width: `${(p.duration / cycle) * 100}%`, background: PHASE_COLOR[dominant] }}
                  />
                </Tip>
              )
            })}
          </div>
          <div className="mt-1.5 text-[11px] text-ink-3">
            Phase {d.signal.phase + 1} of {phases.length}
            {d.signal.heldBy && <span className="ml-1 font-medium text-accent">· pre-empted for {d.signal.heldBy}</span>}
          </div>
        </Section>
      )}
      {d && (
        <Section title="Approaches">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[11px] text-ink-3">
                <th className="pb-1 font-normal">Approach</th>
                <th className="pb-1 text-right font-normal">Veh.</th>
                <th className="pb-1 text-right font-normal">Stopped</th>
                <th className="pb-1 text-right font-normal">km/h</th>
              </tr>
            </thead>
            <tbody>
              {d.approaches.map((a) => (
                <tr key={a.edge} className="border-t border-border">
                  <td className="py-1.5">
                    <div className="font-medium text-ink">{a.name}</div>
                    <div className="text-[11px] text-ink-3">{HEADING[a.heading]}</div>
                  </td>
                  <td className="tnum text-right">{a.vehicles}</td>
                  <td className={cn('tnum text-right font-medium', a.queue >= 8 ? 'text-bad' : a.queue >= 4 ? 'text-warn' : 'text-ink')}>{a.queue}</td>
                  <td className="tnum text-right text-ink-2">{a.speed == null ? '—' : num(a.speed, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 text-[11px] text-ink-3">Peak queue this run: {d.maxQueue} vehicles</div>
        </Section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
const CAT_TONE: Record<string, string> = { incident: 'text-bad', emergency: 'text-accent', traffic: 'text-warn', transit: 'text-[#7A5AF8]', scenario: 'text-ink-2', system: 'text-ink-3' }

function EventList() {
  const events = useStore((s) => s.events)
  const select = useStore((s) => s.select)
  const set = useStore((s) => s.set)
  const networks = useStore((s) => s.networks)
  const run = useStore((s) => s.run)
  if (!events.length) return <Empty title="No events yet">Incidents, dispatches, signal pre-emptions and measured congestion appear here as SUMO reports them.</Empty>
  const net = networks[run?.network ?? 'district']
  const onClick = (e: SimEvent) => {
    if (e.vehicle) {
      select({ kind: 'vehicle', id: e.vehicle })
      set({ follow: true })
    } else if (e.tls) {
      select({ kind: 'junction', id: e.tls })
      const j = net?.junctions.find((q) => q.id === e.tls)
      if (j) useStore.getState().focusOn(j.id)
    }
  }
  return (
    <ul className="divide-y divide-border">
      {[...events].reverse().map((e) => (
        <li key={e.id}>
          <button onClick={() => onClick(e)} className="flex w-full gap-3 px-4 py-2.5 text-left hover:bg-subtle">
            <span className="tnum w-10 shrink-0 pt-px text-[11.5px] text-ink-3">{clock(e.t)}</span>
            <span className="min-w-0">
              <span className={cn('block text-[10.5px] font-semibold uppercase tracking-wide', CAT_TONE[e.category] ?? 'text-ink-2')}>{e.category}</span>
              <span className={cn('block text-[12.5px] leading-snug', e.severity === 'critical' ? 'font-medium text-ink' : 'text-ink')}>{e.message}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
