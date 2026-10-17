"""Guarded Gemini access for the intelligence layer: cache, rate limit, concurrency cap, timeouts, circuit breakers, model chain.

Gemini only ever *interprets text* (natural language -> a validated proposal, results -> an explanation). It is never called from
the signal-control loop: nothing under ``flowstate.rl`` or the controllers imports this module (enforced by a test).

``GuardedGemini`` wraps the REST client that already exists in ``flowstate.experiments.assistant`` (it is imported lazily; this
module adds no second HTTP client) and exposes the same duck-typed interface the assistant expects (``generate_json``,
``generate_text``, ``available``). When Gemini cannot be used it raises :class:`GeminiUnavailable`, a subclass of the assistant's
``GeminiError``, so the assistant's existing offline rule parser / deterministic summary takes over unchanged.

Policy (every number is configurable through ``FLOWSTATE_GEMINI_*`` variables, defaults chosen for the free tier):

* models: primary ``gemini-3.5-flash-lite`` then ``gemini-3.1-flash-lite``; both chosen from a live ``models.list`` and verified with real
  structured-output calls (see docs/GEMINI_INTEGRATION.md). ``gemini-3.8-flash`` was rejected: median 26 s, one read timeout.
* per-call timeout 10 s, one retry for transient errors only (timeouts, network, 5xx), overall deadline 25 s;
* token bucket (default 10 requests/minute, waits at most 2 s, then falls back), at most 2 concurrent requests;
* responses cached 10 min by a hash of (kind, models, prompt, schema);
* breakers: 401/402/403 (and 400 "API key not valid") open the whole project for 30 min (a billing or key problem will not fix itself, so stop calling);
  429 and 404 open that model for 60 s / 30 min and the next model is tried; repeated transient failures open a model for 30 s.
"""

from __future__ import annotations

import collections
import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .secrets import gemini_api_key, redact

log = logging.getLogger(__name__)

# The assistant's own exception type, so its offline fallback triggers on ours. Bound at import time: wherever the assistant exists
# (any deployment that mounts it) ``flowstate.experiments`` is importable before this module, so ``GeminiUnavailable`` *is a* ``GeminiError``.
try:
    from flowstate.experiments.assistant import GeminiError as _Base
except ImportError:  # pragma: no cover - this branch is usable without the experiments package
    _Base = RuntimeError

PROJECT_WIDE = {401, 402, 403}


class GeminiUnavailable(_Base):
    """Gemini could not be used for this request. ``reason`` is one of: no_key, auth, billing, rate_limited, busy, breaker_open,
    timeout, upstream, invalid, deadline. The message never contains key material."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(redact(f"{reason}: {detail}" if detail else reason))
        self.reason = reason


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class GeminiSettings:
    primary: str = "gemini-3.5-flash-lite"
    fallback: str | None = "gemini-3.1-flash-lite"
    timeout_s: float = 10.0
    retries: int = 1
    deadline_s: float = 25.0
    rpm: float = 10.0
    max_wait_s: float = 2.0
    concurrency: int = 2
    cache_ttl_s: float = 600.0
    cache_max: int = 256
    project_open_s: float = 1800.0
    rate_open_s: float = 60.0
    model_404_open_s: float = 1800.0
    transient_threshold: int = 3
    transient_open_s: float = 30.0

    @classmethod
    def from_env(cls) -> "GeminiSettings":
        e = os.environ
        return cls(primary=e.get("GEMINI_MODEL") or cls.primary, fallback=(e.get("GEMINI_FALLBACK_MODEL", cls.fallback) or None),
                   timeout_s=_env_float("FLOWSTATE_GEMINI_TIMEOUT_S", cls.timeout_s), retries=int(_env_float("FLOWSTATE_GEMINI_RETRIES", cls.retries)),
                   deadline_s=_env_float("FLOWSTATE_GEMINI_DEADLINE_S", cls.deadline_s), rpm=_env_float("FLOWSTATE_GEMINI_RPM", cls.rpm),
                   max_wait_s=_env_float("FLOWSTATE_GEMINI_MAX_WAIT_S", cls.max_wait_s), concurrency=max(1, int(_env_float("FLOWSTATE_GEMINI_CONCURRENCY", cls.concurrency))),
                   cache_ttl_s=_env_float("FLOWSTATE_GEMINI_CACHE_TTL_S", cls.cache_ttl_s), cache_max=int(_env_float("FLOWSTATE_GEMINI_CACHE_MAX", cls.cache_max)))


class TokenBucket:
    def __init__(self, rpm: float, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.capacity = max(1.0, rpm)
        self.rate = max(rpm, 0.001) / 60.0
        self.tokens, self.t = self.capacity, clock()
        self.clock, self.sleep, self.lock = clock, sleep, threading.Lock()

    def acquire(self, max_wait_s: float) -> bool:
        deadline = self.clock() + max_wait_s
        while True:
            with self.lock:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                need = (1.0 - self.tokens) / self.rate
            if self.clock() + need > deadline:
                return False
            self.sleep(min(need, 0.25))


class TTLCache:
    def __init__(self, ttl_s: float, max_items: int, clock: Callable[[], float]):
        self.ttl, self.max, self.clock = ttl_s, max_items, clock
        self.items: "collections.OrderedDict[str, tuple[float, Any]]" = collections.OrderedDict()
        self.lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: str) -> Any | None:
        with self.lock:
            hit = self.items.get(key)
            if hit and self.clock() - hit[0] <= self.ttl:
                self.items.move_to_end(key)
                self.hits += 1
                return copy.deepcopy(hit[1])
            if hit:
                del self.items[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        if self.ttl <= 0 or self.max <= 0:
            return
        with self.lock:
            self.items[key] = (self.clock(), copy.deepcopy(value))
            self.items.move_to_end(key)
            while len(self.items) > self.max:
                self.items.popitem(last=False)


class Breaker:
    def __init__(self, clock: Callable[[], float]):
        self.clock, self.open_until, self.reason, self.failures = clock, 0.0, "", 0

    def allows(self) -> bool:
        return self.clock() >= self.open_until

    def remaining(self) -> float:
        return max(0.0, self.open_until - self.clock())

    def open(self, seconds: float, reason: str) -> None:
        self.open_until, self.reason = self.clock() + seconds, reason

    def success(self) -> None:
        self.failures = 0

    def transient(self, threshold: int, seconds: float) -> None:
        self.failures += 1
        if self.failures >= threshold:
            self.open(seconds, "upstream")
            self.failures = 0


def _status_code(exc: BaseException) -> int | None:
    m = re.search(r"returned (\d{3})", str(exc))
    return int(m.group(1)) if m else None


def default_client_factory(model: str, timeout_s: float, api_key: str):
    from flowstate.experiments.assistant import GeminiClient  # lazy: the existing REST client, not a second one

    return GeminiClient(api_key=api_key, model=model, timeout=timeout_s)


class GuardedGemini:
    def __init__(self, settings: GeminiSettings | None = None, *, api_key: str | None = None, client_factory: Callable[[str, float, str], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.s = settings or GeminiSettings.from_env()
        self._key = api_key or gemini_api_key()
        self._factory = client_factory or default_client_factory
        self._clients: dict[str, Any] = {}
        self.clock, self.sleep = clock, sleep
        self.bucket = TokenBucket(self.s.rpm, clock, sleep)
        self.cache = TTLCache(self.s.cache_ttl_s, self.s.cache_max, clock)
        self.sem = threading.BoundedSemaphore(self.s.concurrency)
        self.project = Breaker(clock)
        self.models = {m: Breaker(clock) for m in self.chain()}
        self.lat: "collections.deque[float]" = collections.deque(maxlen=200)
        self.count: collections.Counter[str] = collections.Counter()
        self.last_error = ""
        self.lock = threading.Lock()

    # -- never reveal the key -------------------------------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"GuardedGemini(models={self.chain()}, configured={bool(self._key)})"

    def chain(self) -> list[str]:
        return [m for m in dict.fromkeys([self.s.primary, self.s.fallback]) if m]

    @property
    def available(self) -> bool:
        """False when there is no key or every route is currently closed: the assistant then goes straight to its offline rules."""
        return bool(self._key) and self.project.allows() and any(self.models[m].allows() for m in self.chain())

    # -- the two operations the assistant uses --------------------------------------------------------------------------
    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return self._call("json", system, user, schema)

    def generate_text(self, system: str, user: str) -> str:
        return self._call("text", system, user, None)

    # -- internals ------------------------------------------------------------------------------------------------------
    def _client(self, model: str):
        if model not in self._clients:
            self._clients[model] = self._factory(model, self.s.timeout_s, self._key or "")
        return self._clients[model]

    def _bump(self, name: str) -> None:
        with self.lock:
            self.count[name] += 1

    def _fail(self, reason: str, detail: str = "") -> GeminiUnavailable:
        self._bump(f"unavailable.{reason}")
        self.last_error = redact(f"{reason}: {detail}")[:300]
        return GeminiUnavailable(reason, detail)

    def _call(self, kind: str, system: str, user: str, schema: dict[str, Any] | None) -> Any:
        ck = hashlib.sha256(json.dumps([kind, self.chain(), system, user, schema], sort_keys=True, default=str).encode()).hexdigest()
        cached = self.cache.get(ck)
        if cached is not None:
            self._bump("cache_hit")
            return cached
        if not self._key:
            raise self._fail("no_key")
        if not self.project.allows():
            raise self._fail("breaker_open", f"project closed for {self.project.remaining():.0f}s ({self.project.reason})")
        if not self.bucket.acquire(self.s.max_wait_s):
            raise self._fail("rate_limited", "local request budget exhausted")
        if not self.sem.acquire(timeout=self.s.max_wait_s + self.s.timeout_s):
            raise self._fail("busy", "too many concurrent Gemini requests")
        try:
            return self._through_chain(ck, kind, system, user, schema)
        finally:
            self.sem.release()

    def _through_chain(self, ck: str, kind: str, system: str, user: str, schema: dict[str, Any] | None) -> Any:
        start = self.clock()
        last: GeminiUnavailable | None = None
        for model in self.chain():
            br = self.models[model]
            if not br.allows():
                last = last or GeminiUnavailable("breaker_open", f"{model} closed for {br.remaining():.0f}s ({br.reason})")
                continue
            for attempt in range(1 + max(0, self.s.retries)):
                if self.clock() - start > self.s.deadline_s:
                    raise self._fail("deadline", f"over {self.s.deadline_s:.0f}s")
                t0 = self.clock()
                try:
                    client = self._client(model)
                    out = client.generate_json(system, user, schema) if kind == "json" else client.generate_text(system, user)
                except httpx.TimeoutException as exc:
                    br.transient(self.s.transient_threshold, self.s.transient_open_s)
                    last, retry = self._fail("timeout", f"{model}: {type(exc).__name__}"), True
                except httpx.HTTPError as exc:
                    br.transient(self.s.transient_threshold, self.s.transient_open_s)
                    last, retry = self._fail("upstream", f"{model}: {type(exc).__name__}"), True
                except Exception as exc:  # noqa: BLE001 - provider errors arrive as GeminiError with the status in the message
                    code = _status_code(exc)
                    detail = f"{model}: {redact(exc)[:200]}"
                    retry = False
                    if code in PROJECT_WIDE or (code == 400 and re.search(r"api key", str(exc), re.I)):  # an invalid key arrives as 400 "API key not valid"
                        self.project.open(self.s.project_open_s, "billing" if code == 402 else "auth")
                        raise self._fail("billing" if code == 402 else "auth", detail) from None
                    if code == 429:
                        br.open(self.s.rate_open_s, "rate_limited")
                        last = self._fail("rate_limited", detail)
                    elif code == 404:
                        br.open(self.s.model_404_open_s, "model_unavailable")
                        last = self._fail("upstream", detail)
                    elif code is not None and code >= 500:
                        br.transient(self.s.transient_threshold, self.s.transient_open_s)
                        last, retry = self._fail("upstream", detail), True
                    else:  # 400, non-JSON output, schema problems: this model cannot answer this request
                        last = self._fail("invalid", detail)
                else:
                    br.success()
                    with self.lock:
                        self.lat.append(self.clock() - t0)
                        self.count[f"ok.{model}"] += 1
                        if model != self.s.primary:
                            self.count["used_fallback_model"] += 1
                    self.cache.put(ck, out)
                    return out
                if not retry:
                    break
                self.sleep(0.2 * (attempt + 1))
        raise last or self._fail("breaker_open", "no route available")

    def status(self) -> dict[str, Any]:
        """Health for ``/api/intelligence/status``: never contains the key."""
        lat = sorted(self.lat)
        pick = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 3) if lat else None  # noqa: E731
        return {"configured": bool(self._key), "available": self.available, "models": self.chain(),
                "project": {"open": not self.project.allows(), "reason": self.project.reason, "secondsRemaining": round(self.project.remaining())},
                "modelBreakers": {m: {"open": not b.allows(), "reason": b.reason, "secondsRemaining": round(b.remaining())} for m, b in self.models.items()},
                "limits": {"requestsPerMinute": self.s.rpm, "concurrency": self.s.concurrency, "timeoutSeconds": self.s.timeout_s,
                           "retries": self.s.retries, "deadlineSeconds": self.s.deadline_s, "cacheTtlSeconds": self.s.cache_ttl_s},
                "cache": {"entries": len(self.cache.items), "hits": self.cache.hits, "misses": self.cache.misses},
                "latencySeconds": {"p50": pick(0.5), "p95": pick(0.95), "samples": len(lat)}, "counters": dict(self.count), "lastError": self.last_error or None}
