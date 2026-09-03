from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RENDER_PATH = PROJECT_ROOT / "render.yaml"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"


def _render_env_var_blocks() -> tuple[tuple[str, tuple[str, ...]], ...]:
    lines = RENDER_PATH.read_text(encoding="utf-8").splitlines()
    in_env_vars = False
    current_key: str | None = None
    current_lines: list[str] = []
    blocks: list[tuple[str, tuple[str, ...]]] = []

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if stripped == "envVars:":
            in_env_vars = True
            continue
        if in_env_vars and stripped and indent <= 4:
            break
        if not in_env_vars:
            continue

        if indent == 6 and stripped.startswith("- key:"):
            if current_key is not None:
                blocks.append((current_key, tuple(current_lines)))
            current_key = stripped.split(":", 1)[1].strip()
            current_lines = []
        elif current_key is not None:
            current_lines.append(stripped)

    if current_key is not None:
        blocks.append((current_key, tuple(current_lines)))
    return tuple(blocks)


def _env_block(key: str) -> tuple[str, ...]:
    matches = [lines for block_key, lines in _render_env_var_blocks() if block_key == key]
    assert len(matches) == 1, f"expected exactly one Render env declaration for {key}"
    return matches[0]


def test_render_service_declares_jina_api_key() -> None:
    keys = tuple(key for key, _lines in _render_env_var_blocks())

    assert "JINA_API_KEY" in keys


def test_jina_api_key_is_server_synced_and_has_no_committed_value() -> None:
    block = _env_block("JINA_API_KEY")

    assert "sync: false" in block
    assert not any(line.startswith("value:") for line in block)


def test_env_example_keeps_a_single_blank_jina_api_key_declaration() -> None:
    lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    jina_lines = [line for line in lines if line.startswith("JINA_API_KEY=")]

    assert jina_lines == ["JINA_API_KEY="]


def test_render_manifest_does_not_add_rag_v2_runtime_flags() -> None:
    keys = tuple(key for key, _lines in _render_env_var_blocks())

    assert all(not key.startswith("RAG_V2_") for key in keys)
