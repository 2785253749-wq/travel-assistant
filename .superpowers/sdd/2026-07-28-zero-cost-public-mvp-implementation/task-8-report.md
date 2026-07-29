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

## Review fix round 1

- All production DeepSeek invocations now pass through `ModelGateway`, including extraction and structured-planner repair. It records response usage metadata in a request-local collector, turns 429/5xx failures into stable codes, and refuses calls while its circuit is open.
- Chat reserves before entering the agent, settles with aggregate input/output metadata, and charges at least one input token per model call when metadata is absent. Provider failures roll back and return a safe warning without rerunning `chat` outside the guard.
- Reservations now carry server-generated IDs. The Supabase migration has active reservation records, five-minute expiry cleanup during atomic reserve, subject-scoped idempotent commit/rollback functions, and service-role-only grants. The in-memory test implementation mirrors ID settlement and expiry handling.
- Production settings require a non-empty 32-character `ANON_SESSION_SIGNING_SECRET`; dev/test retain an ephemeral process-local secret. Cookies are signed from that configuration rather than a module hard-code.

### Verification

- RED: the new model gateway test failed to import `ModelGateway` before the invocation boundary existed.
- Focused green: `17 passed, 1 warning in 1.44s` for chat and usage tests.
- Full: `146 passed, 1 warning in 1.80s`.
