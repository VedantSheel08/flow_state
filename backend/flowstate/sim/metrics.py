"""Performance measures computed from SUMO state.

Definitions (all per completed trip unless noted):

* travel time   – arrival time − departure time
* time loss     – SUMO ``timeLoss``: time lost versus driving at the desired
                  speed (delay from congestion, signals, turning)
* waiting time  – SUMO accumulated waiting time (speed < 0.1 m/s)
* throughput    – completed trips per hour over the trailing 5 minutes
* queue         – halting vehicles on signal / stop approach lanes
* mean speed    – space-mean speed of vehicles currently in the network
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Trip:
    id: str
    mode: str
    vtype: str
    depart: float
    arrival: float | None = None
    time_loss: float = 0.0
    waiting: float = 0.0
    distance: float = 0.0
    tag: str | None = None


@dataclass
class ModeStats:
    trips: int = 0
    travel: float = 0.0
    loss: float = 0.0
    waiting: float = 0.0
    distance: float = 0.0

    def add(self, tr: Trip) -> None:
        self.trips += 1
        self.travel += (tr.arrival or 0) - tr.depart
        self.loss += tr.time_loss
        self.waiting += tr.waiting
        self.distance += tr.distance

    def to_dict(self) -> dict[str, Any]:
        n = max(1, self.trips)
        return {
            "trips": self.trips,
            "travelTime": round(self.travel / n, 1) if self.trips else None,
            "timeLoss": round(self.loss / n, 1) if self.trips else None,
            "waiting": round(self.waiting / n, 1) if self.trips else None,
            "speed": round(self.distance / self.travel * 3.6, 1) if self.travel > 0 else None,
        }


@dataclass
class Metrics:
    window: float = 300.0
    trips: dict[str, Trip] = field(default_factory=dict)
    modes: dict[str, ModeStats] = field(default_factory=dict)
    recent: deque = field(default_factory=deque)  # (t, Trip)
    history: list[dict[str, Any]] = field(default_factory=list)
    completed: int = 0
    departed: int = 0
    junction_queue_max: dict[str, int] = field(default_factory=dict)

    def depart(self, vid: str, mode: str, vtype: str, t: float, tag: str | None = None) -> None:
        self.trips[vid] = Trip(vid, mode, vtype, t, tag=tag)
        self.departed += 1

    def update_vehicle(self, vid: str, time_loss: float, waiting: float, distance: float) -> None:
        tr = self.trips.get(vid)
        if tr:
            tr.time_loss, tr.waiting, tr.distance = time_loss, waiting, distance

    def arrive(self, vid: str, t: float) -> Trip | None:
        tr = self.trips.pop(vid, None)
        if tr is None:
            return None
        tr.arrival = t
        if tr.mode in ("incident",):
            return tr
        self.completed += 1
        self.modes.setdefault(tr.mode, ModeStats()).add(tr)
        self.recent.append((t, tr))
        return tr

    def sample(self, t: float, active: dict[str, int], speeds: list[float], queues: dict[str, int], pending: int, teleports: int, persons: int) -> dict[str, Any]:
        while self.recent and self.recent[0][0] < t - self.window:
            self.recent.popleft()
        rec = [tr for _, tr in self.recent if tr.mode in ("car", "truck", "bus")]
        span = min(self.window, max(t, 1.0))
        all_motor = ModeStats()
        for m in ("car", "truck", "bus"):
            s = self.modes.get(m)
            if s:
                all_motor.trips += s.trips
                all_motor.travel += s.travel
                all_motor.loss += s.loss
                all_motor.waiting += s.waiting
                all_motor.distance += s.distance
        for j, q in queues.items():
            if q > self.junction_queue_max.get(j, 0):
                self.junction_queue_max[j] = q
        total_q = sum(queues.values())
        row = {
            "t": round(t, 1),
            "active": sum(active.values()),
            "activeByMode": active,
            "persons": persons,
            "completed": self.completed,
            "throughput": round(len(self.recent) * 3600.0 / span) if t > 30 else None,
            "travelTime": round(sum((tr.arrival or 0) - tr.depart for tr in rec) / len(rec), 1) if rec else None,
            "timeLoss": round(sum(tr.time_loss for tr in rec) / len(rec), 1) if rec else None,
            "waiting": round(sum(tr.waiting for tr in rec) / len(rec), 1) if rec else None,
            "meanSpeed": round(sum(speeds) / len(speeds) * 3.6, 1) if speeds else None,
            "queued": total_q,
            "maxQueue": max(queues.values()) if queues else 0,
            "pending": pending,
            "teleports": teleports,
            "overall": all_motor.to_dict(),
        }
        self.history.append(row)
        return row

    def summary(self) -> dict[str, Any]:
        return {
            "departed": self.departed,
            "completed": self.completed,
            "byMode": {m: s.to_dict() for m, s in sorted(self.modes.items())},
            "junctionMaxQueue": dict(sorted(self.junction_queue_max.items(), key=lambda kv: -kv[1])[:6]),
        }
