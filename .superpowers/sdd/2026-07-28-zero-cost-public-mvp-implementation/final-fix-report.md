# Final branch fix report

Date: 2026-08-09

Base review: `final-branch-review.md`

Critical fixes were committed separately as `a34f64b` (`fix: close critical public release findings`). The Important/Minor closure described below is the subsequent final-fix wave.

## Scope boundaries

- The fixed 80-case corpus, `baseline.json`, and all release thresholds were not changed.
- The untracked Voyage frontend plan and work log were intentionally left untouched.
- No public deployment, release tag, remote push, or online smoke result was fabricated.

## Critical closure

1. The public scanner now security-normalizes tracked paths with NFKC/default-ignorable removal and rejects JavaScript computed-property secret assignments while preserving the original path in diagnostics.
2. A legal seven-day itinerary produces a readable reply within the 4,000-character message contract. Confirmed trip plus user/assistant messages persist through one owner-scoped database transaction. A usage-store failure after successful persistence is logged and no longer converts the successful plan into a 503.
3. Safe titles, notes, facts, assumptions and allow-listed server booking links survive planning and render in the browser; unsafe variable facts/display fields remain fail-closed.

Recorded critical RED/GREEN:

```powershell
python -m pytest tests/integration/test_deployment_config.py -k "computed_property_secret_assignment or unicode_disguised_forbidden_paths" -q
```

- RED: exit `1`, `8 failed` (three computed-property assignments and five Unicode-disguised paths).
- GREEN: exit `0`, `8 passed`.

```powershell
python -m pytest tests/unit/test_agent_routes.py tests/unit/test_chat_application.py tests/unit/test_trip_service.py tests/unit/test_trip_production_wiring.py tests/integration/test_rls_contract.py -k "legal_seven_day or confirmed_structured_plan_returns or authenticated_confirmation_persists or atomic_repository or cannot_partially or planned_trip_and_both or supabase_planned_chat" -q
```

- RED: exit `1`, nine failing checks spanning reply length, atomic save and readable/linked output.
- GREEN: exit `0`, all nine selected checks passed.

The browser test for readable itinerary/facts/assumptions/booking links was RED with one failure, then GREEN; the post-critical full gates were `411 passed`, `17` Node tests passed, and `Public repository check passed`.

## Important closure

### Modify/explain and production composition

- `RuleIntentClassifier` distinguishes modify and explain for a saved trip.
- Modify intent and the exact instruction survive collect/confirm state; the revision planner receives the saved itinerary and modification request.
- Explain reads only the saved itinerary, verified activity facts/citations and assumptions; it makes no provider/model call.
- The HTTP production-seam test now plans, persists, reopens, modifies, explains and verifies the persisted revision.
- The 80-case report is labeled `offline_component_fixtures`. A separate `production_composition_offline_seams` harness exercises production rules, confirmation, persistence and plan/modify/explain/reopen. It reports P50/P95, versions, change notes and explicit zero paid-model/token/cost values.

```powershell
python -m pytest tests/evaluation/test_metrics.py -k "production_composition_flow or written_report_labels" -q
```

- RED: exit `1`, `2 failed, 43 deselected` (`run_production_composition_evaluation` absent and report mode absent).
- GREEN: exit `0`, `2 passed, 43 deselected`.

```powershell
python -m pytest tests/unit/test_agent_routes.py tests/unit/test_chat_application.py tests/integration/test_structured_planner_production_seam.py -k "production_rule_classifier or explain_uses_only or modify_intent_and_instruction or production_revision_prompt or confirm_reserves_against" -q
```

- RED: exit `1`; classifier/explain/revision/confirmation checks failed before the flow existed.
- GREEN: exit `0`, `7 passed, 76 deselected`.

### Abuse and paid-call accounting

- Atomic minute buckets independently enforce anonymous-network, authenticated-user and trusted-network-prefix limits before chat execution.
- Every attempted model `invoke` is counted, including failed calls and the planner repair call. Actual input/output tokens are retained.
- Supabase stores `model_calls` plus configured-rate `estimated_cost_micros` in a separate RLS-enabled service-role table. Rates are explicit environment configuration because provider prices change.
- Once a model call has occurred, failure/validation paths settle actual usage instead of rolling it back. A successful atomic trip save is returned even if the separate usage store is temporarily unavailable.
- Anonymous cookie `Secure` behavior now comes from validated `Settings.app_env`, not a second raw environment read.

Observed RED command for request limiting, cookie and result logging:

```powershell
python -m pytest tests/unit/test_usage.py tests/unit/test_chat_application.py tests/integration/test_chat_api.py -k "final_business_result or secure_flag or rate_limited" -q
```

- RED: exit `1`, `3 failed, 45 deselected`.
- GREEN focused chat boundary: exit `0`, `3 passed, 14 deselected`.
- GREEN usage/RPC selection: exit `0`, `4 passed, 22 deselected`.
- GREEN failed-call/atomic-persistence selection: exit `0`, `2 passed, 3 deselected`.

The first full regression exposed the new cost columns as an unmodeled private-table `ALTER TABLE`: exit `1`, `1 failed, 431 passed`. The design was changed to a separate service-role RLS table and the focused RLS/usage contract command finished `53 passed`.

Final review added these production-boundary closures:

- Raw modification instructions are rejected before confirmation or provider/model forwarding when they contain credential-shaped values.
- The production classifier recognizes the fixed-corpus modify expressions “换” and “不要太赶”; the production composition seam uses the latter instead of a synthetic “改为” phrase.
- Explain output is bounded by the same 4,000-character public reply contract as generated itinerary summaries.
- Confirmation uses an atomic claim. Concurrent replay cannot reserve quota, call the model or persist a second result; failed attempts restore only when no newer collection exists.
- Anonymous minute limits use the stable HMAC network subject, so deleting the signed conversation cookie does not reset the bucket. Pre-authentication network 429 logs and completion logs share the request ID and a non-enumerable HMAC subject.
- DeepSeek SDK retries are disabled in both the structured planner and legacy intent-model factories. The planner's one explicit repair remains the only second paid attempt, so both attempts cross the accounting gateway and fit the two reserved model-call slots.
- Migration `006` removes legacy cost-blind RPC overloads. Settlement returns a verified boolean, records already-incurred calls for reserved or expired reservations, and fails explicitly for missing/invalid state.
- Real incomplete/invalid collection results now carry the classified intent and stable `PROFILE_INCOMPLETE`/`PROFILE_INVALID` result codes.

Recorded final-review RED/GREEN:

```powershell
python -m pytest tests/unit/test_agent_routes.py tests/unit/test_usage.py tests/integration/test_chat_api.py tests/evaluation/test_metrics.py -k "production_rule_classifier_distinguishes or secret_is_rejected or removes_cost_blind or disables_hidden or real_incomplete_collection or production_composition_flow" -q
```

- RED: exit `1`, `8 failed, 3 passed`.

The maximum explain payload, verified settlement and correlated pre-authentication 429 selection was RED with `4 failed`; the concurrent confirmation, cookie-rotation limit and atomic clock selection was RED with `3 failed`.

```powershell
python -m pytest tests/unit/test_agent_routes.py tests/unit/test_chat_application.py tests/unit/test_usage.py tests/integration/test_chat_api.py tests/integration/test_request_limits.py tests/evaluation/test_metrics.py -q
```

- GREEN: exit `0`, `199 passed`, one existing Starlette/httpx deprecation warning.

The post-fix two-axis review found two further partial boundaries: the legacy intent factory still enabled hidden SDK retries, and API/provider/application/network rejection logs did not always carry a reconstructable intent. The intent/retry selection was RED with `3 failed`, then GREEN with `3 passed`; provider/AppError/unknown/confirmation/pre-authentication fallback logging was RED with `5 failed`, then GREEN with `5 passed`. Confirmation exceptions now carry the claimed pending intent, API fallbacks use that value before a local deterministic fallback, and pre-authentication rejection is explicitly `not_evaluated`. The combined agent/usage/API/evaluation selection finished `184 passed`.

### Correlated logs and provider latency

- Final chat results log stage, intent, stable error code and whether a trip was saved.
- Supabase trip/share/usage operations emit one safe `database_result` with operation, success/failure, request ID and hashed subject inherited from the request context; no UUID, token, query payload or raw exception body is logged.
- Weather and Places start concurrently. The complete provider bundle, including booking links, has a lock-protected monotonic five-minute cache that is reused across request-scoped applications.

```powershell
python -m pytest tests/unit/test_usage.py -k "logs_correlated_rpc" -q
```

- RED: exit `1`, missing `correlation_context`/database result logging.
- GREEN: exit `0`, `1 passed, 26 deselected`.

Provider TDD evidence:

- Concurrent start RED: exit `1`; GREEN: `1 passed`.
- TTL/cache boundary RED: exit `1` (`cache_ttl_seconds` absent); GREEN: `1 passed`.
- Cross-request composition reuse RED: exit `1` (different aggregator identity); GREEN: `1 passed`.
- Combined Provider plus production HTTP verification: `19 passed, 1 warning`.

## Documentation and public-release truthfulness

- Removed the unused `langgraph` dependency and changed current architecture documentation to the implemented deterministic workflow. Historical plan/spec files remain historical records.
- Evaluation documentation no longer presents fixture extraction as deployed production composition and no longer lists stale known failures.
- Release documentation now reflects authenticated private CRUD, migrations `001` through `006`, minute limits and configurable cost rates.
- `docs/deployment/release-evidence.md` is intentionally `BLOCKED`: public URL, deploy ID, deployed SHA, online migrations/smoke and `v0.1.0` are not supplied. Only real external evidence from one deployed commit can change it to READY.
- Personal absolute paths were removed from tracked engineering reports; repository-relative commands remain.

## Fresh completion gates

```powershell
python -m pytest -q
```

Result: exit `0`, `466 passed`, one existing Starlette/httpx deprecation warning.

```powershell
node --test tests\frontend\app.test.js
```

Result: exit `0`, `18 passed`.

```powershell
python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation
```

Result: exit `0`; 80 cases, no failures or failed thresholds, and every reported accuracy/validity/coverage/success metric was `1.0` (`unsupported_fact_rate` was `0.0`). Production composition success was `1.0`; all six steps passed, P50/P95 were `0.383/0.673 ms`, and paid-model calls/input tokens/output tokens/cost were `0/0/0/0` under the explicitly offline seam.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
```

The fresh scanner was first RED because a test fixture used a credential-shaped DeepSeek assignment. The fixture was changed to a split-name, allow-listed placeholder (`tests/unit/test_usage.py`: `32 passed`), then the scanner was rerun.

Result: exit `0`, `Public repository check passed`.

## Remaining blocker

External release evidence is still `BLOCKED`. Completing it requires authority and access to deploy Supabase migrations and the Render service, run online authenticated/RLS smoke tests, record the real public URL/deploy ID/commit, then create and push `v0.1.0`. None of those external mutations were performed in this fix wave.
