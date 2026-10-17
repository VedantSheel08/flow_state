"""Reproducible unpaced SUMO benchmark, including independent safety outputs.

Run from backend: python -m flowstate.performance.benchmark --output ../reports/baseline.json
Timings include instrumentation overhead. No client, frame or JSON work is in engineStepSeconds.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import time
import xml.etree.ElementTree as ET

from .network_cache import load_network as load_net_info
from ..scenarios.catalog import create
from ..sim import engine as module
from .resources import sample
import traci.constants as tc

ROOT = Path(__file__).resolve().parents[3]
WORKLOADS = {
    "light": {"demand": 600, "traffic": 1.0, "peak": 1.2},
    "normal": {"demand": 1600, "traffic": 1.0, "peak": 1.2},
    "rush_hour": {"demand": 2800, "traffic": 1.0, "peak": 2.0},
    "severe": {"demand": 3200, "traffic": 1.6, "peak": 2.5},
    "gridlock_stress": {"demand": 3200, "traffic": 1.6, "peak": 3.0},
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timed(obj, name, totals, key=None):
    original = getattr(obj, name)
    def call(*a, **kw):
        start = time.perf_counter()
        try:
            return original(*a, **kw)
        finally:
            totals[key or name] += time.perf_counter() - start
    setattr(obj, name, call)


def enable_edge_subscriptions(eng):
    """Measurement-only experiment; slower in this Windows audit, not shipped in Engine."""
    for eid in eng.net.edges:
        eng.T.edge.subscribe(eid, (tc.LAST_STEP_VEHICLE_NUMBER, tc.LAST_STEP_VEHICLE_HALTING_NUMBER, tc.LAST_STEP_MEAN_SPEED))
    def poll():
        eng.edge_stats = {
            eid: (r[tc.LAST_STEP_VEHICLE_NUMBER], r[tc.LAST_STEP_VEHICLE_HALTING_NUMBER], r[tc.LAST_STEP_MEAN_SPEED])
            for eid, r in eng.T.edge.getAllSubscriptionResults().items()
            if eid in eng.net.edges and r.get(tc.LAST_STEP_VEHICLE_NUMBER, 0)
        }
    eng._poll_edges = poll


@contextmanager
def audited_start(work):
    """Benchmark-only CLI instrumentation, works with both engine backends."""
    targets = [module.traci] + ([module.libsumo] if module.libsumo else [])
    originals = [(target, target.start) for target in targets]
    def wrapper(original):
        def start(cmd, *a, **kw):
            cmd = list(cmd)
            if "--collision-output" in cmd:
                index = cmd.index("--collision-output")
                del cmd[index:index + 2]
            index = cmd.index("--collision.check-junctions")
            cmd[index + 1] = "true"
            cmd += ["--collision-output", str(work / "collisions.xml"),
                    "--tripinfo-output", str(work / "trips.xml"),
                    "--tripinfo-output.write-unfinished", "true",
                    "--summary-output", str(work / "summary.xml")]
            return original(cmd, *a, **kw)
        return start
    try:
        for target, original in originals:
            target.start = wrapper(original)
        yield
    finally:
        for target, original in originals:
            target.start = original


def xml_results(work):
    collisions = [dict(e.attrib) for e in ET.parse(work / "collisions.xml").getroot()]
    trips = [e for e in ET.parse(work / "trips.xml").getroot() if e.tag == "tripinfo"]
    delays = [float(e.get("departDelay", "0")) for e in trips if float(e.get("depart", "-1")) >= 0]
    summaries = list(ET.parse(work / "summary.xml").getroot())
    latest = summaries[-1].attrib if summaries else {}
    return {"collisionEvents": len(collisions), "collisionEventsByType": dict((k, sum(c.get("type") == k for c in collisions)) for k in sorted({c.get("type", "unknown") for c in collisions})),
            "collisionDetails": collisions, "uniqueCollisionPairs": len({tuple(sorted((c.get("collider", ""), c.get("victim", "")))) for c in collisions}),
            "departDelayMeanSeconds": statistics.mean(delays) if delays else None,
            "departDelayMaxSeconds": max(delays, default=None), "sumoFinalSummary": latest}


def run_case(name, params, seed=42, warmup=180.0, backend="libsumo", duration=300.0, edge_mode="poll"):
    os.environ["FLOWSTATE_BACKEND"] = backend
    work = ROOT / "data" / "work" / "performance" / f"{name}-{backend}-{seed}"
    work.mkdir(parents=True, exist_ok=True)
    totals = defaultdict(float)
    start = time.perf_counter()
    scenario = create("rush_hour", {**params, "duration": 5, "signals": "fixed"})
    config_seconds = time.perf_counter() - start
    load_net_info.cache_clear()
    path = ROOT / "data" / "networks" / "district.net.xml"
    start = time.perf_counter()
    load_net_info(str(path), "district")
    network_seconds = time.perf_counter() - start
    timed(scenario, "instantiate", totals)
    start = time.perf_counter()
    eng = module.Engine(scenario, module.RunConfig("rush_hour", scenario.params, seed, warmup=warmup), path.parent, work)
    constructor_seconds = time.perf_counter() - start
    for method in ("_read_state", "_sample"):
        timed(eng, method, totals)
    samples = []
    signatures = hashlib.sha256()
    phases = {k: {"seconds": 0.0, "steps": 0, "departed": 0, "arrived": 0} for k in ("warmup", "observation")}
    invalid = set()
    cpu_start = sample()
    with audited_start(work):
        try:
            start = time.perf_counter()
            eng.start()
            if edge_mode == "subscribe":
                enable_edge_subscriptions(eng)
            timed(eng, "_poll_edges", totals)
            init_seconds = time.perf_counter() - start
            timed(eng.demand, "step", totals, "demandSeconds")
            timed(eng.scenario, "on_step", totals)
            timed(eng.signals, "step", totals, "signalSeconds")
            native = eng.T
            class ConnectionTimer:
                def __getattr__(self, key):
                    return getattr(native, key)
                def simulationStep(self):
                    start = time.perf_counter()
                    try:
                        return native.simulationStep()
                    finally:
                        totals["sumoStepSeconds"] += time.perf_counter() - start
            eng.T = ConnectionTimer()
            child = getattr(eng.T, "_process", None)
            child_pid = getattr(child, "pid", None)
            child_start = sample(child_pid) if child_pid else None
            next_sample = -warmup
            while eng.t < duration:
                phase = phases["warmup" if eng.t < 0 else "observation"]
                start = time.perf_counter()
                eng.step()
                phase["seconds"] += time.perf_counter() - start
                phase["steps"] += 1
                phase["departed"] += eng.T.simulation.getDepartedNumber()
                phase["arrived"] += eng.T.simulation.getArrivedNumber()
                if eng.t + 1e-6 >= next_sample:
                    next_sample += 10
                    pending = len(eng.T.simulation.getPendingVehicles())
                    r = sample()
                    if child_pid:
                        r["child"] = sample(child_pid)
                    samples.append({"t": eng.t, "vehicles": len(eng.vehicles), "persons": len(eng.persons),
                                    "halted": sum(v[3] < 0.1 for v in eng.vehicles.values()),
                                    "approachQueued": sum(eng.junction_queues().values()), "pending": pending, "resources": r})
                    for vid in eng.vehicles:
                        if not eng.T.vehicle.isRouteValid(vid):
                            invalid.add(vid)
                    signatures.update(json.dumps(eng.frame(), sort_keys=True).encode())
            results = eng.results()
            (work / "frame.json").write_text(json.dumps(eng.frame()), encoding="utf-8")
            # Frame construction/serialization separately from the simulator and networking.
            start = time.perf_counter()
            for _ in range(200):
                frame = eng.frame()
            frame_seconds = (time.perf_counter() - start) / 200
            start = time.perf_counter()
            for _ in range(200):
                payload = json.dumps(frame, separators=(",", ":"))
            json_seconds = (time.perf_counter() - start) / 200
            cpu_end = sample()
            child_end = sample(child_pid) if child_pid else None
        finally:
            eng.close()
    for phase in phases.values():
        phase["stepsPerSecond"] = phase["steps"] / max(1e-9, phase["seconds"])
    return {"name": name, "seed": seed, "edgeMode": edge_mode, "backend": backend, "sumoVersion": module.sumo_env.sumo_version(),
            "python": platform.python_version(), "platform": platform.platform(), "params": scenario.params,
            "networkSHA256": digest(path), "stepLength": eng.dt, "warmup": warmup, "observation": duration,
            "timings": {"validationSeconds": config_seconds, "networkColdSeconds": network_seconds,
                        "constructorSeconds": constructor_seconds, "sumoInitSeconds": init_seconds, **totals},
            "phases": phases, "cpuSeconds": cpu_end["cpuSeconds"] - cpu_start["cpuSeconds"],
            "childCpuSeconds": child_end["cpuSeconds"] - child_start["cpuSeconds"] if child_end and child_start else None,
            "frameBuildSeconds": frame_seconds, "frameJsonSeconds": json_seconds, "frameBytes": len(payload.encode()),
            "websocketTransmission": "not included; see transport benchmark", "samples": samples,
            "invalidRouteIDs": sorted(invalid), "trajectorySHA256": signatures.hexdigest(), "engineResults": results, **xml_results(work)}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("libsumo", "traci"), default="libsumo")
    parser.add_argument("--cases", nargs="+", choices=tuple(WORKLOADS), default=list(WORKLOADS))
    parser.add_argument("--edge-mode", choices=("poll", "subscribe"), default="poll")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=float, default=300)
    args = parser.parse_args()
    reports = []
    for name in args.cases:
        report = run_case(name, WORKLOADS[name], args.seed, backend=args.backend, duration=args.duration, edge_mode=args.edge_mode)
        reports.append(report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(reports, indent=2), encoding="utf-8")
        print(json.dumps({"case": name, "phases": report["phases"], "collisions": report["collisionEvents"]}), flush=True)


if __name__ == "__main__":
    main()
