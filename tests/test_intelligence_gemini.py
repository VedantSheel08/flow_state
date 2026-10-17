"""The Gemini guard: cache, rate limit, concurrency cap, timeouts, breakers, model chain, no key leakage. No network access."""

import threading

import httpx
import pytest

from intel_support import use_integrations

HAVE_CLIENT = use_integrations()  # must run before the guard is imported: GeminiUnavailable binds the assistant's GeminiError at import time
from flowstate.intelligence.gemini import GeminiSettings, GeminiUnavailable, GuardedGemini  # noqa: E402

KEY = "AI" + "za" + "FakeKeyForTests0123456789abcdefgh"  # built at run time so the repository never contains a key-shaped literal
SCHEMA = {"type": "object"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Fake:
    """Scripted client: ``plan[model]`` is a list of outcomes consumed in order (a value, or an exception to raise); the last repeats."""

    def __init__(self, plan):
        self.plan, self.calls, self.made = plan, [], []

    def factory(self, model, timeout, key):
        self.made.append((model, timeout, key))
        outer = self

        class C:
            def _next(self, what):
                outer.calls.append((model, what))
                steps = outer.plan[model]
                step = steps.pop(0) if len(steps) > 1 else steps[0]
                if isinstance(step, BaseException):
                    raise step
                return step

            def generate_json(self, system, user, schema):
                return self._next("json")

            def generate_text(self, system, user):
                return self._next("text")

        return C()


def err(code, msg="x"):
    return RuntimeError(f"Gemini API returned {code}: {msg}")


def guard(plan, clk=None, **kw):
    clk = clk or Clock()
    f = Fake(plan)
    s = GeminiSettings(**{"primary": "A", "fallback": "B", "rpm": 600, "retries": 1, **kw})
    return GuardedGemini(s, api_key=KEY, client_factory=f.factory, clock=clk, sleep=clk.sleep), f, clk


def test_without_a_key_nothing_is_called_and_the_assistant_is_told_it_is_unavailable():
    g = GuardedGemini(GeminiSettings(), api_key="", client_factory=lambda *a: pytest.fail("a client must not be built without a key"))
    g._key = None
    assert g.available is False
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "u")
    assert e.value.reason == "no_key"


def test_identical_requests_are_served_from_the_cache():
    g, f, _ = guard({"A": [{"ok": 1}]})
    assert g.generate_json("s", "u", SCHEMA) == {"ok": 1}
    first = g.generate_json("s", "u", SCHEMA)
    first["mutated"] = True  # a caller changing its copy must not corrupt the cache
    assert g.generate_json("s", "u", SCHEMA) == {"ok": 1}
    assert len(f.calls) == 1 and g.status()["cache"]["hits"] == 2
    g.generate_json("s", "different", SCHEMA)
    assert len(f.calls) == 2


def test_the_local_rate_limit_falls_back_instead_of_hammering_the_api():
    g, f, clk = guard({"A": ["t"]}, rpm=2, max_wait_s=0.5)
    g.generate_text("s", "1")
    g.generate_text("s", "2")
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "3")
    assert e.value.reason == "rate_limited" and len(f.calls) == 2
    clk.t += 60  # a minute later the budget has refilled
    assert g.generate_text("s", "4") == "t"


def test_billing_or_auth_errors_close_the_whole_project_and_stop_further_calls():
    g, f, clk = guard({"A": [err(402, "credits depleted")], "B": ["never"]})
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "u")
    assert e.value.reason == "billing" and f.calls == [("A", "text")]  # the fallback model is not tried: the project is the problem
    assert g.available is False
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "other")
    assert e.value.reason == "breaker_open" and len(f.calls) == 1
    clk.t += 1801
    assert g.available is True


def test_an_invalid_key_reported_as_400_closes_the_project_instead_of_retrying_every_model():
    g, f, _ = guard({"A": [err(400, "API key not valid. Please pass a valid API key.")], "B": ["never"]})
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "u")
    assert e.value.reason == "auth" and f.calls == [("A", "text")] and g.available is False


def test_other_400_errors_only_skip_that_model():
    g, f, _ = guard({"A": [err(400, "schema not supported")], "B": ["ok"]})
    assert g.generate_text("s", "u") == "ok" and g.available is True


def test_rate_limited_primary_is_skipped_and_the_fallback_model_answers():
    g, f, clk = guard({"A": [err(429, "quota")], "B": ["from B"]})
    assert g.generate_text("s", "u") == "from B"
    assert g.status()["counters"]["used_fallback_model"] == 1
    assert g.generate_text("s", "u2") == "from B" and [c[0] for c in f.calls] == ["A", "B", "B"]  # A stays closed for a while
    clk.t += 61
    g2 = g.generate_text("s", "u3")
    assert f.calls[-2][0] == "A"  # tried again after the breaker expired


def test_unavailable_model_404_moves_on_and_stays_closed():
    g, f, _ = guard({"A": [err(404, "no longer available")], "B": ["ok"]})
    assert g.generate_text("s", "u") == "ok"
    assert g.status()["modelBreakers"]["A"]["open"] is True


def test_timeouts_are_retried_once_then_the_next_model_is_used():
    g, f, _ = guard({"A": [httpx.ReadTimeout("slow")], "B": ["fast"]})
    assert g.generate_text("s", "u") == "fast"
    assert [c[0] for c in f.calls] == ["A", "A", "B"]


def test_repeated_transient_failures_open_the_breaker_so_the_assistant_skips_gemini():
    g, f, _ = guard({"A": [err(503)], "B": [err(503)]}, transient_threshold=2)
    for i in range(2):
        with pytest.raises(GeminiUnavailable):
            g.generate_text("s", f"u{i}")
    assert g.available is False  # both models closed: the assistant goes straight to its offline rules
    n = len(f.calls)
    with pytest.raises(GeminiUnavailable):
        g.generate_text("s", "again")
    assert len(f.calls) == n


def test_a_model_that_returns_non_json_is_not_retried_and_the_next_model_answers():
    g, f, _ = guard({"A": [RuntimeError("the model did not return JSON: 'oops'")], "B": [{"a": 1}]})
    assert g.generate_json("s", "u", SCHEMA) == {"a": 1}
    assert [c[0] for c in f.calls] == ["A", "B"]


def test_concurrency_is_capped():
    started, release = threading.Event(), threading.Event()

    class Slow:
        def generate_text(self, s, u):
            started.set()
            release.wait(2)
            return "done"

    s = GeminiSettings(primary="A", fallback=None, rpm=600, concurrency=1, max_wait_s=0.05, timeout_s=0.05)
    g = GuardedGemini(s, api_key=KEY, client_factory=lambda *a: Slow())
    out = []
    t = threading.Thread(target=lambda: out.append(g.generate_text("s", "1")))
    t.start()
    assert started.wait(2)
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "2")
    assert e.value.reason == "busy"
    release.set()
    t.join(2)
    assert out == ["done"]


def test_status_repr_and_errors_never_contain_the_key():
    g, f, _ = guard({"A": [err(401, f"API key {KEY} not valid")]})
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "u")
    blob = repr(g) + str(g.status()) + str(e.value) + (g.last_error or "")
    assert KEY not in blob and "AIza" not in blob
    assert g.status()["configured"] is True


# ---- against the real REST client (needs the experiments package from the integrations branch) -----------------------------------
needs_client = pytest.mark.skipif(not HAVE_CLIENT, reason="set FLOWSTATE_INTEGRATIONS_PATH to the integrations branch backend/flowstate")


def real_factory(handler):
    def make(model, timeout, key):
        from flowstate.experiments.assistant import GeminiClient

        return GeminiClient(api_key=key, model=model, timeout=timeout, transport=httpx.MockTransport(handler))

    return make


@needs_client
def test_real_client_sends_the_key_only_in_a_header_and_parses_the_answer():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"intent": "clarify"}'}]}}]})

    g = GuardedGemini(GeminiSettings(primary="gemini-test", fallback=None), api_key=KEY, client_factory=real_factory(handler))
    assert g.generate_json("sys", "user", SCHEMA) == {"intent": "clarify"}
    r = seen[0]
    assert r.headers["x-goog-api-key"] == KEY and KEY not in str(r.url) and "gemini-test:generateContent" in str(r.url)


@needs_client
def test_real_client_402_is_classified_as_billing_and_opens_the_breaker():
    g = GuardedGemini(GeminiSettings(primary="m", fallback="n"), api_key=KEY,
                      client_factory=real_factory(lambda req: httpx.Response(402, json={"error": {"message": "Your prepayment credits are depleted."}})))
    with pytest.raises(GeminiUnavailable) as e:
        g.generate_text("s", "u")
    assert e.value.reason == "billing" and g.available is False and KEY not in str(e.value)


@needs_client
def test_assistant_offline_fallback_triggers_on_our_exception_type():
    from flowstate.experiments.assistant import GeminiError

    assert issubclass(GeminiUnavailable, GeminiError)  # plan_request()/summarize() catch GeminiError and use their offline paths
