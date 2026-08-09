# Segment E completion report

Date: 2026-08-08

Base commit: `4a78f8a test: parse private policy targets`

## Scope

- Expanded the tracked-file public repository gate for additional local/build paths, multi-language sensitive assignments, raw secret tokens, and legacy Supabase JWTs.
- Kept a narrow allow-list for reviewed `.superpowers/sdd/<date-task>/<name>-report.md` engineering reports; every allowed report is still content-scanned.
- Added a semantic CI assertion that the public repository gate uses `if: always()`.
- Reset `OfflineExtractor.last_invalid_fields` for each message so stale invalid input cannot affect a later refusal decision.
- Aligned `.gitignore`, README, and free-tier deployment documentation with the public repository policy.

## Fresh completion-gate evidence

All commands ran from the repository root on 2026-08-08.

1. Deployment and scanner integration tests

   Command: `python -m pytest tests/integration/test_deployment_config.py -q`

   Result: `36 passed, 1 warning in 17.74s`.

2. Real tracked-repository scan

   Command: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_public_repo.ps1`

   Result: `Public repository check passed`.

3. Full Python suite

   Command: `python -m pytest -q`

   Result: `367 passed, 1 warning in 47.04s`.

4. Browser JavaScript tests

   Command: `node --test tests/frontend/app.test.js`

   Result: `16 passed, 0 failed`.

5. Fixed 80-case offline evaluation

   Command: `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation`

   Result: exit code `0`, 80 cases, no case failures, no failed thresholds, overall/task/fallback success `1.0`, unsupported-fact rate `0.0`. The single `agent_failed` log line is produced by an intentional fault-injection fixture and is not an evaluation failure.

6. Diff integrity checks

   Command: `git diff --check`

   Result: no whitespace errors. `tests/evaluation/cases.jsonl` and `tests/evaluation/baseline.json` have no changes, so evaluation cases, thresholds, and baseline were not weakened.

The Python warning is the pre-existing Starlette `TestClient` / `httpx` deprecation warning from the installed test dependencies.

## Self-review

- The scanner only evaluates paths returned by `git ls-files`, matching the documented publication boundary.
- The `.superpowers` exception is path-anchored to dated SDD report files and does not bypass content scanning.
- Colon-style assignment scanning is restricted to configuration/code extensions so Python type annotations and Markdown prose are not treated as credential assignments.
- Token patterns are constructed and tested without embedding usable credentials in the repository.
- CI behavior is tested by locating the actual scanner step and asserting its parsed `if` value, rather than searching workflow text.
- Evaluation state is cleared per raw message; no evaluation fixture, case answer, baseline, or threshold changed.

The initial self-review found no blocking issue; the later independent review findings and their resolution are recorded below.

## Residual risks

- Secret scanning is intentionally heuristic; future provider token formats require adding a regression test and detector.
- The 80-case evaluation is deterministic and offline, so it does not validate live provider availability or live response drift.
- GitHub Actions itself was not executed locally; the workflow contract is covered by integration tests and must still pass after push.
- The dependency deprecation warning should be handled in a later dependency-maintenance change, not in this segment.

## Independent-review fix round 1

Date: 2026-08-08

Reviewed base: `508facd fix: harden public release gates`

### Findings resolved

- Replaced prefix-based placeholder exemptions with a complete, explicit placeholder allow-list. A value that merely begins with a placeholder word is now treated as a credential.
- Extended assignment recognition to dotted receivers used by JavaScript and similar languages, while separating safe environment references from literal values.
- Added TypeScript declaration awareness: colon-style members inside active `interface` and object-type declarations are not runtime credential assignments.
- Replaced the CI text-presence assertion with a parsed workflow contract. It requires one exact PowerShell scanner invocation, `if: always()`, and placement after both the Python suite and offline evaluation.
- Moved per-message invalid-field observation reset behind `OfflineExtractor.begin_message()`, keeping extractor state ownership inside the extractor.
- Preserved the narrow reviewed-report path exception and all raw-token detectors. Evaluation cases, baseline, and thresholds were not changed.

### TDD evidence

The first focused run produced exactly six expected failures and 38 passes: scanner command spoofing, scanner reordering, dotted-property secret assignment, safe JavaScript environment reference, placeholder-prefix bypass, and TypeScript interface declaration. Removal of the scanner step and removal of `if: always()` were already rejected by the extracted contract.

After the fixes, the same focused deployment/scanner suite passed: `44 passed, 1 warning in 16.93s`.

### Fresh completion-gate evidence

- Evaluation unit tests: `43 passed in 1.43s`.
- Real tracked-repository scan: `Public repository check passed`, exit `0`.
- Full Python suite: `375 passed, 1 warning in 45.69s`.
- Browser JavaScript tests: `16 passed, 0 failed`.
- Fixed 80-case offline evaluation: exit `0`; all accuracy/success metrics `1.0`, unsupported-fact rate `0.0`, no failed thresholds.
- `git diff --check`: no whitespace errors before report update; re-run as part of the final commit gate.

The single Python warning remains the pre-existing Starlette `TestClient` / `httpx` deprecation warning. GitHub Actions still requires its normal post-push run; local tests validate the workflow structure and controlled failure mutations.

## Independent-review fix round 2

Date: 2026-08-08

Reviewed range: `508facd...8a13478`

### Findings resolved

- TypeScript declaration exemptions now use the declaration's actual balanced-brace span. A runtime object after a closed interface is scanned normally rather than inheriting the earlier type exemption.
- Assignment matching now locates only the sensitive key and separator. A small expression scanner then reads the complete assigned expression through whitespace and quoted text until a real outer delimiter or comment.
- A safe-reference exemption therefore applies only when the entire expression is one approved environment reference. Logical fallbacks or any trailing literal make the expression non-safe and are rejected.
- All earlier scanner and CI mutation regressions remain in the focused suite. Evaluation cases, baseline, and thresholds remain unchanged.

### TDD and completion evidence

- RED: the first focused run had exactly the two new expected failures and 44 passes: a runtime literal following a TypeScript interface, and a safe environment reference followed by a literal fallback.
- GREEN: focused deployment/scanner suite `46 passed, 1 warning in 18.58s`.
- Real tracked-repository scan: `Public repository check passed`, exit `0`.
- Full Python suite: `377 passed, 1 warning in 47.07s`.
- Browser JavaScript tests: `16 passed, 0 failed`.
- Fixed 80-case offline evaluation: exit `0`; all accuracy/success metrics `1.0`, unsupported-fact rate `0.0`, no failed thresholds.

The warning and post-push GitHub Actions risk are unchanged from round 1.

## Independent-review fix round 3

Date: 2026-08-08

### Finding resolved

- The assigned-expression reader no longer treats every newline or JavaScript line comment as an expression boundary. It skips whitespace and comments only to detect a continued logical operator (`||`, `&&`, or `??`); otherwise they remain safe trailing boundaries.
- Consequently, a safe environment reference is accepted only when the full logical expression contains no continuation. A literal fallback on a later line, including one after a line comment, is read and rejected.
- Existing single-line environment references, TypeScript declaration spans, raw-token detection, and CI workflow mutations remain covered by the focused suite. Evaluation cases, baseline, and thresholds are unchanged.

### TDD and completion evidence

- RED: two new multiline fallback regressions failed while the prior 46 focused tests passed.
- GREEN: focused deployment/scanner suite `48 passed, 1 warning in 19.63s`.
- Real tracked-repository scan: `Public repository check passed`, exit `0`.
- Full Python suite: `379 passed, 1 warning in 49.10s`.
- Browser JavaScript tests: `16 passed, 0 failed`.
- Fixed 80-case offline evaluation: exit `0`; all accuracy/success metrics `1.0`, unsupported-fact rate `0.0`, no failed thresholds.

The dependency warning and post-push GitHub Actions verification remain the only residual release risks.

## Independent-review fix round 4

Date: 2026-08-08

### Finding resolved

- The assigned-expression reader now treats JavaScript/TypeScript continuation punctuators and keyword operators as expression continuations after a safe JavaScript environment reference. Multiline concatenation, concatenation after a line comment, and ternary fallback expressions can no longer be truncated into an apparently safe reference.
- Continuation detection is selected from the expression itself rather than the tracked file extension. JavaScript snippets inside reviewed engineering reports receive the same protection as source files, while YAML list syntax and PowerShell invocations after complete non-JavaScript assignments remain valid boundaries.
- A safe-reference exemption still applies only when the complete assigned expression is exactly one approved environment reference. Existing logical fallback, object-property, TypeScript declaration-span, raw-token, and CI workflow checks remain covered. Evaluation cases, baseline, and thresholds are unchanged.

### TDD and completion evidence

- RED: the requested multiline concatenation, commented concatenation, and ternary fallback cases failed while the prior logical-OR cases passed. Controlled reproductions also captured two real-repository false positives and one fenced JavaScript report bypass before their fixes.
- GREEN: focused deployment/scanner suite `56 passed, 1 warning in 22.52s`.
- Real tracked-repository scan: `Public repository check passed`, exit `0` before this report update; the report is scanned again in the final commit gate.
- Full Python suite: `387 passed, 1 warning in 50.67s`.
- Browser JavaScript tests: `16 passed, 0 failed`.
- Fixed 80-case offline evaluation: exit `0`; 80 cases, overall/task/fallback success `1.0`, unsupported-fact rate `0.0`, and no failed thresholds.

The single Python warning remains the pre-existing Starlette `TestClient` / `httpx` deprecation warning. GitHub Actions still requires its normal post-push run.

## Independent-review fix round 5

Date: 2026-08-08

### Finding resolved

- JavaScript continuation scanning now skips the complete .NET Unicode whitespace set plus BOM before deciding whether an approved environment reference continues into another expression.
- LF, CR, Unicode line separator, and Unicode paragraph separator are all treated as JavaScript line terminators. A line comment therefore cannot hide a following concatenation or fallback behind either Unicode terminator.
- Eight parameterized regressions cover form feed, vertical tab, NBSP, BOM, a block comment followed by NBSP, form feed before logical OR, NBSP before a ternary, and a line comment ending at the Unicode line separator. The existing complete single-reference case remains accepted.
- The change is limited to the scanner and its integration regressions. CI semantics, TypeScript declaration spans, raw-token detection, evaluation cases, baseline, and thresholds are unchanged.

### TDD and completion evidence

- RED: all eight new regressions were incorrectly accepted by the previous scanner while the prior 56 focused cases passed. A single-case run confirmed the failure was the scanner returning success for a form-feed continuation.
- GREEN: focused deployment/scanner suite `64 passed, 1 warning`.
- Real tracked-repository scan: `Public repository check passed`, exit `0` before and after this report update.
- Full Python suite: `395 passed, 1 warning in 61.37s`.
- Browser JavaScript tests: `16 passed, 0 failed`.
- Fixed 80-case offline evaluation: exit `0`; 80 cases, overall/task/fallback success `1.0`, unsupported-fact rate `0.0`, and no failed thresholds.

The single Python warning remains the pre-existing Starlette `TestClient` / `httpx` deprecation warning. GitHub Actions still requires its normal post-push run.
