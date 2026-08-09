# Release blocker fix report

Date: 2026-08-09

Base: `1b9dc47`

Scope was limited to the one Critical and three Important findings in
`final-fix-rereview.md`. No Voyage frontend work, external deployment, tag,
online migration, or online smoke test was performed.

## Root causes and closures

1. The public scanner's computed-property grammar accepted only single- and
   double-quoted static keys and did not allow JavaScript comments inside the
   final brackets. The grammar now recognizes single quotes, double quotes,
   static template-literal keys, block comments, and line comments as
   JavaScript trivia around the final key. Dynamic template expressions remain
   outside the allow-list.
2. Failed model-usage settlement left a durable reservation, but expiration
   removed its slots from `pending`. That allowed later requests to reserve the
   same global capacity. Expired unsettled reservations now retain both
   worst-case slots in the in-memory reference implementation and the Supabase
   RPC contract. A late verified settlement can still replace the two held
   slots with the actual call count. Successful and non-planned chat results
   expose a bounded warning when settlement is unknown instead of silently
   presenting it as fully settled.
3. Modification input filtering recognized labelled credentials but not raw
   provider-token shapes. Raw DeepSeek-style and Supabase secret-token shapes
   are now rejected before intent/extraction/provider/planner forwarding.
4. Deployment instructions now require two authenticated accounts for private
   CRUD ownership/RLS smoke testing and explicitly require anonymous private
   CRUD to fail. Public anonymous access remains limited to the controlled
   share RPC.

## TDD evidence

Initial focused RED:

```text
7 failed, 3 passed
```

The failures covered two computed-property scanner inputs, two raw provider
token inputs, expired unsettled global quota, the SQL fail-closed contract, and
the user-visible settlement state. A second RED isolated the non-planned result
path:

```text
1 failed
```

Focused GREEN:

```text
10 passed
1 passed
```

The final affected-module regression (scanner/deployment, Agent routes, usage,
and chat application) finished with `205 passed` and the same existing
Starlette/httpx deprecation warning.

## Fresh verification

- Full Python suite: `472 passed`, one existing Starlette/httpx deprecation
  warning, exit `0`.
- Frontend Node suite: `18 passed`, exit `0`.
- Offline fixed evaluation corpus: `80` cases; every threshold passed, no known
  failures, overall `1.0`; production-composition offline seam `6/6`.
- Real tracked-repository scanner: `Public repository check passed`, exit `0`.
- `git diff --check`: no whitespace errors.

## Deliberate trade-off

Fail-closed accounting can temporarily deny additional AI planning after a
settlement outage because the full two-slot reservation remains charged for the
rest of the UTC day unless a late settlement succeeds. This is intentional: it
prefers a bounded availability loss over exceeding the configured daily paid
model-call ceiling.

## External release status

External release evidence remains **BLOCKED**:

- no verified public HTTPS URL, Render deploy ID, or deployed commit SHA;
- Supabase migrations `001` through `006` have not been verified online;
- online health, authenticated plan/modify/explain/reopen, and cross-user RLS
  smoke tests have not run;
- `v0.1.0` has not been created or pushed.

None of these states was inferred from local tests or replaced with placeholder
evidence.
