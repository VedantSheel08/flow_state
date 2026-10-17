# Gemini interpretation layer: models, configuration, behaviour, verification

Gemini is used to **interpret text** and nothing else: a natural-language request becomes a proposal that the assistant's validator checks
before anything runs, and a stored comparison becomes a short narrative whose numbers must equal measured values. It is **never** in the
signal-control loop (`tests/test_intelligence_secrets.py` fails if anything under `flowstate/rl` or `flowstate/sim` imports it). Every
failure degrades to the assistant's offline rule parser or to a deterministic summary built from the same measurements.

Code: `backend/flowstate/intelligence/{gemini,secrets,explain,api}.py`. It wraps the REST client and validators that already exist in
`flowstate.experiments.assistant` (branch `task/integrations`); no second HTTP client was written.

## Which model works (measured, free-tier project, not assumed)

Models were discovered with `GET /v1beta/models` (63 returned, 46 with `generateContent`), then each candidate was called through the
assistant's real `GeminiClient` with its real `Proposal` response schema (3 structured-output calls, then one text call; calls spaced 6 s apart).

| Model | Structured output | Latency (3 calls) | Text call | Verdict |
|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 3/3 valid | median 0.94 s, max 0.98 s | 0.74 s | **primary** |
| `gemini-3.1-flash-lite` | 3/3 valid | median 0.98 s, max 7.45 s | 2.80 s | **fallback model** |
| `gemini-3.8-flash` | 2/3 valid (1 read timeout at 30 s) | median 26.1 s | 14.5 s | rejected: too slow for interactive use |
| `gemini-2.5-flash` | not callable: HTTP 404 "no longer available to new users" | - | - | listed by the API but unusable |

Two things follow: a model appearing in the list does not mean it can be called, so selection has to be by a real call; and the id that the
integrations branch defaulted to (`gemini-3.8-flash`) exists but is a poor choice here. Pin ids rather than using `-latest` aliases, so results
are reproducible. Re-run the discovery and probe before changing models; ids and free-tier access change.

An earlier key, from a project with exhausted prepaid credits, returned HTTP 402 for every generation call. That case is handled (below).

## Configuration

The key is read **only** from the server environment. Nothing in the repository reads a path to a credential or contains one.

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | none | the key. Unset: Gemini is off and the offline fallbacks run |
| `FLOWSTATE_ENV_FILE` | none | local development only: a `.env` file from which just the key variable is parsed; the value is never copied into `os.environ`, logs or responses |
| `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` | model chain |
| `FLOWSTATE_GEMINI_TIMEOUT_S` | 10 | per-request timeout |
| `FLOWSTATE_GEMINI_RETRIES` | 1 | retries for transient errors only (timeouts, network, 5xx) |
| `FLOWSTATE_GEMINI_DEADLINE_S` | 25 | overall limit for one logical call across retries and models |
| `FLOWSTATE_GEMINI_RPM` | 10 | local token bucket (requests per minute); waits at most `FLOWSTATE_GEMINI_MAX_WAIT_S` (2 s), then falls back |
| `FLOWSTATE_GEMINI_CONCURRENCY` | 2 | simultaneous Gemini requests |
| `FLOWSTATE_GEMINI_CACHE_TTL_S` / `_CACHE_MAX` | 600 / 256 | response cache (key: hash of kind, models, prompt, schema) |

Production: put `GEMINI_API_KEY` in the service's protected environment file (`chmod 600`, owned by the service user), never in the image, the
repository, the frontend bundle or an API response. This branch does not touch any deployment file or production credential.

## Behaviour

| Situation | What happens |
|---|---|
| no key | `available` is false; the assistant uses its offline rules; `/status` says `configured: false` |
| same prompt again | served from the cache, no API call |
| local budget exhausted | waits up to 2 s, then `GeminiUnavailable(rate_limited)` and the offline path |
| HTTP 401 / 402 / 403, or 400 "API key not valid" | the whole project is closed for 30 minutes (a key or billing problem will not fix itself), the fallback model is **not** tried, the offline path runs |
| HTTP 429 | that model is closed for 60 s and the next model is tried |
| HTTP 404 (model unavailable) | that model is closed for 30 minutes, the next is tried |
| timeout / network / 5xx | one retry, then the next model; 3 consecutive failures close that model for 30 s |
| other 400, non-JSON output | that model cannot answer this request; the next is tried |
| Gemini text quotes a number that was not measured | the draft is discarded, the deterministic summary is returned, `rejectedNumbers` lists the offenders |

`GeminiUnavailable` subclasses the assistant's `GeminiError`, which is what makes `plan_request()` and `summarize()` fall back unchanged.
`GuardedGemini.status()` (served at `GET /api/intelligence/status`) reports models, breaker state, limits, cache statistics, latency p50/p95 and
counters, and never any key material. All logging goes through a filter that masks the configured key and anything shaped like a Google API key.

## Endpoints (additive; mounted from `api/app.py` behind an `ImportError` guard)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/intelligence/status` | Gemini health, `defaultController` |
| POST | `/api/intelligence/explain` | body `{"evaluation": "standard" \| "stress"}`; explains a *stored* held-out evaluation; response has `summary`, `source` (`gemini` or `measurements`), `rejectedNumbers`, `note`, `facts` |
| POST | `/api/intelligence/plan` | body `{"text": "..."}` (1-2000 characters); the assistant's `plan_request` with the guard; `503` if the experiments package is not installed |

## Verification (what was actually run)

Live, against the Gemini API with the free-tier key, through the guard (3 tests in `tests/test_intelligence_gemini_live.py`, plus scratch scripts):

* structured proposal from a natural-language request validates against the assistant's `Proposal` model (1.3 to 2.8 s including client creation);
* `plan_request` with the real district `World`: `source: gemini`, validation `ok`, 1.2 s;
* grounded explanation of the stored held-out evaluation: accepted as `source: gemini` with no rejected numbers (2.95 s); repeated call served from the cache in under 1 ms;
* a deliberately invalid key: the API answers 400 "API key not valid", the guard classifies it as `auth`, the assistant falls back to `source: rules` with a valid plan, and the next call does not touch the API.

An early live run found two defects, both fixed: Gemini wrote "95% confidence interval" and the grounding check rejected "95%" (the confidence level is a true fact and is now part of `facts`), and an invalid key was classified as a per-model error instead of closing the project.

Offline (no network): 14 guard tests with scripted clients and a fake clock (cache, rate limit, 402/401/400 project breaker, 429/404 model skipping, timeout retry, threshold breaker, concurrency cap, no key in `repr`/status/errors), 3 of them against the real `GeminiClient` over `httpx.MockTransport` (key only in a header, never in the URL), secret tests (redaction, log filter, repository scan for key-shaped strings and for the configured key, `.env` ignored, no Gemini import in the control path), explanation grounding tests, API tests.

## Not verified / limits

* The free tier's real request and daily limits are unknown to me; no HTTP 429 was observed. The default of 10 requests/minute is a conservative guess, and the 429 handling has only been exercised with scripted errors.
* The model-fallback path (primary failing, secondary answering) has only been exercised with scripted errors, not live.
* The grounding check verifies **numbers**, not claims: Gemini's explanation said the data "supports queue heuristic as the best controller among the options tested for travel time improvement". That sentence is consistent with the measurements, but nothing checked it. Keep the measurements next to the text (the API does).
* Latencies are from a handful of calls on one day from one machine; they are indicative, not a service level.
* Cross-branch coupling: `explain` and `plan` need `flowstate.experiments.assistant` (branch `task/integrations`). `gemini.py` binds that module's `GeminiError` at import time, so it must be importable before `flowstate.intelligence.gemini`; in a deployment that mounts both this holds. Tests that need it are skipped unless `FLOWSTATE_INTEGRATIONS_PATH` points at that branch's `backend/flowstate`.
* Production deployment, production credentials and paid billing were not touched.
