"""Keep-the-box-clear rule: do not enter a junction unless the exit lane has room for the whole vehicle.

SUMO's own ``keepClear`` decides from the exit lane's occupancy at the moment the vehicle commits. In stop-and-go
traffic the exit queue grows after the vehicle has entered, so vehicles end up *standing inside the junction*: on
crossings, across the paths of cross traffic and of cyclists. The forensic snapshots of dense runs (docs/
TRAFFIC_REALISM.md) show nearly every junction and crossing collision in exactly that situation — a vehicle that
had waited tens of seconds inside the box and was then hit.

The guard applies the legal rule explicitly. Every step it looks at the vehicle at the head of each approach lane;
if its next lane has less than ``vehicle length + minGap + margin`` free, the vehicle is limited to the speed from
which it can still stop comfortably at the stop line (``setSpeed``, released as soon as room appears). It acts only
while the vehicle can still stop before the line, never on emergency vehicles, and never moves a vehicle that is
already committed to the junction. This is a driving rule, not a speed override of free traffic: a queue still
forms, spills back and can still gridlock — the vehicles simply wait at the line instead of in the box.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .vtypes import EMERGENCY_TYPES, base_type

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine

COMFORT_DECEL = 2.5  # m/s^2 used to plan the stop
STOP_MARGIN = 0.8  # m before the line


class BoxGuard:
    def __init__(self, eng: "Engine", margin: float = 2.0, window: float = 60.0, exempt: tuple[str, ...] = ("bus",)):
        self.eng = eng
        self.margin = margin
        self.window = window
        self.exempt = set(exempt) | set(EMERGENCY_TYPES) | {"wreck"}
        self.lanes: dict[str, dict[str, list[str]]] = {}  # approach lane -> {next edge: [receiving lanes]}
        self.lane_len: dict[str, float] = {}
        self.held: dict[str, str] = {}  # vehicle -> lane it is held on
        self.holds = 0  # vehicle-steps spent holding (a measure of how often the rule acts)
        self.releases = 0
        self.max_held = 0

    def start(self) -> None:
        T = self.eng.T
        for lane, info in self.eng.net.lanes.items():
            if not info.allows_car:
                continue
            links = T.lane.getLinks(lane)
            if not links:
                continue
            groups: dict[str, list[str]] = {}
            for link in links:
                groups.setdefault(link[0].rsplit("_", 1)[0], []).append(link[0])
            self.lanes[lane] = groups
            self.lane_len[lane] = info.length

    def _free(self, lane: str) -> float:
        T = self.eng.T
        ids = T.lane.getLastStepVehicleIDs(lane)
        if not ids:
            return self.eng.net.lanes[lane].length if lane in self.eng.net.lanes else 1e9
        # ids run from the rear to the front of the lane: the rearmost vehicle bounds the free space at the lane start
        rear = ids[0]
        return T.vehicle.getLanePosition(rear) - T.vehicle.getLength(rear)

    def step(self) -> None:
        eng, T = self.eng, self.eng.T
        for vid in list(self.held):
            if vid not in eng.vehicles:
                self.held.pop(vid, None)
        for lane, groups in self.lanes.items():
            if T.lane.getLastStepVehicleNumber(lane) == 0:
                continue
            head = T.lane.getLastStepVehicleIDs(lane)[-1]
            tid = eng.vtype.get(head) or T.vehicle.getTypeID(head)
            if base_type(tid) in self.exempt or head in eng.tracked:
                if head in self.held:
                    self._release(head)
                continue
            dist = self.lane_len[lane] - T.vehicle.getLanePosition(head)
            if dist > self.window:
                if head in self.held:
                    self._release(head)
                continue
            route = T.vehicle.getRoute(head)
            idx = T.vehicle.getRouteIndex(head)
            if idx + 1 >= len(route):
                continue  # arriving on this edge
            recv = groups.get(route[idx + 1])
            if not recv:
                continue
            need = T.vehicle.getLength(head) + T.vehicle.getMinGap(head) + self.margin
            if any(self._free(rl) >= need for rl in recv):
                if head in self.held:
                    self._release(head)
                continue
            speed = T.vehicle.getSpeed(head)
            room = dist - STOP_MARGIN
            brake_dist = speed * speed / (2.0 * COMFORT_DECEL)
            if head not in self.held and room < brake_dist:
                continue  # too close to stop before the line: committed, let it pass
            cap = (2.0 * COMFORT_DECEL * max(0.0, room)) ** 0.5
            T.vehicle.setSpeed(head, min(cap, max(speed, 0.0)) if room > 0.5 else 0.0)
            if head not in self.held:
                self.held[head] = lane
            self.holds += 1
        self.max_held = max(self.max_held, len(self.held))

    def _release(self, vid: str) -> None:
        self.held.pop(vid, None)
        try:
            self.eng.T.vehicle.setSpeed(vid, -1)
        except Exception:  # noqa: BLE001
            pass
        self.releases += 1

    def summary(self) -> dict[str, Any]:
        return {"enabled": True, "marginM": self.margin, "windowM": self.window, "holdVehicleSteps": self.holds, "releases": self.releases, "maxHeldAtOnce": self.max_held,
                "rule": "do not enter a junction unless the receiving lane has room for the whole vehicle"}
