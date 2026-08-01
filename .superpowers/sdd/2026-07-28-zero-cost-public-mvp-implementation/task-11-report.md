# Task 11 report: CI, free deployment and public-repository gate

## Scope and baseline

- Authorized starting commit: `cf33ea43315ad605c56ff69fceac1d9b82d646d6`.
- Branch/worktree: `agent/zero-cost-public-mvp` in the existing linked worktree.
- The pre-existing untracked `docs/work-log-2026-07-30.md` was not read, changed, staged or committed.
- Task 11 adds no real deployment, account, repository creation, network call or paid-model call. It supplies and verifies the deployment/release artifacts only.

## TDD evidence

### RED

The production changes that the tests were designed to catch were:

1. a Render service that does not use the free plan, platform `$PORT`, `/health`, or platform-managed secrets;
2. CI that omits the full pytest run, the independent 80-case release evaluator, or the repository gate;
3. a public-repository script that rejects documented placeholders, accepts local-only tracked paths, or accepts representative DeepSeek, GitHub, Supabase service-role, or private-key credentials.

Created `tests/integration/test_deployment_config.py` before any Task 11 production/configuration file. The test executes the real PowerShell script in temporary Git repositories and asserts its exit code and output; it does not inspect the script source.

The first RED run exposed a Windows-only test-harness problem: the external Python launcher decoded the Chinese parent directory incorrectly when an absolute project path was passed to a subprocess. The test was corrected before implementation to read project artifacts through relative paths and copy the script into each ASCII-only temporary repository. No production file existed or changed during this correction.

Corrected RED command:

```powershell
$env:PYTHONPATH=(Resolve-Path '.venv\Lib\site-packages').Path
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/integration/test_deployment_config.py -q
```

Result: exit `1`, `14 failed`, one pre-existing Starlette/httpx deprecation warning. The two configuration tests failed because `render.yaml` and `.github/workflows/ci.yml` did not exist. The 12 scanner cases failed because `scripts/verify_public_repo.ps1` did not exist. These are the intended missing-feature failures.

### GREEN

Implemented the minimal release artifacts and repeated the same focused command.

Result: exit `0`, `14 passed`, one pre-existing Starlette/httpx deprecation warning.

The 14 cases comprise:

- one semantic Render configuration test;
- one semantic CI workflow test;
- one clean placeholder-repository pass case;
- seven forbidden tracked-path cases (`.env`, `.venv`, pytest cache, Python cache, `.agents`, SQLite and logs);
- four tracked credential cases (DeepSeek assignment, GitHub token, Supabase service-key assignment and private-key header).

## Implementation

- `.github/workflows/ci.yml`: Python 3.13, dependency installation, full pytest, a separate 80-case offline evaluator, evaluation report upload, and an always-run PowerShell public-repository gate.
- `render.yaml`: free Python web service, platform `$PORT`, `/health`, production mode, bounded AI defaults, and `sync: false` platform secrets. It never supplies or reads a repository `.env`.
- `scripts/verify_public_repo.ps1`: reads `git ls-files`, checks forbidden tracked paths, scans tracked file contents for the required credential families, prints all unique violations and exits non-zero.
- `.gitignore`: covers environment variants, virtualenvs, agent metadata, Python/tool caches, coverage/build outputs, local databases, logs and frontend dependencies while explicitly retaining `.env.example`.
- `README.md` and `docs/deployment/free-tier.md`: vertical scenario, architecture and boundaries, local startup, complete configuration table, test/evaluation gates, Render/Supabase steps and links, cold starts/free-tier limits, DeepSeek cost warning, AI kill switch, smoke test and rollback checklist.
- `requirements.txt`: explicitly adds `PyYAML>=6.0`. This is necessary because the Task 11 integration test parses both YAML artifacts with `yaml.safe_load`, while a clean GitHub Actions runner installs only `requirements.txt`.

## Exact CI, Render and repository checks

The integration test parses `.github/workflows/ci.yml` and verifies:

- push and pull-request triggers;
- `ubuntu-latest` and Python `3.13`;
- `python -m pytest -q`;
- a separate `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl` command;
- execution of `./scripts/verify_public_repo.ps1` with PowerShell.

It parses `render.yaml` and verifies:

- the first service is a Python web service on plan `free`;
- `startCommand` contains `--port $PORT`;
- `healthCheckPath` is `/health`;
- production mode is explicit;
- DeepSeek, Supabase URL/anon/service keys and the anonymous-session signing key all use `sync: false`.

It executes the repository script in controlled Git repositories and verifies both its success output and non-zero rejection behavior. A direct release-command run from the project root also returned exit `0` and `Public repository check passed` before staging.

## Full verification

### Full pytest gate

Command, with the same environment values used by CI:

```powershell
$env:APP_ENV='test'
$env:DEEPSEEK_API_KEY='test-only-key'
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q
```

Result: exit `0`, `289 passed`, one pre-existing Starlette/httpx deprecation warning.

### Independent 80-case evaluation gate

```powershell
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation
```

Result: exit `0`; `total_cases: 80`; every positive metric and `overall` are `1.0`; `unsupported_fact_rate` is `0.0`; `failures`, `failed_thresholds` and `known_failures` are empty. The single `agent_failed` log line is the evaluator's intentional E010 database-fallback observation.

### Diff and public-repository hygiene

- `git diff --check`: exit `0`; Git emitted only expected LF-to-CRLF working-copy notices.
- Exact post-staging command, `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`: exit `0`, output `Public repository check passed`.
- `git diff --cached --check`: exit `0`.
- A `git ls-files` filter for `.env`, `.venv/`, `.pytest_cache/`, `__pycache__/`, `.agents/`, database extensions and `.log` returned no paths.
- The staged file list contains exactly the nine Task 11 files recorded in the implementation section; the unrelated work log remains untracked.

## Remaining concerns

- Render and Supabase free-plan quotas, sleep behavior and product terms can change; the documentation requires checking the current platform console and does not promise an SLA.
- Render free-instance cold starts can make the first health or browser request slow.
- DeepSeek usage remains billable independently of Render/Supabase. `AI_ENABLED=false` is the primary stop-cost control; both daily limits can also be set to zero.
- The existing Starlette/httpx deprecation warning is unchanged by this task.
- No external Render, Supabase or GitHub deployment was performed, so online smoke tests remain a release-time responsibility after platform secrets are configured.
