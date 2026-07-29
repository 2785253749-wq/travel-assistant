# Task 7 report — structured itinerary, budget, and fact constraints

## Implementation

- Added strict Pydantic API schemas for activities, three-part days, continuous 2–7 day itineraries, explicit CNY budget categories, and source citations.
- Budget totals are deterministic: transport, hotel, food, tickets, reserve, and other must be non-negative and exactly equal `total`; the output names both currency and traveler basis/count.
- Added `validate_itinerary`, which checks confirmed profile dates, traveler basis, confirmed budget limit, trusted Task 5 evidence IDs, HTTPS source metadata, and disclosure fields (`fetched_at`, `freshness`).
- Dynamic price/inventory/opening-time language without a citation is rejected as `UNSOURCED_FACT`; citations not matching a trusted Provider/Task 5 evidence record are rejected as `UNTRUSTED_EVIDENCE`.
- Added a bounded `Planner`: structured JSON is parsed before use, gets one repair callback containing only stable issue codes, and then fails closed with `PLAN_VALIDATION_FAILED` rather than returning partial text.

## TDD evidence

1. RED: `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_planning.py -v` failed during collection with `ModuleNotFoundError: No module named 'app.agent.planning'` after the new tests were added.
2. GREEN focused: the same command passed `6 passed in 1.20s` after the minimal schema and validation implementation.
3. Full regression: `T:\.venv\Scripts\python.exe -m pytest -q` passed `94 passed, 1 warning in 1.56s`.

## Concern

- The existing Starlette/httpx TestClient deprecation warning is unchanged. The planner is an offline orchestration seam; its model callback must provide structured JSON and no network is used by these tests.

## Fix round 1/5 — production planner, evidence binding, and estimates

- Production `SafeTravelAgent` now defaults to `ModelStructuredPlanner`, which invokes the Task 7 structured planner rather than returning legacy raw model text. The route performs one repair with stable issue codes and returns `PLAN_VALIDATION_FAILED` without a partial plan after a second invalid result; legacy injected `invoke` fakes remain a compatibility seam for pre-existing API tests.
- Added timestamped trusted evidence. Task 6 weather/place adapters attach their provider `fetched_at`; structured planning rejects missing, future, or TTL-expired evidence (24 hours for provider data; seven days for official/government data). Source citation metadata is derived solely from the registry.
- Replaced free-text factual authorization with `FactClaim(text, evidence_id)`. Claim text must normalize exactly to the canonical trusted fact and generated citations are populated from that registry. Model-supplied citation metadata is discarded.
- Added `EstimateRange` and structured `PlanningAssumption`; budget totals expose `trip_total`, correctly multiply `per_person` plans by traveler count, and require a range containing the point estimate. Assumptions cannot carry price, availability, opening, or inventory claims.

### Round-1 verification

- RED: the expanded planning test initially failed at collection because `EstimateRange` did not exist; the production wiring test then failed with the agent treating ASCII `Hangzhou` as an unallowlisted destination, confirming the test exercised the real routing gate. It was corrected to the existing Chinese allowlisted destination before validating planner behavior.
- GREEN focused: `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_routes.py tests\unit\test_planning.py -q` → `27 passed in 1.18s`.
