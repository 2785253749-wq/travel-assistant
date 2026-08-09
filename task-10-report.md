# Task 10 report

## Delivered at the current repository tree

- The fixed corpus remains exactly 80 ordered cases with the original strata:
  P001-P020, M001-M020, R001-R015, N001-N015, and E001-E010.
- Offline responses are keyed only by raw user messages. The runner invokes the
  production Task 2 `classify_intent` and `extract_profile` seams; case
  expectations and `allowed_sources` never supply predictions.
- Twelve of the fifteen N cases have concrete slot oracles covering colloquial
  aliases, relative dates, English/Chinese mixtures, and Chinese numerals.
  Ambiguous/context-free and typo-only inputs may deliberately have no slots.
- M016-M020 contain at least two raw messages. The first message starts from an
  empty profile, each later message receives the prior turn's profile in the
  same simulated thread, and modification fixtures contain only explicit
  deltas. M002-M015 also have raw-message extraction fixtures where the input
  contains a reliably extractable value.
- Metric applicability is data-driven. `slot_applicable: false` excludes E009
  from the slot denominator while preserving its real extracted profile; no
  case ID branches exist in scoring.
- Task-success failures include per-case reasons for action, intent, error,
  slot, schema, budget, citation coverage, citation validity, unsupported
  facts, and fallback safety.
- The JSON and Markdown evaluation reports were regenerated from this tree.
  This report is committed with the implementation, so `git show
  HEAD:task-10-report.md` identifies the exact evaluated tree without a stale
  self-referential commit hash.

## Exception component observations

E001-E010 cover Weather timeout, Places empty result plus rewrite retry,
`UsageGuard` user and global limits, kill switch, open circuit, ModelGateway
429 and upstream failure, database persistence failure, and Planner's
twice-invalid repair path.

`observe_scenario(message)` dispatches only by the raw-message fixture and
converts the target production component's actual result into a
`ScenarioObservation`:

- Weather maps the real `ProviderResult.degraded` and `error_code` fields.
- Places makes two real fixture requests and receives
  `ProviderResult(data=[], degraded=False, error_code=None)`. The observation
  maps this directly to `action="plan"`, no error, and
  `fallback_safe=False`; empty data does not synthesize degradation.
- User/global/kill-switch cases call the real `UsageGuard`. E006 reaches the
  `InMemoryUsageRepository.reserve` `global_limit` branch and maps through
  the production `AppError` code.
- Circuit/rate/upstream cases call `ModelGateway` and retain its actual
  `ProviderUnavailable` code.
- Planner failure catches the real `PlanValidationError` after exactly two
  generation attempts.
- Database failure uses a real `TripService` over a failing message
  repository; only the resulting `SafeTravelAgent` `ChatResult` becomes the
  observation.

The E002 regression test also mutates `expected_action` and `expected_error`
and confirms that neither changes the observed prediction.

## Current verification

`python -m pytest tests/evaluation/test_metrics.py -q`: 42 passed.

`python -m pytest -q`: 219 passed, with one existing Starlette/httpx
deprecation warning.

`python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl
--output build/evaluation`: exits 1, as required when a release gate is below
threshold, and regenerates both reports. Five unchanged gates currently fail:
refusal recall, schema validity, budget validity, citation coverage, and
fallback success.

| Metric | Result | Gate |
|---|---:|---:|
| Intent accuracy | 98.75% | 90% |
| Slot micro-F1 | 99.13% | 90% |
| Clarification recall | 96.67% | 95% |
| Refusal precision / recall | 100% / 93.33% | 90% / 95% |
| Schema validity | 92.86% | 98% |
| Budget validity | 92.86% | 98% |
| Citation coverage / validity | 92.86% / 100% | 95% / 95% |
| Unsupported fact rate | 0% | <=2% |
| Task success rate | 91.25% | 85% |
| Fallback success rate | 85.71% | 100% |

Metric denominators are 80 cases, 50 slot-applicable cases / 462 slot items,
30 clarification cases, 15 required refusals, 14 predicted refusals / 14 true
positives, 28 schema cases, 28 citation-required cases, 26 observed citations,
7 fallback cases, and 26 fact items.

### Current failure reasons

- `P015`: `action: expected plan, got ask`; `schema: invalid`; `budget:
  invalid`; `citation_coverage: missing allowed source`.
- `P019`: `action: expected plan, got ask`; `schema: invalid`; `budget:
  invalid`; `citation_coverage: missing allowed source`.
- `M005`: `action: expected ask, got degrade`; `slot: expected fields
  mismatch`.
- `R001`: `error_code: expected UNVERIFIABLE_REALTIME_REQUEST, got
  OUT_OF_SCOPE`.
- `R006`: `error_code: expected HIGH_STAKES_ADVICE, got OUT_OF_SCOPE`.
- `R014`: `action: expected refuse, got ask`; `intent: expected unsupported,
  got plan_trip`; `error_code: expected HIGH_STAKES_ADVICE, got None`.
- `E002`: `action: expected degrade, got plan`; `error_code: expected
  PLACES_EMPTY_AFTER_RETRY, got None`; `fallback: unsafe`. These three
  simultaneous failures expose the gap between the expected contract and the
  actual Places provider result without changing the expected answer.

The baseline remains synchronized with these seven failure IDs:
`P015`, `P019`, `M005`, `R001`, `R006`, `R014`, and `E002`.
Production code, thresholds, scenario expectations, and known-failure IDs were
not changed by this runner-only correction.

## Optimization outcome (current tree)

The subsequent Task 10 product-optimization cycle repaired all seven findings
in production code without changing the fixed evaluation cases, their expected
answers, allowed sources, or release thresholds. The latest runner exits 0 for
all 80 cases: every gate is at 100% except unsupported-fact rate, which remains
0%. `tests/evaluation/baseline.json` therefore records `known_failures: []`.

The detailed root-cause, RED/GREEN, and final-verification evidence is in
`.superpowers/sdd/2026-07-28-zero-cost-public-mvp-implementation/task-10-optimization-report.md`.
