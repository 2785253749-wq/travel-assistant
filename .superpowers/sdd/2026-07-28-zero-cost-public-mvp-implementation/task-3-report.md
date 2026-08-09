# Task 3 Report: Supabase authentication and RLS

## Implementation summary

- Added the initial Supabase migration for `profiles`, `trips`, `conversation_messages`, `share_links`, and `ai_usage`.
- All five tables have a `user_id` ownership boundary, RLS enabled, and an owner-only `auth.uid() = user_id` policy.
- `share_links.token_hash` is unique and no plaintext share token column or public-read policy exists.
- Added a lazy Supabase auth gateway using the anon key and a FastAPI dependency that derives `AuthenticatedUser` only from `auth.get_user(token)`.
- Added protected `/api/me` and placeholder `/api/trips` routes without altering the existing `/api/chat` contract.
- Added `supabase>=2.11` to runtime requirements plus authentication and migration security contract tests.

## TDD evidence

### Red

Command:

```powershell
python -m pytest tests/unit/test_auth.py tests/integration/test_rls_contract.py -v
```

Result: failed at collection with `ModuleNotFoundError: No module named 'app.api'`, before the auth gateway and migration existed.

### Green

Command:

```powershell
python -m pytest tests/unit/test_auth.py tests/integration/test_rls_contract.py -v
```

Result: `5 passed, 1 warning in 1.49s`.

## Full regression

Command:

```powershell
python -m pytest -v
```

Result: `30 passed, 1 warning in 1.59s`.

## Concerns

- The test environment does not install the newly declared `supabase` package; the gateway imports it lazily, so authentication contract tests use the injected fake gateway without network access. Deployments must install `requirements.txt`.
- RLS tests validate policy text and execute the parsed composite ownership constraint through an in-memory SQL projection. Applying the PostgreSQL migration to a real Supabase project remains a deployment-time integration check.
- The single warning is pre-existing FastAPI/Starlette TestClient deprecation for the installed HTTP client, not a Task 3 behavior failure.

## Review fix round 1

### Findings addressed

- Bearer parsing now completes before a real Supabase gateway is constructed. A request without Authorization returns `401 AUTH_REQUIRED` even when Supabase configuration and the client package are unavailable.
- `conversation_messages` and `share_links` now reference `trips` through `(trip_id, user_id)`, backed by a matching unique key on `trips`, so a child row cannot point at another user's trip.
- Only an explicit `InvalidAuthToken` maps to `401 AUTH_INVALID`. Network, upstream 5xx, malformed responses, missing configuration, and unknown gateway errors map to a stable `503 AUTH_UNAVAILABLE` without exposing upstream details.

### Red evidence

Command:

```powershell
python -m pytest tests/unit/test_auth.py tests/integration/test_rls_contract.py -v
```

Result before the fixes: `4 failed, 5 passed, 1 warning in 2.58s`. The failures were the unconfigured missing-token request returning 500, service failure returning 401, and both cross-user child inserts lacking a composite ownership constraint.

### Focused green evidence

The same command after the fixes returned `9 passed, 1 warning in 1.98s`.

### Full regression after review fixes

Command:

```powershell
python -m pytest -v
```

Result: `34 passed, 1 warning in 1.62s`.
