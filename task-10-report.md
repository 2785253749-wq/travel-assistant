# Task 10 report

## Delivered

- Added the fixed 80-case offline corpus at `tests/evaluation/cases.jsonl`:
  P001-P020 complete domestic planning, M001-M020 missing/conflicting and
  multi-turn modification, R001-R015 boundaries/refusals including injection
  and hallucination prompts, N001-N015 colloquial/context/mixed-language, and
  E001-E010 provider/model/format/limit/database degradation.
- Added a deterministic runner that drives the production `SafeTravelAgent`,
  routing, safety checks, profile validation, and structured itinerary
  validation. Model/provider seams are fixed fixtures; it has no network or
  paid-model path.
- Added formula/corpus tests, release thresholds, JSON and Markdown reports,
  baseline metadata, and operating documentation.

## Verification

`python -m pytest tests/evaluation/test_metrics.py -v`: 3 passed.

`python -m pytest -q`: 180 passed, 1 existing third-party deprecation warning.

`python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation` writes both reports and exits 1 as required when gates fail.

| Metric | Result | Gate |
|---|---:|---:|
| Intent accuracy | 100% | 90% |
| Slot micro-F1 | 100% | 90% |
| Clarification recall | 100% | 95% |
| Refusal precision / recall | 100% / 100% | 90% / 95% |
| Schema validity | 92% | 98% |
| Budget validity | 92% | 98% |
| Citation coverage / validity | 92% / 100% | 95% / 95% |
| Unsupported fact rate | 0% | <=2% |
| Task success rate | 97.5% | 85% |
| Fallback success rate | 100% | 100% |

## Known release blockers

`P015` (兰州) and `P019` (西宁) are valid domestic destinations but are not
present in the production deterministic destination allowlist. They correctly
stop at `DESTINATION_UNDETERMINED`; this leaves two schema/budget/citation plan
denominators unmet. The corpus retains these failures and the runner lists the
case IDs rather than modifying production behavior or weakening thresholds.

## Fix round 1

- Replaced runner-owned answer fixtures with `tests/evaluation/offline_fixtures.py`.
  The adapter keys fixed model responses by raw user message and invokes the
  production Task 2 `classify_intent`, `extract_profile`, `ModelGateway`, and
  deterministic route. Tests prove that changing expected values does not
  change a prediction.
- Added scoring of explicit expected error codes, refusal true-positive
  precision/recall denominators, metric-specific failure reasons, baseline-only
  gate loading, and guarded `--live` contract checks.
- The exception corpus now exercises injected Weather timeout, Places retry
  empty result, real `UsageGuard` user/global limits, ModelGateway status
  normalization, and the structured planner's twice-invalid repair path.

Current offline run remains non-zero. In addition to P015/P019, it reports
R001/R006/R014 (safety error mapping/refusal behavior) and N004/N009/N014
(missing persisted context routes modifications to creation). These are kept
as product findings; no production rule, expected answer, or threshold was
changed to suppress them.

The latest hardening run has 8 focused evaluation tests passing. Its current
offline metrics are intent 98.75%, slot micro-F1 92.68%, clarification 100%,
refusal precision/recall 100%/93.33%, schema/budget/citation coverage 92.86%,
citation validity 100%, unsupported-fact rate 13.33%, task success 93.75%, and
fallback 100%. The report now names every case and metric-specific reason.
