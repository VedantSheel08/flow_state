"""Simulation event log. Every event originates from the simulation, a
scenario or a controller – never from the UI."""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

CATEGORIES = ("scenario", "controller", "incident", "emergency", "traffic", "transit", "system")
SEVERITIES = ("info", "success", "warning", "critical")


@dataclass
class Event:
    id: int
    t: float
    category: str
    severity: str
    message: str
    tls: str | None = None
    edge: str | None = None
    vehicle: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventLog:
    def __init__(self, listener: Callable[[Event], None] | None = None, limit: int = 2000):
        self._events: list[Event] = []
        self._ids = itertools.count(1)
        self._listener = listener
        self._limit = limit

    def emit(
        self,
        t: float,
        category: str,
        message: str,
        severity: str = "info",
        **kw: Any,
    ) -> Event:
        assert category in CATEGORIES, category
        assert severity in SEVERITIES, severity
        ev = Event(
            id=next(self._ids),
            t=round(t, 2),
            category=category,
            severity=severity,
            message=message,
            tls=kw.pop("tls", None),
            edge=kw.pop("edge", None),
            vehicle=kw.pop("vehicle", None),
            data=kw.pop("data", {}) or {},
        )
        self._events.append(ev)
        if len(self._events) > self._limit:
            self._events = self._events[-self._limit :]
        if self._listener:
            self._listener(ev)
        return ev

    def all(self) -> list[Event]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()
