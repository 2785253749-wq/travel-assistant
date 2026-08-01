# Task 1 report: realtime clause safety refactor

## Scope and base

- Base commit: `0b317a8ed5d16cfcbcd32bd2a2e98d93095fc3d2`.
- Production scope: `app/agent/safety.py` only.
- Regression scope: `tests/unit/test_agent_routes.py` only.
- The approved design and implementation plan are included with this task.
- The historical Task 10 report was read as evidence and deliberately not
  changed. The unrelated `docs/work-log-2026-07-30.md` was not read, changed,
  staged, or committed.

## Root cause

`_REQUEST_CLAUSE_SEPARATOR` correctly treats Chinese/ASCII colons and newlines
as hard clause boundaries so an opt-out cannot suppress a separate clause.
However, `_requests_realtime_dynamic_data` then required the realtime marker,
travel subject, and dynamic-data demand to occur inside one resulting clause.
Consequently, `明天机票：价格多少` and its newline equivalent became
`["明天机票", "价格多少"]`; neither clause contained all three signals, so both
requests were accepted even though each complete message asks for unverifiable
realtime data.

The refactor retains clause-local opt-out handling and evaluates each
non-opt-out clause together with at most its immediately following clause.
This restores signals separated by a hard boundary without making an opt-out
message-global or aggregating across more than one boundary.

## Strict TDD evidence

### RED

Added the approved direct safety-boundary regressions before changing
production code:

- `test_realtime_signals_across_adjacent_clauses_are_refused`, parameterized
  over Chinese-colon and LF boundaries.
- `test_opt_out_only_applies_to_its_own_clause`, covering both an allowed
  ordinary itinerary request and a separate hotel-price request that must be
  refused.

Command:

```text
python -m pytest tests/unit/test_agent_routes.py -k "adjacent_clauses or opt_out_only" -q
```

Result: exit `1`; `2 failed, 1 passed, 46 deselected in 1.76s`.

Both adjacent-clause parameter cases failed for the intended reason:
`assess_message(message).code` was `None` instead of
`UNVERIFIABLE_REALTIME_REQUEST`. The opt-out control test passed, proving the
failure was specifically the lost cross-boundary signal aggregation rather
than test setup or an existing opt-out regression.

### GREEN

Changed `_requests_realtime_dynamic_data` to split once, enumerate clauses,
skip a current clause containing `_DYNAMIC_LOOKUP_OPT_OUT`, and evaluate the
current clause plus its immediate successor as one signal window. No regex,
refusal term, separator, threshold, or public interface changed.

Focused command (identical selector):

```text
python -m pytest tests/unit/test_agent_routes.py -k "adjacent_clauses or opt_out_only" -q
```

Result: exit `0`; `3 passed, 46 deselected in 1.26s`.

## Required verification

### Changed-area suite

```text
python -m pytest tests/unit/test_agent_routes.py -q
```

Result: exit `0`; `49 passed in 1.28s`.

### Full suite

```text
python -m pytest -q
```

Result: exit `0`; `256 passed, 1 warning in 1.96s`.

The warning is the existing Starlette/httpx deprecation warning emitted from
the worktree's installed `fastapi/testclient.py`; this task did not change the
dependency environment.

### Fixed offline evaluation runner

```text
python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation
```

Result: exit `0`. The generated report records:

- `total_cases: 80`;
- every positive metric at `1.0` and `unsupported_fact_rate: 0.0`;
- `failures: {}`;
- `failed_thresholds: []`;
- `known_failures: []`.

The runner emitted the expected `agent_failed` observation for E010's
intentional database-failure fallback scenario; it did not represent a runner
failure.

## Test environment

The worktree launcher `.venv/Scripts/python.exe` exists but exited `101` because
it could not create the Python process from the workspace path. Following the
task instruction, commands used the existing Python 3.13.13 executable with
the worktree's existing `.venv/Lib/site-packages` supplied through a
process-local `PYTHONPATH`. No interpreter, virtual environment, or dependency
was installed or modified.

## Protected artifacts

- `tests/evaluation/cases.jsonl` was not changed; no raw case, expected value,
  allowed source, or threshold was changed.
- The evaluation baseline and runner were not changed.
- Task 11 CI, rendering, README, deployment, and public-repository assets were
  not changed.
- The historical Task 10 report and unrelated work log were not changed.

## Remaining concern

The window is intentionally bounded to the current clause and its immediate
successor. A request whose three semantic signals are spread across three or
more hard-delimited clauses remains outside this approved refactor.

## Reviewer round 1: trailing opt-out within an adjacent window

### Finding and root cause

Review identified that the first refactor checked `_DYNAMIC_LOOKUP_OPT_OUT`
only in the window's leading clause while aggregating realtime signals from the
leading clause and its successor. Therefore both `明天机票：价格不用查，只帮我安排行程`
and its LF equivalent aggregated the time marker and flight subject from the
first clause with the price term in the second clause, then incorrectly
returned `UNVERIFIABLE_REALTIME_REQUEST` despite the second clause explicitly
negating that dynamic lookup.

The approved design requires a refusal only when the complete aggregated
two-clause window has no explicit dynamic-query opt-out. The opt-out must not
become message-global: a later window that has no opt-out must still refuse an
independent realtime query.

### RED

Added `test_trailing_opt_out_exempts_its_adjacent_realtime_window`,
parameterized over the Chinese-colon and LF forms. It invokes the real
`assess_message` boundary and asserts `None`, using literal messages and no
mocks.

Command:

```text
python -m pytest tests/unit/test_agent_routes.py -k "trailing_opt_out" -q
```

Result: exit `1`; `2 failed, 49 deselected in 1.76s`.

Both cases failed for the intended production behavior: the result was
`UNVERIFIABLE_REALTIME_REQUEST` instead of `None`.

### GREEN

Moved the existing opt-out check from the leading clause to the same adjacent
window that provides the realtime signals. The loop remains bounded to the
current clause and immediate successor; no regex, separator, threshold, or
public interface changed. Consequently, an opt-out exempts only windows that
contain it, while `机票价格不用查：明天酒店价格多少` still reaches the next,
opt-out-free window and is refused.

Focused command (identical selector):

```text
python -m pytest tests/unit/test_agent_routes.py -k "trailing_opt_out" -q
```

Result: exit `0`; `2 passed, 49 deselected in 1.27s`.

Relevant controls:

```text
python -m pytest tests/unit/test_agent_routes.py -k "trailing_opt_out or adjacent_clauses or opt_out_only or lookup_opt_out or unverifiable_realtime_and_guaranteed_safety_requests" -q
```

Result: exit `0`; `14 passed, 37 deselected in 1.30s`. This includes the
existing split-signal requests, colon/newline separate-query refusal, the
existing opt-out controls, and R001's realtime refusal.

### Full verification

- `python -m pytest tests/unit/test_agent_routes.py -q` → exit `0`;
  `51 passed in 1.26s`.
- `python -m pytest -q` → exit `0`; `258 passed, 1 warning in 2.03s`.
  The warning is the existing Starlette/httpx deprecation warning.
- `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl
  --output build/evaluation` → exit `0`; 80 cases, every positive metric
  `1.0`, `unsupported_fact_rate: 0.0`, `failures: {}`,
  `failed_thresholds: []`, and `known_failures: []`.

The runner's `agent_failed` output remains E010's expected database-failure
fallback observation, not a failed evaluation.

### Scope confirmation

This review-fix round changed only `app/agent/safety.py`,
`tests/unit/test_agent_routes.py`, and this evidence report. Evaluation cases,
baseline, thresholds, runner, Task 11 assets, and the historical Task 10
report remain unchanged. The unrelated work log remains untracked and was not
modified.
