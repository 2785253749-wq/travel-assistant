# Offline evaluation gate

Run the fixed, versioned corpus from the repository root:

```powershell
python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation
```

The command creates `evaluation-report.json` for automation and
`evaluation-report.md` for review. It exits `1` whenever a release threshold
is missed and prints every failed case ID in both files. The corpus always has
80 ordered, unique cases: 20 complete domestic plans (`P`), 20 missing or
contradictory requests (`M`), 15 refusals (`R`), 15 natural-language variants
(`N`), and 10 provider/model/limit failures (`E`).

The runner invokes the real `SafeTravelAgent`, safety routing, profile
validation and structured itinerary validator. Model and provider seams use
fixed fixtures only: there is no network traffic or paid-model usage. `--live`
is deliberately rejected by this runner; a separate explicit harness must set
`ALLOW_PAID_EVAL=true` before any paid evaluation is introduced.

Metric denominators are included in every JSON report: all 80 cases for intent
and task success; only expected slots for slot F1; expected ask/refusal cases
for recall; predicted refusals for precision; schema-required plans for schema
and budget validity; citation-required plans for coverage; observed citations
for validity; and expected degradation cases for fallback success. An empty
precision/citation denominator is recorded as a vacuous `1.0`, while its zero
denominator remains visible in the report.

The current baseline records `P015` (兰州) and `P019` (西宁) as known failures:
they are valid domestic destinations but are not in the current deterministic
destination allowlist. They are intentionally retained so a product fix must
improve the production allowlist rather than alter the evaluation answer.
