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

## Review fix round 2

- Intent classification now calls the same guarded `ModelGateway` as extraction and planning, so intent-only and smalltalk model calls are counted and blocked by an open circuit.
- Removed guard-side stale usage reads before atomic reservation. The local repository cleans expired reservations inside its reservation lock and preserves the atomic failure reason.
- Production session secrets now require base64url-decoded high entropy (at least 32 bytes), rejecting whitespace, placeholders, repetition, and low-diversity values. Provider configuration now reflects the actual DeepSeek key in every environment.

### Focused verification

- `tests/unit/test_intent.py tests/unit/test_usage.py`: `16 passed in 1.16s`.

### Round 2 completion

- Added `tests/unit/test_usage.py::test_service_role_repository_uses_reservation_rpc_contract`, which asserts the exact `reserve_ai_usage`, `commit_ai_usage`, and `rollback_ai_usage` RPC names plus reservation ID and global-limit parameters.
- `tests/conftest.py` supplies an explicit test-only DeepSeek key to the offline fake model seam. Production wiring fixtures now use a real compliant base64url 32-byte session secret; this does not relax production validation.
- Fresh focused command: `python -m pytest tests/unit/test_usage.py tests/unit/test_intent.py -q` → `17 passed in 1.27s`.
- Fresh full command: `python -m pytest -q` → `147 passed, 1 warning in 1.78s` (existing Starlette/httpx deprecation warning).

## Review fix round 3

- `UsageRepository.reserve` now returns immutable `ReserveResult(reservation_id, failure_reason)`. `UsageGuard` maps only that operation-local reason; it has no shared failure state or stale read-after-unlock classification.
- The service-role adapter requires a structured reserve response (`allowed`, `reservation_id`, `reason`) and the contract test covers reserve, commit, and rollback IDs and parameters.
- Production session secrets accept only canonical unpadded base64url that decodes to at least 32 high-diversity bytes and round-trips byte-for-byte. Whitespace, padding, non-alphabet characters, placeholder/repeated patterns, and weak decoded byte strings are rejected.

### Verification

- RED: the new reserve-result tests failed to import `ReserveResult` before the refactor.
- Focused: `python -m pytest tests/unit/test_usage.py tests/unit/test_config.py -q` → `20 passed in 0.07s`.
- Full: `python -m pytest -q` → `154 passed, 1 warning in 1.76s`.

## Review fix round 4

- `reserve_ai_usage` now returns one JSONB protocol on every branch: the exact `allowed`, `reservation_id`, and `reason` fields carry an allowed UUID, `user_limit`, or `global_limit` result while retaining the advisory transaction lock and expired-reservation cleanup.
- `SupabaseUsageRepository` normalizes the real dict/single-row-list response shapes, validates the complete state and canonical UUID strictly, and maps malformed, unknown, or failed RPC responses to the stable fail-closed `AI_UNAVAILABLE` signal.
- Production session secrets now reject any exact periodic repetition, decoded common placeholders, and empirical Shannon total entropy below 128 bits after canonical base64url decoding. A fresh `secrets.token_urlsafe(32)` remains accepted.
- Contract coverage asserts the migration's JSONB shape (and fails against the former `returns text` function), exact reserve/commit/rollback RPC payloads, all three reserve outcomes, malformed/unknown responses, RPC exceptions, and service-role production wiring.

### Verification

- RED: `python -m pytest tests/unit/test_usage.py tests/unit/test_config.py -q` produced `13 failed, 22 passed`; failures covered the old text SQL protocol, unsafe response parsing, RPC exception leakage, and the three weak-secret classes.
- Focused GREEN: `python -m pytest tests/unit/test_usage.py tests/unit/test_config.py -q` produced `35 passed in 0.10s`.
- Full: `python -m pytest tests -q` produced `169 passed, 1 warning in 1.80s` (the existing Starlette/httpx deprecation warning).
