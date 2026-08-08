from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _load_yaml(relative_path: str) -> dict:
    return yaml.safe_load(Path(relative_path).read_text(encoding="utf-8"))


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    assert executable is not None, "PowerShell is required to exercise the release gate"
    return executable


def _run_public_repo_check(repo: Path) -> subprocess.CompletedProcess[str]:
    script = repo / ".release" / "verify_public_repo.ps1"
    script.parent.mkdir(exist_ok=True)
    shutil.copyfile(Path("scripts/verify_public_repo.ps1"), script)
    return subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".release/verify_public_repo.ps1"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _tracked_repo(tmp_path: Path, relative_path: str, content: str) -> Path:
    repo = tmp_path / "repository"
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--force", relative_path], cwd=repo, check=True)
    return repo


def test_render_uses_free_plan_port_and_platform_secrets():
    config = _load_yaml("render.yaml")
    service = config["services"][0]

    assert service["type"] == "web"
    assert service["runtime"] == "python"
    assert service["plan"] == "free"
    assert "--port $PORT" in service["startCommand"]
    assert "--no-access-log" in service["startCommand"]
    assert "--no-proxy-headers" in service["startCommand"]
    assert service["healthCheckPath"] == "/health"
    env = {item["key"]: item for item in service["envVars"]}
    for key in (
        "DEEPSEEK_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_KEY",
        "ANON_SESSION_SIGNING_SECRET",
    ):
        assert env[key]["sync"] is False
    assert env["APP_ENV"]["value"] == "production"
    assert env["TRUSTED_CLIENT_IP_HEADER"]["value"] == "cf-connecting-ip"


def test_ci_runs_tests_offline_evaluation_and_public_repo_gate():
    workflow = _load_yaml(".github/workflows/ci.yml")
    steps = workflow["jobs"]["test"]["steps"]
    commands = "\n".join(str(step.get("run", "")) for step in steps)
    public_repo_steps = [
        step
        for step in steps
        if "./scripts/verify_public_repo.ps1" in str(step.get("run", ""))
    ]

    assert workflow["on"] == ["push", "pull_request"]
    assert workflow["jobs"]["test"]["runs-on"] == "ubuntu-latest"
    assert any(step.get("uses") == "actions/setup-python@v5" and step["with"]["python-version"] == "3.13" for step in steps)
    assert "python -m pytest -q" in commands
    assert "python -m tests.evaluation.runner" in commands
    assert "--cases tests/evaluation/cases.jsonl" in commands
    assert "./scripts/verify_public_repo.ps1" in commands
    assert len(public_repo_steps) == 1
    assert public_repo_steps[0].get("if") == "always()"


def test_public_repo_check_accepts_tracked_placeholders(tmp_path: Path):
    placeholder = "DEEPSEEK_API" + "_KEY=your_deepseek_api_key_here\n"
    placeholder += "SUPABASE_SERVICE" + "_KEY=your_supabase_service_key_here\n"
    repo = _tracked_repo(tmp_path, ".env.example", placeholder)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_accepts_unicode_tracked_placeholder(tmp_path: Path):
    placeholder = "DEEPSEEK_API" + "_KEY=your_deepseek_api_key_here\n"
    repo = _tracked_repo(tmp_path, "资料/占位符.txt", placeholder)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_accepts_multilanguage_placeholders(tmp_path: Path):
    placeholder = "deepseek_api" + '_key: "${DEEPSEEK_API_KEY}"\n'
    placeholder += 'supabase_service' + '_key: "<YOUR_SUPABASE_SERVICE_KEY>"\n'
    placeholder += 'anon_session_signing' + '_secret: "redacted"\n'
    repo = _tracked_repo(tmp_path, "config/settings.example.yaml", placeholder)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_accepts_typed_secret_setting_declarations(tmp_path: Path):
    declarations = "deepseek_api" + "_key: SecretStr | None = None\n"
    declarations += "supabase_service" + "_key: SecretStr | None = None\n"
    declarations += "anon_session_signing" + "_secret: SecretStr | None = None\n"
    repo = _tracked_repo(tmp_path, "app/config.py", declarations)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_allows_only_reviewed_superpowers_reports(tmp_path: Path):
    repo = _tracked_repo(
        tmp_path,
        ".superpowers/sdd/2026-08-08-segment-e/task-1-report.md",
        "# Reviewed public engineering report\n",
    )

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_scans_credentials_in_unicode_tracked_filename(tmp_path: Path):
    secret = "DEEPSEEK_API" + "_KEY=live-production-value\n"
    repo = _tracked_repo(tmp_path, "资料/凭据.txt", secret)

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env",
        ".venv/pyvenv.cfg",
        ".pytest_cache/README.md",
        "app/__pycache__/module.pyc",
        ".agents/skills/local.md",
        ".codex/config.toml",
        ".claude/settings.local.json",
        ".idea/workspace.xml",
        ".vscode/settings.json",
        ".worktrees/local/task.txt",
        ".superpowers/cache/state.json",
        "build/evaluation.json",
        "dist/app.js",
        "node_modules/package/index.js",
        "data/travel.sqlite3",
        "logs/app.log",
    ],
)
def test_public_repo_check_rejects_forbidden_tracked_paths(tmp_path: Path, relative_path: str):
    repo = _tracked_repo(tmp_path, relative_path, "local-only\n")

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert relative_path in result.stdout.replace("\\", "/")


@pytest.mark.parametrize(
    "secret",
    [
        "DEEPSEEK_API" + "_KEY=live-production-value",
        "gh" + "p_" + "A" * 36,
        "SUPABASE_SERVICE" + "_KEY=production-service-value",
        "-" * 5 + "BEGIN " + "RSA PRIVATE KEY" + "-" * 5,
        "-" * 5 + "BEGIN " + "ENCRYPTED PRIVATE KEY" + "-" * 5,
    ],
)
def test_public_repo_check_rejects_tracked_credentials(tmp_path: Path, secret: str):
    repo = _tracked_repo(tmp_path, "config.txt", secret + "\n")

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


@pytest.mark.parametrize(
    ("relative_path", "secret"),
    [
        ("config/settings.json", '{"deepseek_api' + '_key":"live-production-value"}'),
        ("config/settings.yaml", "supabase_service" + "_key: live-production-value"),
        ("config/settings.toml", 'anon_session_signing' + '_secret = "live-production-value"'),
        ("config/settings.js", 'const DEEPSEEK_API' + '_KEY = "live-production-value";'),
        ("notes/deepseek-token.txt", "s" + "k-" + "A" * 32),
        ("notes/supabase-token.txt", "s" + "b_secret_" + "A" * 32),
        ("notes/legacy-supabase-jwt.txt", "e" + "yJ" + "A" * 24 + "." + "B" * 24 + "." + "C" * 24),
    ],
)
def test_public_repo_check_rejects_multilanguage_and_raw_credentials(
    tmp_path: Path,
    relative_path: str,
    secret: str,
):
    repo = _tracked_repo(tmp_path, relative_path, secret + "\n")

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()
