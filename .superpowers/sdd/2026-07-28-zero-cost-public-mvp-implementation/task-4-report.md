# Task 4 report

## Delivered

- Private trip create, list, read, update and delete routes use `CurrentUser` only.
- Trip lookups always include both verified `user_id` and `trip_id`; a non-owner receives `TRIP_NOT_FOUND` / HTTP 404.
- Request bodies cannot set the trip owner because route models ignore unrecognised fields and service ownership is supplied separately.
- Share tokens use `secrets.token_urlsafe(32)`, are persisted only as SHA-256 hashes, default to 30 days, and can be revoked.
- `GET /api/shared/{token}` is the sole anonymous route and returns an explicit allowlist: id, title, status, profile, itinerary and updated_at. It excludes owner and conversation fields.
- Added in-memory fake plus a Supabase DTO adapter. No external service or real secrets are required by tests.
- Preserved `/api/chat` unchanged.

## TDD evidence

Initial test invocation using `python` could not run because it was absent from PATH. The verified short-path command was then used:

```powershell
Set-Location W:\; & T:\.venv\Scripts\python.exe -m pytest tests/unit/test_trip_service.py tests/integration/test_trip_api.py -v
```

First executable focused run: 5 passed and 2 failed, demonstrating the missing persistent API test fixture wiring and optional share-body handling (`PATCH` returned 404 and `POST /share` returned 422). After the smallest fixes, the same focused command returned **7 passed** (one known Starlette/httpx deprecation warning).

Final verification:

```powershell
Set-Location W:\; & T:\.venv\Scripts\python.exe -m pytest -v
```

Result: **41 passed**, with the same one third-party deprecation warning.

## Concerns

- `get_trip_service()` remains intentionally in-memory to keep local and test execution offline. `SupabaseTripRepository` is provided as the DTO/persistence adapter, but production composition must supply a user-scoped Supabase client so RLS remains authoritative.
- No live Supabase integration was run, by design; the migration/RLS contract and no-network fakes cover the repository boundary.

## Fix round 1/5 (2026-07-29)

### Security and production wiring changes

- Production (or configured Supabase) private trip service construction now uses a fresh Supabase client scoped with the already-verified bearer JWT. The service key is never used by trip endpoints. Test/development without Supabase remains explicitly in-memory and FastAPI tests override both service dependencies.
- The anonymous shared endpoint now uses a dedicated public repository that can call only `get_shared_trip_by_token_hash`; it does not query `share_links` or `trips` directly.
- Added migration `002_secure_public_share_rpc.sql`: a `SECURITY DEFINER` function with fixed `pg_catalog, public` search path validates hash, revocation and expiry in one query and returns only the public allowlist. It revokes base-table access from `PUBLIC` and `anon`, grants authenticated users their RLS-protected CRUD privileges, grants only RPC execution to `anon`/`authenticated`, and supplies a trip `updated_at` trigger.
- Extended API isolation coverage to non-owner list, PATCH, DELETE, share creation and share revocation. The token-hash assertion now requires the exact SHA-256 digest.

### TDD and verification evidence

1. The initial red run failed during collection because `get_public_trip_service` did not exist.
2. After implementing the composition/RPC boundary, the focused suite initially had 9 pass / 2 fail: its migration privilege assertion and DELETE test invocation exposed remaining gaps. The privilege migration and test harness invocation were corrected.
3. A second red check for revoking `profiles` and `ai_usage` base-table permissions failed as expected; the migration was then tightened.
4. Final focused command:

```powershell
Set-Location W:\; & T:\.venv\Scripts\python.exe -m pytest tests/unit/test_trip_production_wiring.py tests/integration/test_share_rpc_contract.py tests/integration/test_trip_api.py tests/unit/test_trip_service.py -v
```

Result: **11 passed**, one existing third-party Starlette/httpx deprecation warning.

5. Full command:

```powershell
Set-Location W:\; & T:\.venv\Scripts\python.exe -m pytest -v
```

Result: **45 passed**, the same one third-party warning.

### Remaining concern

No live Supabase project was contacted. The JWT client composition and public RPC are verified with no-network fakes and migration contracts; deployment must apply migration 002 before enabling the Supabase-configured application.

## Fix round 2/5 (2026-07-29)

### Regression fixes

- Restored Supabase share-row deserialization after `create_share_link`, including UUIDs and timezone-aware timestamp fields. A fake Supabase insert response now exercises the production adapter path without network access.
- Development/test mode without Supabase now shares one credential-free `InMemoryTripRepository` between private and public service dependencies. The no-override API regression covers create trip, create share, and anonymous share read end to end.
- Removed `lru_cache` from both request-facing service dependencies. Every Supabase private request now constructs a new JWT-scoped repository/client, even when the same verified bearer is reused. Only the credential-free development repository has a bounded one-entry cache.

### TDD evidence

Focused RED command:

```powershell
Set-Location W:\; & T:\.venv\Scripts\python.exe -m pytest tests/unit/test_trip_production_wiring.py tests/integration/test_trip_api.py -v
```

Result before implementation: **3 failed, 5 passed**. The failures precisely reproduced JWT client reuse, the missing `_share_from_row`, and a development anonymous share read returning 404.

The same focused command after the minimal fixes returned **8 passed**, with one existing third-party Starlette/httpx deprecation warning.

Full verification command:

```powershell
Set-Location W:\; & T:\.venv\Scripts\python.exe -m pytest -v
```

Result: **48 passed**, with the same one third-party warning.

### Remaining concern

The Supabase adapter remains verified with representative fake responses and the RPC/migration contract rather than a live project. No bearer token, scoped client, service key, or other credential is retained in an application-level cache.
