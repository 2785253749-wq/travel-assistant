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

The runner invokes the real `SafeTravelAgent`, Task 2 intent/profile extraction,
safety routing, profile validation and structured itinerary validator. Model
and provider responses are keyed only by raw messages: there is no network
traffic or paid-model usage, and case expectations never create predictions.
Multi-message cases start with an empty profile and carry each result into the
next turn in the same simulated thread. `--live` is deliberately rejected by
this runner; a separate explicit harness must set `ALLOW_PAID_EVAL=true` before
any paid evaluation is introduced.

Metric denominators are included in every JSON report: all 80 cases for intent
and task success; cases with expected slots and `slot_applicable: true` for slot
F1; expected ask/refusal cases for recall; predicted refusals for precision;
schema-required plans for schema and budget validity; citation-required plans
for coverage; observed citations for validity; and expected degradation cases
for fallback success. Applicability is declared in case data and never inferred
from a case ID. An empty precision/citation denominator is recorded as a
vacuous `1.0`, while its zero denominator remains visible in the report.

The current baseline records `P015`, `P019`, `M005`, `R001`, `R006`, and `R014`
as known product failures. They cover missing domestic allowlist entries, an
invalid-traveler extraction that aborts too early, and safety/refusal mapping
gaps. They are intentionally retained so a product fix must improve production
behavior rather than alter the evaluation answer or release thresholds.
