# Release evidence

Status: **BLOCKED — external deployment has not been verified**

This file deliberately records absence of evidence instead of substituting local tests for an online release.

| Evidence | Current value |
|---|---|
| Public HTTPS URL | Not supplied |
| Render deploy ID | Not supplied |
| Deployed commit SHA | Not supplied |
| Applied Supabase migrations | Not verified online |
| Online `/health` result | Not run |
| Online authenticated plan/modify/explain/reopen smoke | Not run |
| Online cross-user RLS smoke | Not run |
| Public `v0.1.0` tag | Not created |

Do not change the status to `READY` until every row has concrete, independently checkable evidence from the same deployed commit. A placeholder URL, localhost response, offline fixture report, CI result, or unpushed local tag is not online release evidence.

After deployment, record timestamps in UTC, redact all credentials and personal inputs, and link the exact release commit. Follow the smoke sequence in [free-tier.md](free-tier.md).
