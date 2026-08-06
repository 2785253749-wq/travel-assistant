# Segment D report: bounded input and server-trusted persistence

## Status and commit

- Status: complete; Segment E was not started.
- Authorized baseline: `fb855144643ca7f0d897fccb1f3ded5856a440f7`.
- Implementation commit: `a610c0e` (`fix: enforce server-trusted persistence boundaries`).
- The pre-existing untracked `docs/work-log-2026-07-30.md` was preserved and was not committed.
- No evaluation case, baseline, expected answer, or threshold changed.

## Implemented changes

1. Request and schema bounds
   - Added a 64 KiB pure-ASGI request-body middleware. It counts the actual bytes from every HTTP method, does not trust `Content-Length`, and replays accepted multi-chunk bodies without private Starlette request attributes.
   - Added strict upper bounds to `TravelProfile`, raw profile, itinerary fields/items, and every Pydantic list. The resource bound for `travelers` is 100; the existing product bound of 1–6 remains in `validate_profile()` so its stable issue-code contract is preserved.
   - All public JSON request models reject unknown fields. Validation and 413 responses remain sanitized and correlated by request ID.

2. Server-trusted save and copy
   - Browser code no longer POSTs/PATCHes an itinerary. Authenticated confirmation is already persisted by the server; history copy calls `POST /api/trips/{trip_id}/copy` without a client itinerary.
   - `Trip.itinerary` and the repository seam now use validated `Itinerary` objects. Status/itinerary consistency is enforced before persistence, copies are revalidated and do not alias the source, and public/shared rows are validated before exposure.
   - Client PATCH accepts only title. Profile, status, itinerary, owner, and unknown fields are rejected.
   - Invalid legacy Supabase trip rows fail closed: `get` returns unavailable and `list_for_user` skips only the invalid row instead of failing the entire history request.

3. Dependency inversion
   - `TripService` depends only on the trip repository protocols and domain schemas; FastAPI, settings, and infrastructure adapter creation moved to `app/composition.py`.
   - `core/usage.py` no longer imports settings, Supabase, or its concrete adapter. `SupabaseUsageRepository` moved to infrastructure and is constructed in composition.

4. RLS contract
   - The contract test parses every policy block independently, including quoted and unquoted policy names, then checks each private-table policy for its own `FOR ALL`, owner `USING`, and owner `WITH CHECK` clauses.
   - A regression fixture proves that an additional unquoted weak policy is not silently skipped.

## TDD and review evidence

- The resumed partial implementation initially had 53 focused Python tests and 16 Node tests green. The first full run exposed one compatibility regression: `313 passed, 1 failed`; schema-level `travelers <= 6` had bypassed the stable product-validation issue path. The final resource bound was corrected to 100 while retaining the product validator.
- Response-list limit tests were observed RED as `3 failed, 6 passed`, then GREEN as `9 passed`.
- Review regressions were observed RED for oversized DELETE/GET bodies and an unquoted policy (`3 failed, 5 passed`), for the new pure-ASGI interface (`1 failed`), and for a legacy invalid trip row (`1 failed`).
- Focused review-fix command:

```powershell
$env:PYTHONPATH='D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp\.venv\Lib\site-packages'
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/integration/test_request_limits.py tests/integration/test_rls_contract.py tests/unit/test_trip_production_wiring.py -q
```

Result: exit `0`, `15 passed`, one pre-existing Starlette/httpx deprecation warning.

- Standards review initially reported private `request._body` replay and invalid historical-row fan-out. Spec review initially reported method-based body bypass and unquoted policy parsing. After fixes, both reviewers explicitly marked all four findings closed.

## Final verification

```powershell
$env:PYTHONPATH='D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp\.venv\Lib\site-packages'
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q
```

Result: exit `0`, `322 passed`, one pre-existing Starlette/httpx deprecation warning, `43.51s`.

```powershell
node --test tests/frontend/app.test.js
```

Result: exit `0`, `16 passed`, `0 failed`.

```powershell
git -c safe.directory='D:/Users/Asus/Desktop/旅行助手/.worktrees/zero-cost-public-mvp' diff --cached --check
```

Result before implementation commit: exit `0`, no whitespace errors. The staged path list contained no evaluation case or baseline file.

## Remaining risks

- The installed FastAPI test shim emits one known Starlette/httpx deprecation warning; it does not affect current behavior or test results.
- Request bodies are intentionally buffered only up to 64 KiB before dispatch. This MVP therefore does not support large uploads or streaming request bodies.
- Legacy invalid trip rows are hidden fail-closed rather than repaired automatically. If a deployed database later contains such rows, repair should be performed with a separate audited data migration.
