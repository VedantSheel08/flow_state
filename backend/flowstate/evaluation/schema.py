"""Versioned observation schema for controllers and evaluation (shared with the RL agent; docs/OBSERVATION_SCHEMA.md).

Everything here is read from SUMO through the engine's own per-second state. Nothing is a target value or a
configured multiplier, and no field is decorated: a quantity SUMO cannot report is ``None``, never a guess.

``observation()`` is cheap enough to call once per simulated second from a controller. It does not step or modify
the simulation. Field names are stable within a ``schemaVersion``; additions bump the minor version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..sim.engine import Engine

SCHEMA_VERSION = "1.0"
GREEN = "Gg"


def _phase(eng: "Engine", tls: str) -> dict[str, Any]:
    T = eng.T
    prog = eng.net.tls_programs.get(tls, [])
    phase = eng.tls_phase.get(tls)
    nxt = T.trafficlight.getNextSwitch(tls) - T.simulation.getTime()
    elapsed = None
    if phase is not None and prog and phase < len(prog):
        elapsed = max(0.0, prog[phase][0] - nxt)  # time the current phase has already run
    state = eng.tls_state.get(tls, "")
    return {
        "phase": phase, "phaseCount": len(prog), "elapsedInPhase": None if elapsed is None else round(elapsed, 2), "secondsToNextSwitch": round(max(0.0, nxt), 2),
        "state": state, "program": T.trafficlight.getProgram(tls), "heldBy": eng.signals.tls[tls].held_by if tls in eng.signals.tls else None,
    }


def junction_observation(eng: "Engine", jid: str) -> dict[str, Any]:
    """One signalised junction: its signal and, for every approach, the queue and the receiving-lane state."""
    net = eng.net
    approaches = []
    for e in net.edges.values():
        if e.to != jid or e.kind == "exit":
            continue
        seg = net.street_segment(e.id)
        stats = [eng.edge_stats.get(x, (0, 0, 0.0)) for x in seg]
        n = sum(s[0] for s in stats)
        approaches.append({
            "edge": e.id, "heading": net.edge_heading(e.id), "street": e.street or e.name, "class": e.cls,
            "vehicles": n, "halting": sum(s[1] for s in stats),
            "meanSpeedKmh": round(sum(s[0] * s[2] for s in stats) / n * 3.6, 1) if n else None,
            "laneKm": round(sum(net.edges[x].length * len(net.car_lanes(x)) for x in seg) / 1000.0, 4),
        })
    out: dict[str, Any] = {"id": jid, "name": net.junction_name(jid), "approaches": approaches}
    if jid in eng.tls_state:
        out["signal"] = _phase(eng, jid)
    return out


def observation(eng: "Engine", collector: Any | None = None) -> dict[str, Any]:
    """The whole-network observation at the engine's current time.

    ``collector`` (evaluation.collector.Collector) adds the contract measurements of the last completed second:
    density, body occupancy, storage use, speed percentiles, per-block queue extents and blocked movements.
    """
    T = eng.T
    obs: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "time": {"scenario": round(eng.t, 2), "sumo": round(T.simulation.getTime(), 2), "step": eng.dt, "warmup": eng.warmup, "inObservation": eng.t >= 0},
        "network": {
            "activeVehicles": len(eng.vehicles), "persons": len(eng.persons), "pendingInsertion": len(T.simulation.getPendingVehicles()),
            "heldExternally": len(getattr(eng.demand, "held", ())),
            "cumulative": {"added": eng.added, "teleports": eng.teleports, "collisionRecords": eng.safety.records, "collisionEpisodes": eng.safety.episodes},
        },
        "kpi": {k: v for k, v in eng.kpi.items() if k not in ("queues", "modes")},
        "junctions": {j: junction_observation(eng, j) for j in eng.net.tls_ids},
    }
    if collector is not None and collector.rows:
        row = collector.rows[-1]
        obs["scope"] = {"id": collector.zone.id, "laneKm": round(collector.zone.lane_km, 3), **{k: row[k] for k in ("t", "motor", "bikes", "density", "body", "storage", "p10", "p50", "p90", "halted")}}
        blocks = {}
        for key, st in collector.block_stats.items():
            if st.extent:
                blocks[f"{key[0]}:{key[1]}{key[2]}"] = {"queueMeters": st.extent[-1], "queueVehicles": st.vehicles[-1], "lengthMeters": round(collector.graph.blocks[key].length)}
        obs["blocks"] = blocks
        obs["blockedMovements"] = [[f"{a[0]}:{a[1]}{a[2]}", f"{b[0]}:{b[1]}{b[2]}"] for a, b in (collector.gb_arcs[-1] if collector.gb_arcs else [])]
    return obs


FIELDS: dict[str, str] = {
    "time.scenario": "scenario seconds (negative during warm-up)",
    "network.pendingInsertion": "vehicles SUMO has loaded but not yet inserted (native count)",
    "network.heldExternally": "background requests waiting outside the district in the demand generator (explicit hold queue)",
    "junctions.<id>.signal.elapsedInPhase": "seconds the current phase has run",
    "junctions.<id>.approaches[].halting": "SUMO halting vehicles (speed < 0.1 m/s) on the approach block, main + bay parts",
    "scope.density": "motor vehicles per km of motor lane over the declared scope, empty lanes included",
    "scope.storage": "(vehicle length + native standstill gap) summed over the scope / lane metres",
    "blocks.<street:dirSeg>.queueMeters": "persisted (>= 10 s) queue extent from the stop line, bumper to bumper (<= 10 m gaps), speed < 1 m/s",
    "blockedMovements": "(from block, to block) movements whose head vehicle is stopped while its signal is green and the receiving lane has no room",
}
