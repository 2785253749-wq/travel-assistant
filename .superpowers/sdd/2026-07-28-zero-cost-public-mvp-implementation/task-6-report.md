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
