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
