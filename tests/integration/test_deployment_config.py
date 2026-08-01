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


def test_ci_runs_tests_offline_evaluation_and_public_repo_gate():
    workflow = _load_yaml(".github/workflows/ci.yml")
    steps = workflow["jobs"]["test"]["steps"]
    commands = "\n".join(str(step.get("run", "")) for step in steps)

    assert workflow["on"] == ["push", "pull_request"]
    assert workflow["jobs"]["test"]["runs-on"] == "ubuntu-latest"
    assert any(step.get("uses") == "actions/setup-python@v5" and step["with"]["python-version"] == "3.13" for step in steps)
    assert "python -m pytest -q" in commands
    assert "python -m tests.evaluation.runner" in commands
    assert "--cases tests/evaluation/cases.jsonl" in commands
    assert "./scripts/verify_public_repo.ps1" in commands


def test_public_repo_check_accepts_tracked_placeholders(tmp_path: Path):
    placeholder = "DEEPSEEK_API" + "_KEY=your_deepseek_api_key_here\n"
    placeholder += "SUPABASE_SERVICE" + "_KEY=your_supabase_service_key_here\n"
    repo = _tracked_repo(tmp_path, ".env.example", placeholder)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env",
        ".venv/pyvenv.cfg",
        ".pytest_cache/README.md",
        "app/__pycache__/module.pyc",
        ".agents/skills/local.md",
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
    ],
)
def test_public_repo_check_rejects_tracked_credentials(tmp_path: Path, secret: str):
    repo = _tracked_repo(tmp_path, "config.txt", secret + "\n")

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()
