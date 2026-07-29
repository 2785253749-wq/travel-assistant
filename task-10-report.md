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
- Task-success failures include per-case reasons for slot, schema, budget,
  citation coverage, citation validity, and unsupported-fact failures. Focused
  anti-cheat tests independently trigger every reason.
- The JSON and Markdown evaluation reports were regenerated from this tree.
  This report is committed with the implementation, so `git show
  HEAD:task-10-report.md` identifies the exact evaluated tree without a stale
  self-referential commit hash.

## Verification

`python -m pytest tests/evaluation/test_metrics.py -q`: 24 passed.

`python -m pytest -q`: 201 passed, with one existing Starlette/httpx
deprecation warning.

`python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl
--output build/evaluation`: exits 1 because four release thresholds remain
below their unchanged gates, while regenerating both reports.

| Metric | Result | Gate |
|---|---:|---:|
| Intent accuracy | 98.75% | 90% |
| Slot micro-F1 | 99.13% | 90% |
| Clarification recall | 96.55% | 95% |
| Refusal precision / recall | 100% / 93.33% | 90% / 95% |
| Schema validity | 92.86% | 98% |
| Budget validity | 92.86% | 98% |
| Citation coverage / validity | 92.86% / 100% | 95% / 95% |
| Unsupported fact rate | 0% | <=2% |
| Task success rate | 92.50% | 85% |
| Fallback success rate | 100% | 100% |

The visible denominators are 80 cases, 50 slot-applicable cases / 462 slot
items, 29 clarification cases, 15 required refusals, 14 predicted refusals,
28 schema cases, 28 citation-required cases, 26 observed citations, 8 fallback
cases, and 26 fact items.

## Known product failures retained

- `P015`, `P019`: 兰州 and 西宁 are valid domestic cities absent from the
  deterministic destination allowlist, so schema, budget, and citation output
  cannot be produced.
- `M005`: the raw model response includes `travelers: 0`; production Pydantic
  rejects the entire extraction before `validate_profile`, yielding
  `AGENT_UNAVAILABLE` instead of a clarification.
- `R001`, `R006`, `R014`: deterministic safety classification returns the
  wrong refusal code or misses the required refusal.

The baseline records exactly these six case IDs. Production code, thresholds,
and expected answers were not changed to improve the evaluation. There are no
known unfinished Task 10 round-2 items; the six failures above are deliberately
retained product findings.
