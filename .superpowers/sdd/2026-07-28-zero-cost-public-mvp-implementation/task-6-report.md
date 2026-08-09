# Task 6 report — degradable free travel-data providers

## Implementation

- Added a common `ProviderResult` with data, source, fetch time, degradation state, stable error code, and adapter-generated `TrustedEvidence` compatible with Task 5.
- Added free Open-Meteo weather and geocoding adapters. Each request supplies a 3-second connect / 6-second total timeout and a fixed User-Agent; network and 5xx responses retry once, while timeout, network, HTTP, invalid-payload, and location-not-found outcomes return explicit degraded results.
- Added a Photon places adapter. It returns only provider results, rewrites an empty result exactly once as `city + normalized alias`, and never invents a place after failure or no result.
- Added fixed-host, HTTPS-only 12306/Ctrip search links. All profile values are UTF-8 URL encoded; output identifies links as search jumps and states that third-party prices and inventory are authoritative.
- All provider tests use an in-memory HTTPX transport; no real network or key is used.

## TDD evidence

1. RED: `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_providers.py -v` failed at collection with `ModuleNotFoundError: No module named 'app.providers'`.
2. A subsequent RED added provider geocoding before forecast, and failed with two expected assertions because the original weather implementation incorrectly used fixed Hangzhou coordinates.
3. GREEN focused: `6 passed in 1.19s` for `tests\unit\test_providers.py`.
4. Full verification: `80 passed, 1 warning in 1.60s` for `pytest -q`; the one existing warning is Starlette's `httpx` TestClient deprecation warning.

## Concerns

- Provider output is deliberately advisory and needs normal freshness/display handling when Task 7 consumes it.
- Booking URLs are fixed, allowlisted search endpoints only; they do not expose a facility for user- or model-supplied base URLs.

---

## Fix round 1/5 — payload validity, URL authority, and total deadline

### Changes

- Places now distinguishes an authoritative empty `features: []` response from a malformed payload. A missing/non-list `features` field or malformed feature/property entry maps to degraded `PLACES_INVALID_RESPONSE` without query rewriting.
- Booking search URL validation rejects userinfo and every explicit port, including `:443`, in addition to requiring exact allowlisted HTTPS hostnames. The returned URL is rebuilt only from the validated scheme, hostname, path, and encoded search parameters.
- Weather and places operations now share one injected monotonic six-second deadline across all requests and retries. Each blocking HTTPX call has a wall-clock guard and receives only the remaining phase budget; deadline exhaustion maps to the existing stable provider timeout code.
- Deadline coverage uses a fake clock and deterministic duration-aware HTTPX transport. It performs no sleep and no real network request.

### TDD and verification

- Places RED: malformed schema and entry fixtures were treated as empty results and triggered a second request. GREEN maps them directly to `PLACES_INVALID_RESPONSE`.
- Booking RED: the userinfo, `:444`, and `:443` inputs all passed the old hostname-only check. GREEN rejects all three.
- Deadline RED: `PlacesProvider` had no injectable clock or operation budget. GREEN proves a 4-second first attempt leaves only 2 seconds for retry and returns `PLACES_TIMEOUT` at exactly 6 simulated seconds.
- Final focused: `13 passed in 1.15s`.
- Final full suite: `87 passed, 1 warning in 1.53s`. The warning is the existing Starlette/httpx TestClient deprecation warning.

### Concerns

- A wall-clock timeout may abandon a Python worker thread because threads cannot be force-killed. That worker remains bounded by the same remaining HTTPX phase timeout and is not reused; the provider returns the stable timeout result at the operation deadline.

---

## Fix round 2/5 — exact Photon feature schema

- Removed the fallback that interpreted fields directly on a Photon feature as its properties. Every feature must now be a dictionary containing a dictionary-valued `properties`; `properties.name` remains a required non-empty string and optional city data retains its existing type validation.
- RED: `{"features": [{"name": "西湖", "city": "杭州"}]}` produced a normal `Place` under the fallback.
- GREEN: the same payload returns degraded `PLACES_INVALID_RESPONSE`, empty data, and performs exactly one request without query rewriting.
- Final focused: `14 passed in 1.15s`.
- Final full suite: `88 passed, 1 warning in 1.52s`. The warning is the existing Starlette/httpx TestClient deprecation warning.
- Booking-link authority checks and the shared six-second provider deadline were unchanged and remain covered by the focused suite.
