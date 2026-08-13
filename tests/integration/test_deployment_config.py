from __future__ import annotations

import copy
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


def _assert_ci_workflow_contract(workflow: dict) -> None:
    steps = workflow["jobs"]["test"]["steps"]
    commands = [" ".join(str(step.get("run", "")).split()) for step in steps]
    pytest_indices = [index for index, command in enumerate(commands) if command == "python -m pytest -q"]
    evaluation_indices = [
        index
        for index, command in enumerate(commands)
        if command
        == "python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation"
    ]
    public_repo_indices = [
        index for index, command in enumerate(commands) if command == "./scripts/verify_public_repo.ps1"
    ]

    assert workflow["on"] == ["push", "pull_request"]
    assert workflow["jobs"]["test"]["runs-on"] == "ubuntu-latest"
    assert any(step.get("uses") == "actions/setup-python@v5" and step["with"]["python-version"] == "3.13" for step in steps)
    assert len(pytest_indices) == 1
    assert len(evaluation_indices) == 1
    assert len(public_repo_indices) == 1
    public_repo_index = public_repo_indices[0]
    public_repo_step = steps[public_repo_index]
    assert public_repo_step.get("shell") == "pwsh"
    assert public_repo_step.get("if") == "always()"
    assert public_repo_index > max(pytest_indices[0], evaluation_indices[0])


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
        "AMAP_JS_KEY",
        "AMAP_SECURITY_JS_CODE",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_KEY",
        "ANON_SESSION_SIGNING_SECRET",
    ):
        assert env[key]["sync"] is False
    assert env["APP_ENV"]["value"] == "production"
    assert env["TRUSTED_CLIENT_IP_HEADER"]["value"] == "cf-connecting-ip"


def test_deployment_document_describes_amap_key_and_offline_fallback():
    text = Path("docs/deployment/free-tier.md").read_text(encoding="utf-8")

    assert "AMAP_JS_KEY" in text
    assert "AMAP_SECURITY_JS_CODE" in text
    assert "同时" in text
    assert "域名" in text
    assert "离线" in text
    assert "travel-assistant-2cbd.onrender.com" in text
    assert "JavaScript API" in text
    assert "Web 服务 Key" in text
    assert "http://127.0.0.1" in text
    assert "重新部署" in text
    assert "福建 → 厦门 → 任一景点" in text


def test_ci_runs_tests_offline_evaluation_and_public_repo_gate():
    workflow = _load_yaml(".github/workflows/ci.yml")

    _assert_ci_workflow_contract(workflow)


def test_ci_contract_rejects_a_removed_scanner_step():
    workflow = copy.deepcopy(_load_yaml(".github/workflows/ci.yml"))
    workflow["jobs"]["test"]["steps"].pop()

    with pytest.raises(AssertionError):
        _assert_ci_workflow_contract(workflow)


def test_ci_contract_rejects_a_spoofed_scanner_command():
    workflow = copy.deepcopy(_load_yaml(".github/workflows/ci.yml"))
    workflow["jobs"]["test"]["steps"][-1]["run"] = "Write-Output ./scripts/verify_public_repo.ps1"

    with pytest.raises(AssertionError):
        _assert_ci_workflow_contract(workflow)


def test_ci_contract_requires_scanner_to_run_after_failure():
    workflow = copy.deepcopy(_load_yaml(".github/workflows/ci.yml"))
    workflow["jobs"]["test"]["steps"][-1].pop("if")

    with pytest.raises(AssertionError):
        _assert_ci_workflow_contract(workflow)


def test_ci_contract_rejects_scanner_before_the_evaluation_gate():
    workflow = copy.deepcopy(_load_yaml(".github/workflows/ci.yml"))
    steps = workflow["jobs"]["test"]["steps"]
    scanner = steps.pop()
    steps.insert(4, scanner)

    with pytest.raises(AssertionError):
        _assert_ci_workflow_contract(workflow)


def test_public_repo_check_accepts_tracked_placeholders(tmp_path: Path):
    placeholder = "DEEPSEEK_API" + "_KEY=your_deepseek_api_key_here\n"
    placeholder += "SUPABASE_SERVICE" + "_KEY=your_supabase_service_key_here\n"
    repo = _tracked_repo(tmp_path, ".env.example", placeholder)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_accepts_empty_new_key_declarations(tmp_path: Path):
    content = "JINA_API_KEY=\nAMAP_WEB_SERVICE_KEY=\n"
    repo = _tracked_repo(tmp_path, ".env.example", content)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_example_environment_lists_server_keys_without_secret_values():
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "JINA_API_KEY=" in content
    assert "AMAP_WEB_SERVICE_KEY=" in content
    assert "JINA_API_KEY=your_" not in content
    assert "AMAP_WEB_SERVICE_KEY=your_" not in content
    assert "sk-" not in content


def test_readme_documents_rag_weather_safe_deployment_order_and_fallbacks():
    content = Path("README.md").read_text(encoding="utf-8")

    assert "008_rag_knowledge.sql" in content
    assert "python -m app.scripts.import_knowledge" in content
    assert "JINA_API_KEY" in content
    assert "AMAP_WEB_SERVICE_KEY" in content
    assert "资料库没有足够依据，无法可靠回答。" in content
    assert "天气信息暂不可用" in content
    assert "行程仍可正常生成" in content


@pytest.mark.parametrize("name", ["JINA_API_KEY", "AMAP_WEB_SERVICE_KEY"])
def test_public_repo_check_rejects_new_server_secret_assignments(
    tmp_path: Path,
    name: str,
):
    repo = _tracked_repo(tmp_path, "config/settings.env", f"{name}=live-production-value\n")

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ('Settings(jina_api' + '_key="__JINA_INLINE_SECRET__")\n', "Jina API key"),
        ('configure(\n    amap_web_service' + '_key="__AMAP_INLINE_SECRET__",\n)\n', "AMap Web Service key"),
    ],
)
def test_public_repo_check_rejects_python_inline_server_key_arguments(
    tmp_path: Path,
    source: str,
    name: str,
):
    secret = "fixture" + "-private-value"
    repo = _tracked_repo(
        tmp_path,
        "app/configuration.py",
        source.replace("__JINA_INLINE_SECRET__", secret).replace("__AMAP_INLINE_SECRET__", secret),
    )

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert name in result.stdout


def test_public_repo_check_does_not_mistake_python_comparisons_or_annotations_for_assignments(
    tmp_path: Path,
):
    source = 'jina_api' + '_key: str | None = None\nassert amap_web_service' + '_key == "configured"\n'
    repo = _tracked_repo(tmp_path, "app/configuration.py", source)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_public_repo_check_accepts_test_only_python_constructor_key_value(tmp_path: Path):
    source = 'Settings(jina_api' + '_key="test-key")\nnext_test()\n'
    repo = _tracked_repo(tmp_path, "tests/test_configuration.py", source)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_public_repo_check_accepts_yaml_placeholder_before_next_list_item(tmp_path: Path):
    name = "DEEPSEEK_API" + "_KEY"
    repo = _tracked_repo(tmp_path, "config/workflow.yml", f"{name}: test-only-key\n- name: next step\n")

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_accepts_powershell_safe_reference_before_invocation(tmp_path: Path):
    name = "DEEPSEEK_API" + "_KEY"
    source = f"$env:{name}=${name}\n& 'python.exe' -m pytest\n"
    repo = _tracked_repo(tmp_path, "docs/report.md", source)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_rejects_javascript_expression_in_review_report(tmp_path: Path):
    name = "DEEPSEEK_API" + "_KEY"
    source = f'```javascript\nconst {name} = process.env.{name}\n  + "live-secret"\n```\n'
    repo = _tracked_repo(tmp_path, "docs/report.md", source)

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()
    assert "credential" in result.stdout.lower()


def test_public_repo_check_accepts_typed_secret_setting_declarations(tmp_path: Path):
    declarations = "deepseek_api" + "_key: SecretStr | None = None\n"
    declarations += "supabase_service" + "_key: SecretStr | None = None\n"
    declarations += "anon_session_signing" + "_secret: SecretStr | None = None\n"
    repo = _tracked_repo(tmp_path, "app/config.py", declarations)

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_rejects_javascript_property_secret_assignment(tmp_path: Path):
    name = "ANON_SESSION_SIGNING" + "_SECRET"
    repo = _tracked_repo(tmp_path, "config/settings.js", f'process.env.{name} = "real-secret";\n')

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


@pytest.mark.parametrize(
    "left_hand_side",
    [
        'process.env["ANON_SESSION_SIGNING_SECRET"]',
        "process.env['ANON_SESSION_SIGNING_SECRET']",
        "process.env[`ANON_SESSION_SIGNING_SECRET`]",
        'process.env[/* config */ "ANON_SESSION_SIGNING_SECRET"]',
        'config["ANON_SESSION_SIGNING_SECRET"]',
    ],
)
def test_public_repo_check_rejects_javascript_computed_property_secret_assignment(
    tmp_path: Path,
    left_hand_side: str,
):
    repo = _tracked_repo(tmp_path, "config/settings.js", f'{left_hand_side} = "real-secret";\n')

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


def test_public_repo_check_rejects_cooked_static_javascript_template_secret_key(tmp_path: Path):
    receiver = "process" + ".env"
    left_hand_side = receiver + "[`ANON_SESSION_SIGNING_" + r"\u0053ECRET`]"
    repo = _tracked_repo(tmp_path, "config/settings.js", f'{left_hand_side} = "real-secret";\n')

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


@pytest.mark.parametrize(
    "left_hand_side",
    [
        pytest.param(
            'process{trivia}.env["{name}"]',
            id="before-property-access-dot",
        ),
        pytest.param(
            'process.{trivia}env["{name}"]',
            id="after-property-access-dot",
        ),
        pytest.param(
            'process.env{trivia}["{name}"]',
            id="before-computed-property-bracket",
        ),
        pytest.param(
            'process.env[{trivia}"{name}"]',
            id="after-computed-property-bracket",
        ),
        pytest.param(
            'process.env["{name}"{trivia}]',
            id="before-computed-property-close",
        ),
        pytest.param(
            'process.env["{name}"]{trivia}',
            id="before-assignment-operator",
        ),
    ],
)
@pytest.mark.parametrize(
    "trivia",
    [
        pytest.param("/* config */", id="block-comment"),
        pytest.param("// config\n", id="line-comment"),
    ],
)
def test_public_repo_check_rejects_javascript_comment_trivia_at_assignment_token_seams(
    tmp_path: Path,
    left_hand_side: str,
    trivia: str,
):
    name = "ANON_SESSION_SIGNING" + "_SECRET"
    left_hand_side = left_hand_side.format(name=name, trivia=trivia)
    repo = _tracked_repo(tmp_path, "config/settings.js", f'{left_hand_side} = "real-secret";\n')

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


@pytest.mark.parametrize(
    ("setup", "expression"),
    [
        pytest.param('const suffix = "SECRET";\n', "suffix", id="identifier-expression"),
        pytest.param("", "`SECRET`", id="nested-template-expression"),
    ],
)
def test_public_repo_check_rejects_dynamic_template_key_with_safe_environment_reference(
    tmp_path: Path,
    setup: str,
    expression: str,
):
    receiver = "process" + ".env"
    name = "ANON_SESSION_SIGNING" + "_SECRET"
    prefix = "ANON_SESSION_SIGNING" + "_"
    source = setup
    source += f"{receiver}[`{prefix}${{{expression}}}`] = {receiver}.{name};\n"
    repo = _tracked_repo(tmp_path, "config/settings.js", source)

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


def test_public_repo_check_accepts_javascript_environment_reference(tmp_path: Path):
    name = "DEEPSEEK_API" + "_KEY"
    repo = _tracked_repo(tmp_path, "config/settings.js", f"const config = {{ {name}: process.env.{name} }};\n")

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_rejects_javascript_environment_reference_with_literal_fallback(tmp_path: Path):
    name = "DEEPSEEK_API" + "_KEY"
    source = f'const config = {{ {name}: process.env.{name} || "live-secret" }};\n'
    repo = _tracked_repo(tmp_path, "config/settings.js", source)

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


@pytest.mark.parametrize(
    "continuation",
    [
        '\n  || "live-secret"',
        ' // safe reference\n  || "live-secret"',
        '\n  && "live-secret"',
        '\n  ?? "live-secret"',
        '\n  + "live-secret"',
        ' // safe reference\n  + "live-secret"',
        '\n  ? process.env.DEEPSEEK_API_KEY : "live-secret"',
        pytest.param('\n  \f + "live-secret"', id="form-feed-before-concatenation"),
        pytest.param('\n  \v + "live-secret"', id="vertical-tab-before-concatenation"),
        pytest.param('\n  \u00a0 + "live-secret"', id="nbsp-before-concatenation"),
        pytest.param('\n  \ufeff + "live-secret"', id="bom-before-concatenation"),
        pytest.param(
            '\n  /* safe reference */\u00a0 + "live-secret"',
            id="block-comment-and-nbsp-before-concatenation",
        ),
        pytest.param('\n  \f || "live-secret"', id="form-feed-before-logical-or"),
        pytest.param(
            '\n  \u00a0 ? process.env.DEEPSEEK_API_KEY : "live-secret"',
            id="nbsp-before-ternary",
        ),
        pytest.param(
            ' // safe reference\u2028 + "live-secret"',
            id="line-separator-ends-line-comment",
        ),
    ],
)
def test_public_repo_check_rejects_multiline_javascript_environment_expression_continuation(
    tmp_path: Path,
    continuation: str,
):
    name = "DEEPSEEK_API" + "_KEY"
    source = f"const config = {{ {name}: process.env.{name}{continuation} }};\n"
    repo = _tracked_repo(tmp_path, "config/settings.js", source)

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


def test_public_repo_check_rejects_placeholder_prefixed_secret(tmp_path: Path):
    name = "ANON_SESSION_SIGNING" + "_SECRET"
    repo = _tracked_repo(tmp_path, ".env.example", f"{name}=placeholder-live-production-secret\n")

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


def test_release_docs_do_not_claim_unverified_online_evidence_or_langgraph():
    readme = Path("README.md").read_text(encoding="utf-8")
    deployment = Path("docs/deployment/free-tier.md").read_text(encoding="utf-8")
    evidence = Path("docs/deployment/release-evidence.md").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()

    assert "LangGraph" not in readme
    assert "langgraph" not in requirements
    assert "BLOCKED" in deployment
    assert "不包含已验证的公开 URL" in deployment
    assert "不得把 `https://<service>.onrender.com`" in deployment
    assert "匿名用户只能使用未持久化的对话规划" in deployment
    assert "BLOCKED — external deployment has not been verified" in evidence
    assert "Not supplied" in evidence
    assert "Not run" in evidence


def test_public_repo_check_accepts_typescript_secret_type_declaration(tmp_path: Path):
    name = "DEEPSEEK_API" + "_KEY"
    repo = _tracked_repo(tmp_path, "config/settings.ts", f"interface Config {{ {name}: string; }}\n")

    result = _run_public_repo_check(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public repository check passed" in result.stdout


def test_public_repo_check_rejects_runtime_secret_after_typescript_interface(tmp_path: Path):
    name = "DEEPSEEK_API" + "_KEY"
    source = f"interface Config {{ {name}: string; }}\n"
    source += f'const config = {{ {name}: "live-secret" }};\n'
    repo = _tracked_repo(tmp_path, "config/settings.ts", source)

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "credential" in result.stdout.lower()


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
    "relative_path",
    [
        ".env\u200b",
        ".e\u200dnv",
        ".\ufeffenv",
        ".ｅｎｖ",
        ".venv\ufe0f/pyvenv.cfg",
    ],
)
def test_public_repo_check_rejects_unicode_disguised_forbidden_paths(
    tmp_path: Path,
    relative_path: str,
):
    repo = _tracked_repo(tmp_path, relative_path, "local-only\n")

    result = _run_public_repo_check(repo)

    assert result.returncode != 0
    assert "forbidden tracked path" in result.stdout.lower()


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
