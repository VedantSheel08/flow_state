"""Read-only branch audit. All outputs go to the coordinator worktree.

No patches, merges or source writes to the branch under test. The held-peak arm
is an explicit diagnostic input intervention, not a shipped scenario change.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HOME = Path(__file__).resolve().parents[1]


def quantile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[round((len(values) - 1) * fraction)]


def capacity(net, meta):
    result = {}
    for edge in net.getEdges():
        m = meta[edge.getID()]
        key = m['cls'] if m['kind'] == 'street' else m['kind']
        group = result.setdefault(key, {'laneMeters': 0, 'lanePieces': 0, 'carCapacity': {str(g): 0 for g in (2, 3, 5)}})
        for lane in edge.getLanes():
            if not lane.allows('passenger'):
                continue
            group['laneMeters'] += lane.getLength()
            group['lanePieces'] += 1
            for gap in (2, 3, 5):
                # One vehicle's gap can lie beyond the last parked vehicle.
                group['carCapacity'][str(gap)] += math.floor((lane.getLength() + gap) / (4.75 + gap))
    return result


def sample(engine, lanes, meta, types):
    T = engine.T
    rows = []
    by_lane = defaultdict(list)
    for vid, state in engine.vehicles.items():
        vt = engine.vtype.get(vid, '')
        if vt not in types:
            types[vt] = (T.vehicletype.getVehicleClass(vt), T.vehicletype.getLength(vt), T.vehicletype.getMinGap(vt))
        cls, length, gap = types[vt]
        if cls not in ('passenger', 'delivery', 'truck', 'bus', 'emergency'):
            continue
        lane = T.vehicle.getLaneID(vid)
        if lane not in lanes:
            continue  # internal junctions are not road storage
        position = T.vehicle.getLanePosition(vid)
        r = (vid, lane, position, length, gap, state[3])
        rows.append(r)
        by_lane[lane].append(r)
    def scope(selected):
        length = sum(lanes[l]['length'] for l in selected)
        vehicles = [r for r in rows if r[1] in selected]
        speeds = [r[5] * 3.6 for r in vehicles]
        return {'vehicles': len(vehicles), 'densityVehPerLaneKm': len(vehicles) * 1000 / length,
                'bodyCoverage': sum(min(r[3], max(0, r[2])) for r in vehicles) / length,
                'standstillFootprintFill': sum(r[3] + r[4] for r in vehicles) / length,
                'haltedFraction': sum(r[5] < .1 for r in vehicles) / max(1, len(vehicles)),
                'speedP10': quantile(speeds, .1), 'speedP50': quantile(speeds, .5), 'speedP90': quantile(speeds, .9)}
    arterial = {l for l, info in lanes.items() if info['class'] == 'arterial' and info['kind'] == 'street'}
    core = {l for l, info in lanes.items() if info['kind'] == 'street'}
    full_blocks = set()
    queue_rows = []
    for path in engine._audit_paths:
        halted = []
        for lid, offset in path['lanes']:
            halted.extend((r[0], r[1], r[2]+offset, *r[3:]) for r in by_lane[lid] if r[5] < .1)
        halted.sort(key=lambda r: -r[2])
        clusters = []
        for r in halted:
            if not clusters or clusters[-1][-1][2] - clusters[-1][-1][3] - r[2] > 10:
                clusters.append([r])
            else:
                clusters[-1].append(r)
        for cluster in clusters:
            if len(cluster) < 3:
                continue
            span = cluster[0][2] - max(0, cluster[-1][2] - cluster[-1][3])
            queue_rows.append({'vehicles': len(cluster), 'meters': span})
            if span >= .75 * path['length'] and cluster[-1][2] - cluster[-1][3] <= 10:
                full_blocks.add(path['block'])
    # Strictly a geometric multi-block proxy; causal receiving-lane blockage
    # during green requires the owner-led detector test in the management report.
    adjacent = []
    for block in full_blocks:
        street, direction, segment = block
        if (street, direction, segment + 1) in full_blocks:
            adjacent.append((block, (street, direction, segment + 1)))
    gaps = []
    for r in rows:
        if r[5] >= .1 or T.vehicle.getWaitingTime(r[0]) < 5:
            continue
        leader = T.vehicle.getLeader(r[0], 15)
        if leader and leader[0] in engine.vehicles and T.vehicle.getLaneID(leader[0]) == r[1] and engine.vehicles[leader[0]][3] < .1:
            # SUMO reports distance from ego front + minGap, not bumper gap.
            gaps.append(leader[1] + r[4])
    pending = len(T.simulation.getPendingVehicles())
    return {'t': engine.t, 'network': scope(set(lanes)), 'core': scope(core), 'arterial': scope(arterial),
            'pending': pending, 'maxLaneQueueMeters': max((q['meters'] for q in queue_rows), default=0),
            'maxLaneQueueVehicles': max((q['vehicles'] for q in queue_rows), default=0),
            'fullQueuedPhysicalBlocks': len(full_blocks), 'adjacentQueuedBlockPairsProxy': len(adjacent),
            'queuedBlockIDs': sorted([list(b) for b in full_blocks]),
            'standstillGapSamples': gaps}


def run(args):
    source = args.source.resolve()
    sys.path.insert(0, str(source / 'backend'))
    import libsumo
    import traci
    import sumolib
    from flowstate.scenarios.catalog import create
    from flowstate.sim.engine import Engine, RunConfig
    net_path = source / 'data/networks/district.net.xml'
    meta = json.loads(net_path.with_name('district.city.json').read_text(encoding='utf-8'))['edges']
    net = sumolib.net.readNet(str(net_path))
    lanes = {}
    for edge in net.getEdges():
        m = meta[edge.getID()]
        for lane in edge.getLanes():
            if lane.allows('passenger'):
                lanes[lane.getID()] = {'length': lane.getLength(), 'class': m['cls'], 'kind': m['kind'],
                                      'block': (m['street'], m['dir'], m['seg'])}
    cases = {'rush': {'demand': 2800, 'traffic': 1, 'peak': 2},
             'severe': {'demand': 3200, 'traffic': 1.6, 'peak': 2.5},
             'gridlock_request': {'demand': 3200, 'traffic': 1.6, 'peak': 3}}
    work = HOME / 'data/work/coordination' / f'{source.name}-{args.case}-{args.seed}-{args.profile}'
    work.mkdir(parents=True, exist_ok=True)
    sc = create('rush_hour', {'duration': 5, 'signals': 'fixed', **cases[args.case]})
    engine = Engine(sc, RunConfig('rush_hour', sc.params, args.seed, warmup=args.warmup), net_path.parent, work)
    # Build connected lane paths through the road's split main/bay edge pieces,
    # stopping at physical junctions (street/dir/seg identifies a city block).
    successors = {}
    incoming = set()
    for lid, info in lanes.items():
        if info['kind'] != 'street': continue
        targets = {c.getToLane().getID() for c in net.getLane(lid).getOutgoing()}
        successors[lid] = [l for l in sorted(targets) if l in lanes and lanes[l]['block']==info['block']]
        incoming.update(successors[lid])
    paths = []
    def walk(lid, previous, offset):
        chain = previous+[(lid,offset)]
        offset += lanes[lid]['length']
        next_lanes = [l for l in successors[lid] if l not in {p[0] for p in chain}]
        if not next_lanes:
            paths.append({'lanes':chain,'length':offset,'block':lanes[lid]['block']})
        for nxt in next_lanes: walk(nxt,chain,offset)
    for root in sorted(set(successors)-incoming): walk(root,[],0)
    engine._audit_paths = paths
    peak = sc.components[0]
    demand_time = peak.start + peak.ramp + peak.plateau / 2
    original_demand = sc.demand
    if args.profile == 'held_peak':
        sc.demand = lambda _t: original_demand(demand_time)
    originals = [(m, m.start) for m in (libsumo, traci)]
    def wrapper(original):
        def start(cmd, *a, **kw):
            cmd = list(cmd)
            for option in ('--collision.check-junctions', '--no-warnings'):
                cmd[cmd.index(option) + 1] = 'true' if option == '--collision.check-junctions' else 'false'
            if '--collision-output' in cmd:
                i = cmd.index('--collision-output'); del cmd[i:i + 2]
            cmd += ['--collision-output', str(work/'collisions.xml'), '--summary-output', str(work/'summary.xml'),
                    '--tripinfo-output', str(work/'trips.xml'), '--tripinfo-output.write-unfinished', 'true']
            return original(cmd, *a, **kw)
        return start
    samples = []
    types = {}
    counts = {p: {'departed': 0, 'arrived': 0} for p in ('warmup', 'observation')}
    invalid = set()
    start = time.perf_counter()
    try:
        for m, original in originals:
            m.start = wrapper(original)
        engine.start()
        next_sample = 0
        while not engine.finished:
            period = 'warmup' if engine.t < 0 else 'observation'
            engine.step()
            counts[period]['departed'] += engine.T.simulation.getDepartedNumber()
            counts[period]['arrived'] += engine.T.simulation.getArrivedNumber()
            if engine.t >= next_sample:
                next_sample += 1
                samples.append(sample(engine, lanes, meta, types))
                if int(engine.t) % 10 == 0:
                    for vid in engine.vehicles:
                        if not engine.T.vehicle.isRouteValid(vid): invalid.add(vid)
        results = engine.results()
        demand_diagnostics = {k: getattr(engine.demand, k, None) for k in ('counter','failed','metered','deferred','throttle')}
    finally:
        engine.close()
        for m, original in originals: m.start = original
    elapsed = time.perf_counter() - start
    summary = list(ET.parse(work/'summary.xml').getroot())[-1].attrib
    collisions = [dict(e.attrib) for e in ET.parse(work/'collisions.xml').getroot()]
    gaps = [g for s in samples for g in s.pop('standstillGapSamples')]
    rows = samples[:-1]  # t=0..299; exactly 300 1 Hz observation samples
    revision = (source/'REVISION').read_text(encoding='utf-8') if (source/'REVISION').exists() else subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    report = {'source': str(source), 'commit': revision,
              'networkSHA256': hashlib.sha256(net_path.read_bytes()).hexdigest(), 'case': args.case, 'seed': args.seed,
              'profile': args.profile, 'heldDemandScenarioTime': demand_time if args.profile=='held_peak' else None,
              'offeredRateAtPeak': original_demand(demand_time).rate * sc.density, 'params': sc.params,
              'warmup': args.warmup, 'observation': 300, 'wallSeconds': elapsed, 'capacity': capacity(net, meta),
              'counts': counts, 'demand': demand_diagnostics, 'results': results, 'nativeSummary': summary,
              'collisionRecords': collisions, 'invalidRouteIDsSampled': sorted(invalid), 'samples': samples,
              'summary': {'arterialDensityMean': statistics.mean(s['arterial']['densityVehPerLaneKm'] for s in rows),
                          'arterialFillMean': statistics.mean(s['arterial']['standstillFootprintFill'] for s in rows),
                          'arterialSpeedMedianOfSamples': statistics.median(s['arterial']['speedP50'] for s in rows if s['arterial']['speedP50'] is not None),
                          'arterialBodyCoverageMean': statistics.mean(s['arterial']['bodyCoverage'] for s in rows),
                          'arterialHaltedMean': statistics.mean(s['arterial']['haltedFraction'] for s in rows),
                          'maxQueueMeters': max(s['maxLaneQueueMeters'] for s in rows),
                          'maxQueueVehicles': max(s['maxLaneQueueVehicles'] for s in rows),
                          'adjacentQueueProxySeconds': sum(s['adjacentQueuedBlockPairsProxy']>0 for s in rows),
                          'rushDensitySeconds': sum(s['arterial']['densityVehPerLaneKm']>=40 for s in rows),
                          'severeDensitySeconds': sum(s['arterial']['densityVehPerLaneKm']>=85 for s in rows),
                          'gridlockDensitySeconds': sum(s['arterial']['densityVehPerLaneKm']>=110 for s in rows),
                          'pendingMax': max(s['pending'] for s in rows),
                          'standstillGapP05': quantile(gaps,.05), 'standstillGapP50': quantile(gaps,.5),
                          'standstillGapP95': quantile(gaps,.95), 'standstillGapSamples': len(gaps),
                          'standstillGapWithin2to5Fraction': sum(2<=g<=5 for g in gaps)/max(1,len(gaps))}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('case','seed','profile','summary','demand','wallSeconds')}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',choices=('rush','severe','gridlock_request'),required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--profile',choices=('held_peak','recipe_clock'),default='held_peak')
    parser.add_argument('--warmup',type=float,default=300)
    run(parser.parse_args())
