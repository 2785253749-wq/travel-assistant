# Task 10 evaluation product optimization report

## Scope and constraints

- Base commit: `e3f9d550a5049eef28fd820ce6445cf4cf2d5213`.
- Work was limited to the `zero-cost-public-mvp` feature worktree.
- `tests/evaluation/cases.jsonl` was not changed. No `expected_*` value,
  raw message, allowed source, or threshold was changed.
- Task 11 assets (CI, rendering, README, deployment, and public-repository
  verification) were not touched.
- Tests ran with `T:\.venv\Scripts\python.exe`; the worktree virtual
  environment was not used or committed.

## Before optimization

The fixed 80-case runner exited `1` at the base commit. Its failures were
`P015`, `P019`, `M005`, `R001`, `R006`, `R014`, and `E002`.

| Metric | Before | Gate | After |
|---|---:|---:|---:|
| Intent accuracy | 98.75% | 90% | 100% |
| Slot micro-F1 | 99.13% | 90% | 100% |
| Clarification recall | 96.67% | 95% | 100% |
| Refusal recall | 93.33% | 95% | 100% |
| Schema validity | 92.86% | 98% | 100% |
| Budget validity | 92.86% | 98% | 100% |
| Citation coverage | 92.86% | 95% | 100% |
| Fallback success | 85.71% | 100% | 100% |
| Task success | 91.25% | 85% | 100% |

The unchanged metrics—refusal precision, citation validity, and unsupported
fact rate—also meet their gates after the change (100%, 100%, and 0%).

## Root causes and production fixes

| IDs | Root cause | Production fix | Evidence |
|---|---|---|---|
| P015, P019 | The deterministic domestic allowlist omitted 兰州 and 西宁. | Added both names and their 市 aliases while preserving foreign and unknown destination handling. | New route tests cover 兰州/兰州市/西宁/西宁市; P015/P019 now reach the structured planner. |
| M005 | `ExtractionResult.profile` was a `TravelProfile`; `travelers=0` failed Pydantic `ge=1` before the agent could issue a correction prompt, then became `AGENT_UNAVAILABLE`. | `RawTravelProfile` accepts raw integer output. `ExtractionCandidate` converts valid fields into a real `TravelProfile`, records `traveler_count` for 0, -1, and 7, and keeps the invalid count out of profile/planner/session state. | Real Task 2 seam tests cover 0/-1/7 plus valid 1/6. Agent test proves a collecting response with `traveler_count` and no planner call. The runner reads only the actual extraction candidate's transient invalid slot for the corpus slot oracle. |
| R001 | The safety list did not recognize phrases such as 实时机票价格. | Added a deterministic dynamic-price/request pattern for live flight, hotel, ticket, price, availability, and inventory requests. | The raw R001 message returns `UNVERIFIABLE_REALTIME_REQUEST` before classifier/provider use. |
| R006, R014 | Safety matching omitted guarantee/absolute wording and disaster guarantees. | Added deterministic high-stakes guarantee patterns for safety, earthquake, disaster, injury, accident, and risk. | R006/R014 return `HIGH_STAKES_ADVICE`; a normal 夜游安全建议 remains non-refused. |
| E002 | `PlacesProvider` retried an empty result but treated a second empty result as successful empty data. | After exactly one query rewrite, a second valid empty response returns `data=[]`, `degraded=True`, and `PLACES_EMPTY_AFTER_RETRY`. | Provider regression asserts two requests, rewritten query, empty data, and the stable code; the exception runner observes the real provider result. |

## TDD evidence

Each production behavior was first exercised by a focused regression test.

| Cycle | RED command and result | GREEN command and result |
|---|---|---|
| Allowlist and safety | `pytest tests/unit/test_agent_routes.py -q -k 'new_domestic_city_aliases or unverifiable_realtime_and_guaranteed_safety_requests or ordinary_travel_safety_advice'` → 7 failed, 1 passed. | Same command → 8 passed. |
| Raw extraction candidate | `pytest tests/unit/test_extraction.py -q -k task2_extraction` → 5 failed: 0/-1 raised the old Pydantic validation error; 7/1/6 returned the old profile shape. | Same command → 5 passed. |
| Agent issue propagation | Mutation removing candidate issues: `pytest tests/unit/test_agent_routes.py -q -k invalid_extracted_traveler_count` → 1 failed. | Restored propagation: same command → 1 passed. |
| Places empty retry | `pytest tests/unit/test_providers.py -q -k second_empty_result` → 1 failed because the old provider returned non-degraded empty data. | `pytest tests/unit/test_providers.py -q` → 15 passed. |

An existing multi-turn custom-extractor regression then exposed a compatibility
gap: a legacy extractor returning a delta `TravelProfile` lost previous fields.
The focused test failed, and the compatibility branch now merges that delta
with the current profile; its focused test passes.

## Final verification

- Focused changed-area suite: `101 passed`, with one existing Starlette/httpx
  deprecation warning.
- Full suite: `python -m pytest -q` → `234 passed`, with the same one warning.
- Runner: `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation` → exit `0`.
  It reports 80 cases, no failures, no failed thresholds, and
  `known_failures: []`.

`agent_failed` appears once in runner stderr because E010 intentionally
exercises the SafeTravelAgent's database-failure fallback; this is its
expected deterministic observation, not a runner failure.

## Changed files

- Production: `app/schemas.py`, `app/agent/extraction.py`,
  `app/agent/graph.py`, `app/agent/safety.py`, `app/graph.py`, and
  `app/providers/places.py`.
- Tests/evaluation: `tests/unit/test_agent_routes.py`,
  `tests/unit/test_extraction.py`, `tests/unit/test_providers.py`,
  `tests/test_app.py`, `tests/evaluation/runner.py`,
  `tests/evaluation/test_metrics.py`, and `tests/evaluation/baseline.json`.

## Remaining concerns

- The public `TravelProfile.travelers >= 1` validation remains in force. The
  raw candidate exists only at extraction time; invalid counts are never put
  into a `TravelProfile`, planner input, or session store.
- `docs/work-log-2026-07-30.md` was already untracked in the worktree and was
  deliberately not read, changed, staged, or committed by this task.

## Safety refinement round 1

### Finding and root cause

The first dynamic-request regex made its trailing price/inventory predicate
optional. A relative-date lodging guidance request (`明天住什么酒店比较方便`) therefore
matched as if it required a realtime provider. Separately, the guarantee regex
treated `确保` as an unconditional safety promise, so a request for guidance
(`如何确保夜游安全`) was refused.

### RED

Added `test_ordinary_timed_lodging_and_safety_guidance_are_not_refused` to
`tests/unit/test_agent_routes.py`, retaining the existing R001/R006/R014
refusal assertions. Ran:

```text
T:\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_routes.py -q -k
'ordinary_timed_lodging_and_safety_guidance or
unverifiable_realtime_and_guaranteed_safety_requests'
```

Result: `2 failed, 3 passed, 24 deselected`. The ordinary lodging request
returned `UNVERIFIABLE_REALTIME_REQUEST`; the ordinary safety-guidance request
returned `HIGH_STAKES_ADVICE`.

### GREEN

- The realtime pattern now requires a price, inventory, or availability token
  after a timed flight/hotel/ticket subject.
- The high-stakes pattern retains `保证` and `绝对` promises plus disaster
  impossibility claims, but no longer treats `确保` alone as a guarantee.

Focused GREEN command:

```text
T:\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_routes.py -q -k
'ordinary_timed_lodging_and_safety_guidance or
unverifiable_realtime_and_guaranteed_safety_requests or
ordinary_travel_safety_advice'
```

Result: `6 passed, 23 deselected`.

Additional verification:

- `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_routes.py -q`
  → `29 passed`.
- `T:\.venv\Scripts\python.exe -m tests.evaluation.runner --cases
  tests/evaluation/cases.jsonl --output build/evaluation` → exit `0`.
  The expected `agent_failed` log is the E010 fallback scenario.

Implementation commit: `7abadf7d8d0acd557c97676ef44aa96d9222116b`
(`fix: narrow safety refusal patterns`).

## Safety refinement round 2

### Finding and root cause

Round 1 correctly stopped treating every `确保` phrase as a guarantee, but
therefore missed direct promises such as `确保旅途安全`. Its realtime regex also
required a second dynamic token after the timed travel subject; concise
requests such as `明天票价是多少` have one `票价` token that is both the travel
subject and price demand.

### RED

Added direct-guarantee, precaution/guidance, ordinary-flight-planning, and
concise-ticket-price cases to `tests/unit/test_agent_routes.py`. Ran:

```text
T:\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_routes.py -q -k
'direct_safety_guarantees or safety_precautions_and_ordinary_flight_planning or
concise_timed_ticket_price_request or ordinary_timed_lodging_and_safety_guidance
or unverifiable_realtime_and_guaranteed_safety_requests'
```

Result: `2 failed, 9 passed, 24 deselected`. `确保旅途安全` was not refused, and
`明天票价是多少` was not refused. The ordinary guidance and planning cases were
already non-refused.

### GREEN

- A direct-ensure matcher now requires `确保` to be followed by an explicit
  protected traveler/itinerary subject and safety result. It refuses direct
  promises (`确保我人身安全`, `确保旅途安全`) but does not match guidance or
  equipment precautions (`如何确保夜游安全`, `确保带上安全装备`).
- Realtime detection now requires three semantic signals: a time marker, a
  travel subject, and a price/inventory/availability demand. `票价` appears in
  both the subject and demand sets so concise price questions remain refused,
  while normal hotel/flight planning lacks a dynamic demand.

Focused GREEN command (same selector) → `11 passed, 24 deselected`.

Additional verification:

- `T:\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_routes.py -q`
  → `35 passed`.
- `T:\.venv\Scripts\python.exe -m tests.evaluation.runner --cases
  tests/evaluation/cases.jsonl --output build/evaluation` → exit `0`.
  The expected `agent_failed` log is E010's fallback observation.

Implementation commit: `e1eb60ac3dd3aa1853afcfe253fa22f7174ee99e`
(`fix: distinguish safety guarantees from guidance`).
