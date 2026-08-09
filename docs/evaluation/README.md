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

The 80-case release gate is explicitly labeled `offline_component_fixtures`.
It invokes the real `SafeTravelAgent`, safety routing, profile validation and
structured itinerary validator, but intentionally supplies `OfflineClassifier`
and `OfflineExtractor` fixtures rather than claiming to test the deployed rule
composition. Model and provider responses are keyed only by raw messages:
there is no network traffic or paid-model usage, and case expectations never
create predictions.
Multi-message cases start with an empty profile and carry each result into the
next turn in the same simulated thread. `--live` is deliberately rejected by
this runner; a separate explicit harness must set `ALLOW_PAID_EVAL=true` before
any paid evaluation is introduced.

Each report also contains a separate `production_composition_offline_seams`
section. That flow uses the deployed `RuleIntentClassifier`,
`RuleTravelExtractor`, `SafeTravelAgent`, `TravelChatApplication` and
`TripService` through plan/confirm/save/modify/explain/reopen. Supabase,
network providers and the paid model remain deterministic offline seams. The
section reports step success, P50/P95 orchestration latency, model calls,
input/output tokens, estimated cost, harness versions and change notes. Its
zero model cost means exactly “no paid call was made”; it is not a live latency
or billing benchmark.

Metric denominators are included in every JSON report: all 80 cases for intent
and task success; cases with expected slots and `slot_applicable: true` for slot
F1; expected ask/refusal cases for recall; predicted refusals for precision;
schema-required plans for schema and budget validity; citation-required plans
for coverage; observed citations for validity; and expected degradation cases
for fallback success. Applicability is declared in case data and never inferred
from a case ID. An empty precision/citation denominator is recorded as a
vacuous `1.0`, while its zero denominator remains visible in the report.

Exception cases dispatch by raw-message scenario to the component under test
and convert its actual return value or production-mapped exception into a
`ScenarioObservation`. Provider/usage/model/planner observations are not run
through the agent first. The database case alone uses `SafeTravelAgent`, backed
by a real `TripService`, and observes only its `ChatResult`.

The current baseline has no accepted known failures. Product changes must
improve implementation behavior rather than alter case answers or release
thresholds.
