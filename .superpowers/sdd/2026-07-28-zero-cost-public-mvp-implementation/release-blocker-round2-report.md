# Release blocker round 2 report

Date: 2026-08-09

Base: `a56dcce`

Scope was limited to the remaining Critical scanner bypass and Important
UTC-crossing model-call quota finding in `release-blocker-rereview.md`. No
Voyage frontend work, external deployment, tag, online migration, or online
smoke test was performed.

## Root causes and closures

1. The public scanner compared static template keys only by source spelling,
   allowed whitespace but not JavaScript comment trivia at several computed
   assignment token seams, and could treat an unresolved dynamic template as
   compatible with the safe-reference allow-list. It now recognizes cooked
   ASCII template escapes, consumes block and line comments at every supported
   computed-property seam, and rejects unresolved dynamic template assignments
   fail-closed.
2. A model-call reservation fixed its UTC date at request admission and final
   settlement trusted that old date. The provider boundary had no per-attempt
   admission, so calls after midnight could be charged to the previous day and
   exceed the new day's physical-call ceiling. Each `ModelGateway` attempt now
   obtains one atomic slot immediately before `client.invoke()`, using the
   actual UTC invoke day. Same-day attempts convert one pending slot into one
   call; cross-day attempts first enforce the target day's user/global ceiling
   and then atomically transfer the slot.
3. Supabase migration `007_actual_invoke_day_quota.sql` mirrors the in-memory
   contract. It records incurred calls before provider execution, locks both
   affected UTC dates in stable order, keeps settlement fail-closed, and makes
   commit/rollback release only unused slots without adding calls twice.

## TDD evidence

Scanner RED:

- primary cooked-template/comment matrix: `7 failed, 7 passed`;
- nested dynamic-template probe: `1 failed, 1 passed`.

Scanner focused GREEN:

- `15 passed`, covering cooked static keys, block/line comment seams, and
  simple/nested unresolved dynamic templates.

Quota RED:

- the serial midnight reproduction failed because two calls made on the new
  UTC day were recorded on the reservation day;
- explicit pre-invoke reservation binding and full-call-day rejection both
  failed before implementation.

Quota focused GREEN:

- `85 passed`, covering the usage core, application production seam, Supabase
  adapter/RPC contract, RLS migration audit, concurrent midnight admission,
  settlement failure, expiry, and late settlement.

## Fresh verification

- Full Python suite: `494 passed`, one existing Starlette/httpx deprecation
  warning, exit `0`.
- Frontend Node suite: `18 passed`, exit `0`.
- Fixed offline evaluation corpus: `80` cases, every release threshold passed,
  no known failures, overall `1.0`.
- Production-composition offline seam: `6/6` steps passed.
- Real tracked-repository scanner: `Public repository check passed`, exit `0`.
- `git diff --check`: no whitespace errors.
- Independent read-only scoped review: no Critical, Important, or Minor issue;
  local scanner and invoke-day quota blockers are ready to close.

## Deliberate fail-closed behavior and residual risk

- An unresolved dynamic JavaScript computed-template assignment is rejected
  even when it might be harmless at runtime. This conservative false-positive
  trade-off is intentional for a public repository credential gate.
- If an admission RPC succeeds but its response is lost, the slot remains
  consumed while the provider is not called. This bounded availability loss is
  intentional and prevents uncertain state from exceeding the paid-call cap.
- Migration `007` has local behavior and SQL-contract evidence only. It must be
  applied after migration `006` and verified against the real Supabase project
  before release.

## External release status

External release evidence remains **BLOCKED**:

- no verified public HTTPS URL, Render deploy ID, or deployed commit SHA;
- Supabase migrations through `007` are not verified online;
- online health, authenticated plan/modify/explain/reopen, cross-midnight quota,
  and cross-user RLS smoke tests have not run;
- `v0.1.0` has not been created or pushed.

None of these external states was inferred from local tests or replaced with
placeholder evidence.
