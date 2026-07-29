# Task 8 report — AI usage guard and safe provider failures

## Delivered

- Added server-owned usage subjects: verified users use their authenticated ID and anonymous users use the hash of a signed server cookie. Request bodies never select the billing subject.
- Added lock-protected local/test atomic reservations with pending/commit/rollback accounting, user/global daily limits, a manual `AI_ENABLED` kill switch, and real input/output token fields.
- Added `002_ai_usage_reservations.sql`: production service-role RPC functions reserve under a Postgres advisory transaction lock and expose commit/rollback/read RPCs. `SupabaseUsageRepository` is the production default; the service key remains internal to that adapter.
- Added stable 429/5xx classification, a fail-closed circuit breaker primitive, safe 429/503 route mapping, and a 200 warning degradation path. Raw upstream bodies, prompts, JWTs, and keys are not returned or logged.
- Preserved the existing normal `/api/chat` response shape. Missing DeepSeek configuration fails before constructing a model client and resolves through the existing safe agent fallback.

## TDD evidence

- RED: focused run initially failed at collection with `ModuleNotFoundError: app.core.usage`.
- GREEN focused: `10 passed, 1 warning in 1.35s` for `test_usage.py` and `test_error_mapping.py`.
- Full regression before the final circuit/RPC addition: `143 passed, 1 warning in 1.65s`. (The persistent warning is the existing Starlette/httpx deprecation.)

## Concern

- The SQL RPC adapter is covered by the same strict RPC parameter shapes in code, but this offline suite cannot execute Supabase/Postgres. Apply `002_ai_usage_reservations.sql` before production deployment.
