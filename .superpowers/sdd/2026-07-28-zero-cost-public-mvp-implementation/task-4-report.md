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
