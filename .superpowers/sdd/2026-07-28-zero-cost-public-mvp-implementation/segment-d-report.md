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

## 2026-08-08 independent-review fix round 1

### Status and commit

- Status: the four Segment D review findings were fixed; Segment E was not started.
- Implementation commit: `05c8b93` (`fix: close segment d review findings`).
- The pre-existing untracked `docs/work-log-2026-07-30.md` remains untracked and was not included in the implementation commit.
- No evaluation case, baseline, expected answer, or threshold changed.

### Review findings closed

1. The explicit `/api/chat` response now constructs and validates `ChatResponse` before creating `JSONResponse`. Citations use stable first-seen `evidence_id` deduplication and retain the first 100 unique entries; warnings retain the first 40 entries. Optional top-level fields remain omitted without removing `None` fields from the legacy nested profile shape.
2. A central 1–100-character `TripTitle` contract now matches the database check. Generated destination titles reserve five characters for `" trip"`; create, direct service update, copy, Supabase write/read, and public share projection all use the same validator. Invalid legacy titles fail closed in both get and list paths.
3. The RLS contract loads every `*.sql` migration in filename order and splits SQL outside line/block comments, single/double quotes, and dollar-quoted bodies. It tracks final CREATE/DROP policy state, rejects any later RLS disable, rejects weak owner policies, prohibits policies on service-role accounting tables, and fails closed on unmodeled private-table `ALTER POLICY` statements.
4. The pure-ASGI request-body replay remembers a terminal disconnect and returns `http.disconnect` on every later receive without calling an exhausted upstream receive again.

### RED evidence

```powershell
python -m pytest tests/integration/test_chat_api.py::test_chat_api_bounds_and_deduplicates_generated_citations_and_warnings tests/integration/test_request_limits.py::test_request_body_limit_middleware_replays_terminal_disconnect_after_buffering tests/unit/test_trip_service.py::test_trip_titles_are_bounded_for_long_destinations_and_updates tests/unit/test_trip_production_wiring.py::test_supabase_repository_isolates_legacy_invalid_trip_rows_on_reads -q
```

Result before production changes: exit `1`, `4 failed`. The observed values were 102 sources, two upstream disconnect reads, a 205-character generated title, and a non-null 101-character legacy title row.

```powershell
python -m pytest tests/integration/test_rls_contract.py -q
```

Initial result after adding the migration/parser regressions: exit `1`, `4 failed, 5 passed`. The original fixed-path loader omitted later migrations, quoted policy identifiers were skipped, and later quoted `DISABLE RLS`/weak policies were not rejected.

Additional boundary cycles were also observed RED before their implementations:

- Long-title save/share/list coverage: `3 failed`.
- Service-role table RLS plus validation-before-insert coverage: `3 failed, 2 passed`.
- Later private-table `ALTER POLICY`/`DROP POLICY` coverage: `2 failed, 4 passed`.

### GREEN and regression evidence

Focused transitions were observed GREEN as follows:

- `/api/chat` bounds/deduplication: `1 passed`.
- Terminal disconnect replay: `1 passed`.
- Long-title generation/update/share and legacy-row isolation: `3 passed`.
- Final RLS contract suite: `13 passed`.
- Complete chat API suite after preserving the nested legacy profile shape: `14 passed`.

Final Python verification:

```powershell
$env:PYTHONPATH='D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp\.venv\Lib\site-packages'
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q
```

Result: exit `0`, `335 passed`, one pre-existing Starlette/httpx deprecation warning, `40.00s`.

Final browser verification:

```powershell
node --test tests/frontend/app.test.js
```

Result: exit `0`, `16 passed`, `0 failed`, `140.2394ms`.

Repository checks:

```powershell
git diff --check
git diff --cached --check
```

Results: exit `0`; no whitespace errors. The implementation commit contains only the six production files and five corresponding test files listed by the staged diff, with no evaluation fixture or baseline path.

### Remaining risks after review fix

- The known Starlette/httpx deprecation warning remains unchanged.
- The offline RLS auditor deliberately rejects private-table `ALTER POLICY`; a future legitimate policy alteration must first add explicit, tested final-state semantics to the auditor.
- Citation and warning overflow is intentionally deterministic truncation, so lower-priority evidence beyond the public response bounds is not returned to the browser.
