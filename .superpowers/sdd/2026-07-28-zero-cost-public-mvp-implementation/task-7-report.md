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

## Fix round 2/5 — activity-scoped facts and canonical citations

- Replaced activity output `claims` with canonical `facts` (the legacy input alias remains only for compatibility). Titles and notes reject variable price/availability/opening/inventory assertions; facts belong to the specific activity and must independently match a registry evidence record.
- Citation metadata supplied in a candidate is discarded. After claim validation, each activity receives a fresh, deduplicated citation list created solely from the canonical registry, with its provider fetch time and freshness message.
- Assumptions retain structured `assumption_id`, allowlisted category, and guarded description. Itinerary validation now requires unique assumption IDs and requires `estimate.assumption_id` to point to exactly one existing assumption.

### Round-2 verification

- RED: new activity-fact cases failed because `Activity.facts` was not part of the strict schema, and missing/duplicate estimate assumption IDs were accepted.
- GREEN focused: `tests\unit\test_planning.py` → `12 passed in 1.18s`.
- Full: `T:\.venv\Scripts\python.exe -m pytest -q` → `101 passed, 1 warning in 1.55s` (existing Starlette/httpx deprecation warning).

## Fix round 3/5 — top-level fact boundary and direct citation defense

- Itinerary-level title and notes now reject the same variable-fact vocabulary as activity title/notes. A fact is valid only in the owning activity's `facts` list, never through a global claim switch.
- Canonical citation creation now deduplicates `(evidence_id, canonical fact)` within each activity. Direct `validate_itinerary` calls also require the citation's fact, source, fetch timestamp, and generated freshness text to equal the registry result, preventing callers from bypassing planner normalization.

### Round-3 verification

- RED: top-level price/opening text was accepted, and duplicate facts emitted two citations.
- Full: `T:\.venv\Scripts\python.exe -m pytest -q` → `104 passed, 1 warning in 1.56s` (existing Starlette/httpx deprecation warning).

## Fix round 4/5 — canonical server-owned display text

- Replaced the title/notes keyword blacklist with one structural contract. Planner now discards every model-authored itinerary/activity title and note, then rebuilds them from the confirmed destination, trip length, day number, and slot; notes are always the server-owned empty template.
- Direct `validate_itinerary` calls require exact equality with those same canonical templates. This remains effective when callers bypass Pydantic construction validation with `model_copy` or `model_construct`; noncanonical text returns `NON_CANONICAL_DISPLAY_TEXT`.
- Removed the obsolete title/notes schema validators, regex, and dead scan helper. Variable provider facts remain confined to activity `facts` and the existing trusted-evidence/canonical-citation gate.

### Round-4 verification

- RED: the new English and Chinese injection cases produced `9 failed, 15 passed`; the failures covered `Hotel cost is CNY 399`, `All rooms are sold out`, Chinese variants, both direct-construction bypasses, and Planner preservation of malicious display text.
- GREEN focused: `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_planning.py -q` → `24 passed in 1.20s`.
- Full: `T:\.venv\Scripts\python.exe -m pytest -q` → `113 passed, 1 warning in 1.56s` (existing Starlette/httpx deprecation warning).

## Fix round 5/5 — pre-schema display canonicalization and direct fact binding

- Planner now canonicalizes only itinerary/activity `title` and `notes` on a copied raw JSON mapping before Pydantic validation. Missing, empty, oversized, numeric, or object-valued display fields are discarded without consuming the repair attempt; non-mapping candidates and malformed day/activity structure remain `SCHEMA_INVALID`, while dates, budgets, facts, assumptions, and all other schema fields remain untouched.
- Direct `validate_itinerary` now binds every activity fact to a current registry entry by exact evidence ID and exact canonical text, then requires the matching canonical citation in that same activity. Provider timestamps and TTL still determine registry membership, so model-copy/model-construct injection cannot bypass freshness, fact, or citation checks.
- Planner fact normalization now uses the same exact-text rule before rebuilding server-owned canonical citations; whitespace or case variants no longer inherit evidence authority.

### Round-5 verification

- RED: `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_planning.py -q` → `11 failed, 30 passed`; the failures covered pre-validation display rejection plus direct unknown-ID, non-exact-text, and missing-citation injections.
- GREEN focused: `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_planning.py -q` → `41 passed in 1.27s`.
- Full: `T:\.venv\Scripts\python.exe -m pytest -q` → `130 passed, 1 warning in 1.57s` (existing Starlette/httpx deprecation warning).

## Authorized post-circuit-breaker refactor — raw production model seam

- `ModelStructuredPlanner` no longer binds the production chat model to `Itinerary` through `with_structured_output`, or performs any itinerary Pydantic validation before `Planner`. It invokes the model for raw JSON content and supplies the itinerary JSON Schema only as prompt data.
- The production path is now `SafeTravelAgent → ModelStructuredPlanner raw content → Planner`: raw mappings reach display canonicalization before Pydantic, budget, evidence, and cross-model validation. Missing, empty, oversized, and non-string itinerary/activity display fields are rebuilt from server-owned templates on the first candidate without consuming the repair attempt.
- Malformed JSON, non-mapping responses, and non-display structural errors remain `SCHEMA_INVALID`. `Planner` permits exactly one semantic repair request and the agent returns `PLAN_VALIDATION_FAILED`, never `AGENT_UNAVAILABLE` or partial model text, when the second candidate is still invalid.
- Added production-seam integration coverage with a minimal fake chat model that exposes only raw `invoke`. The tests exercise the real agent, production model adapter, and planner together; they do not bypass the failing seam by injecting `Planner` directly.

### Authorized-refactor verification

- RED: `T:\.venv\Scripts\python.exe -m pytest W:\tests\integration\test_structured_planner_production_seam.py -q` → `5 failed in 1.63s`; valid malicious display payloads returned `collecting`, and malformed/non-mapping/non-display-invalid candidates returned `AGENT_UNAVAILABLE` instead of the bounded planner result.
- GREEN focused: `T:\.venv\Scripts\python.exe -m pytest W:\tests\unit\test_agent_routes.py W:\tests\unit\test_planning.py W:\tests\integration\test_structured_planner_production_seam.py -q` → `64 passed in 1.25s`.
- Full: `T:\.venv\Scripts\python.exe -m pytest W:\ -q` → `135 passed, 1 warning in 1.58s`.

### Authorized-refactor concern

- The unchanged Starlette/httpx TestClient deprecation warning remains. The chat client still has transport-level retries configured separately; semantic itinerary repair remains bounded to exactly one second planner candidate.
