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
& 'D:\Users\Asus\Desktop\旅行助手\.venv\Scripts\python.exe' -m pytest tests/unit/test_auth.py tests/integration/test_rls_contract.py -v
```

Result: failed at collection with `ModuleNotFoundError: No module named 'app.api'`, before the auth gateway and migration existed.

### Green

Command:

```powershell
& 'D:\Users\Asus\Desktop\旅行助手\.venv\Scripts\python.exe' -m pytest tests/unit/test_auth.py tests/integration/test_rls_contract.py -v
```

Result: `5 passed, 1 warning in 1.49s`.

## Full regression

Command:

```powershell
& 'D:\Users\Asus\Desktop\旅行助手\.venv\Scripts\python.exe' -m pytest -v
```

Result: `30 passed, 1 warning in 1.59s`.

## Concerns

- The test environment does not install the newly declared `supabase` package; the gateway imports it lazily, so authentication contract tests use the injected fake gateway without network access. Deployments must install `requirements.txt`.
- RLS tests validate the migration's SQL security contract statically. Applying the migration to a real Supabase project remains a deployment-time integration check.
- The single warning is pre-existing FastAPI/Starlette TestClient deprecation for the installed HTTP client, not a Task 3 behavior failure.
