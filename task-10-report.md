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
