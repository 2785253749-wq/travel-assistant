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

## Reviewer round 3: canonical dynamic-subject categories

### Finding and root cause

Reviewer found that round 2 compared raw matched subject strings rather than
the query's meaning. This allowed both
`明天酒店和机票价格多少：机票价格不用查` and
`明天车票价格多少：机票价格不用查`: the former shared the flight token even though
the hotel request remained unnegated, while the latter shared generic `票价`
text despite rail and flight being different requests. Conversely,
`明天航班价格多少：机票价格不用查` was refused because `航班` and `机票` are different
strings even though both identify the same flight request.

### RED

Added two real-boundary regression groups with literal messages:

- `test_opt_out_must_cover_every_requested_dynamic_category`: mixed
  hotel+flight and rail+flight requests, each with Chinese-colon and LF
  boundaries, must be refused.
- `test_flight_synonym_opt_out_exempts_the_same_dynamic_request`: flight
  `航班` requests with flight `机票` opt-outs, for Chinese-colon and LF
  boundaries, must remain allowed.

Command:

```text
python -m pytest tests/unit/test_agent_routes.py -k "cover_every_requested_dynamic_category or flight_synonym_opt_out" -q
```

Result: exit `1`; `6 failed, 53 deselected in 1.78s`.

The four insufficient-coverage cases returned `None`; the two valid flight
synonym cases returned `UNVERIFIABLE_REALTIME_REQUEST`, precisely matching the
review finding.

### GREEN

Added canonical dynamic categories: flight (`机票`/`航班`), hotel
(`酒店`/`住宿`/`房价`), rail (`车票`), and admission (`门票`). The raw
`_DYNAMIC_TRAVEL_SUBJECTS` list still recognizes dynamic requests, including
generic `票价`; generic price wording does not, however, establish category
equivalence for opt-out matching.

An explicit opt-out now applies only if its categories cover every category in
the request clauses. A subjectless opt-out applies only where the request has
one canonical category. An uncovered opt-out clause remains excluded from the
request window, so it cannot add dynamic terms to an unrelated request.

Focused command (identical selector):

```text
python -m pytest tests/unit/test_agent_routes.py -k "cover_every_requested_dynamic_category or flight_synonym_opt_out" -q
```

Result: exit `0`; `6 passed, 53 deselected in 1.24s`.

Relevant controls:

```text
python -m pytest tests/unit/test_agent_routes.py -k "cover_every_requested_dynamic_category or flight_synonym_opt_out or unrelated_trailing_opt_out or trailing_opt_out or adjacent_clauses or opt_out_only or lookup_opt_out or unverifiable_realtime_and_guaranteed_safety_requests or practical_safety_measures_and_explicit_price_lookup_opt_out or safety_precautions_and_ordinary_flight_planning or direct_safety_guarantees" -q
```

Result: exit `0`; `27 passed, 32 deselected in 1.26s`. This covers all prior
leading/trailing opt-outs, split signals, R001/R006/R014, practical safety
controls, and ordinary flight planning.

### Full verification

- `python -m pytest tests/unit/test_agent_routes.py -q` → exit `0`;
  `59 passed in 1.38s`.
- `python -m pytest -q` → exit `0`; `266 passed, 1 warning in 1.94s`.
  The warning is the existing Starlette/httpx deprecation warning.
- `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl
  --output build/evaluation` → exit `0`; 80 cases, every positive metric
  `1.0`, `unsupported_fact_rate: 0.0`, `failures: {}`,
  `failed_thresholds: []`, and `known_failures: []`.

The runner's `agent_failed` output remains E010's expected database-failure
fallback observation, not an evaluation failure.

### Scope confirmation

This review-fix round changed only `app/agent/safety.py`,
`tests/unit/test_agent_routes.py`, and this evidence report. Evaluation cases,
baseline, thresholds, runner, Task 11 assets, and the historical Task 10
report remain unchanged. The unrelated work log remains untracked and was not
modified.

## Reviewer round 2: reverse-order unrelated opt-out

### Finding and root cause

The first reviewer fix exempted every adjacent window containing an opt-out.
That made an opt-out for one travel subject suppress an already-complete,
unrelated request in the preceding clause. Both
`明天酒店价格多少：机票价格不用查` and its LF equivalent returned `None`: the
later flight opt-out suppressed the earlier realtime hotel-price request.

This violates clause-local opt-out semantics. A trailing opt-out with no
explicit subject, such as `明天机票：价格不用查，只帮我安排行程`, still inherits the
adjacent flight request and remains an exemption. An opt-out that explicitly
names a different dynamic travel subject cannot exempt that request.

### RED

Added `test_unrelated_trailing_opt_out_does_not_suppress_realtime_request`,
parameterized over Chinese-colon and LF boundaries. It invokes the real
`assess_message` boundary with literal messages and expects
`UNVERIFIABLE_REALTIME_REQUEST`.

Command:

```text
python -m pytest tests/unit/test_agent_routes.py -k "unrelated_trailing_opt_out" -q
```

Result: exit `1`; `2 failed, 51 deselected in 1.74s`.

Both tests failed for the reported behavior: `assess_message` returned `None`
instead of the realtime refusal.

### GREEN

The two-clause evaluator now separates opt-out clauses from request clauses.
It exempts the window when the opt-out names no dynamic travel subject (so it
inherits the adjacent request) or shares an explicit subject with the request.
When the opt-out names a different subject, it is removed from the request
window; it can neither exempt the unrelated request nor contribute the price
term that would create a false request. The window remains bounded to two
adjacent clauses.

Focused command (identical selector):

```text
python -m pytest tests/unit/test_agent_routes.py -k "unrelated_trailing_opt_out" -q
```

Result: exit `0`; `2 passed, 51 deselected in 1.28s`.

Relevant controls:

```text
python -m pytest tests/unit/test_agent_routes.py -k "unrelated_trailing_opt_out or trailing_opt_out or adjacent_clauses or opt_out_only or lookup_opt_out or unverifiable_realtime_and_guaranteed_safety_requests or safety_precautions_and_ordinary_flight_planning" -q
```

Result: exit `0`; `19 passed, 34 deselected in 1.34s`. This retains the
same-request trailing opt-out, `机票价格不用查：明天酒店价格多少` refusal, split
signals, R001, and ordinary flight planning.

### Full verification

- `python -m pytest tests/unit/test_agent_routes.py -q` → exit `0`;
  `53 passed in 1.41s`.
- `python -m pytest -q` → exit `0`; `260 passed, 1 warning in 2.01s`.
  The warning is the existing Starlette/httpx deprecation warning.
- `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl
  --output build/evaluation` → exit `0`; 80 cases, every positive metric
  `1.0`, `unsupported_fact_rate: 0.0`, `failures: {}`,
  `failed_thresholds: []`, and `known_failures: []`.

The runner's `agent_failed` output remains E010's expected database-failure
fallback observation, not an evaluation failure.

### Scope confirmation

This review-fix round changed only `app/agent/safety.py`,
`tests/unit/test_agent_routes.py`, and this evidence report. Evaluation cases,
baseline, thresholds, runner, Task 11 assets, and the historical Task 10
report remain unchanged. The unrelated work log remains untracked and was not
modified.
