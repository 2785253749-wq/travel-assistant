from __future__ import annotations

import ast
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ROOT_EXPORTS = (
    "RagV2Schema",
    "DestinationLevel",
    "ChunkType",
    "AttractionLifecycleStatus",
    "AttractionVersionStatus",
    "ChunkStatus",
    "EmbeddingTask",
    "Destination",
    "EmbeddingProfile",
    "SourceProvenance",
    "StableAttraction",
    "AttractionVersionMetadata",
    "SemanticSection",
    "normalize_text",
    "normalize_content",
    "content_hash",
    "canonical_metadata_json",
    "metadata_hash",
    "EmbeddingInput",
    "build_embedding_input",
    "canonical_embedding_text",
    "embedding_input_hash",
    "ManifestChunk",
    "ManifestAttraction",
    "ManifestInput",
    "canonical_manifest_json",
    "manifest_hash",
    "AttractionIdentitySource",
    "rename_attraction",
    "merge_attraction",
    "retire_attraction",
    "handle_corpus_absence",
    "SemanticChunk",
    "CHUNK_KEY_SCHEMA_VERSION",
    "DEFAULT_CHUNK_BUDGET",
    "chunk_key_for",
    "SemanticChunker",
    "IncrementalSubject",
    "IncrementalAction",
    "EmbeddingIdentity",
    "PreviousEmbedding",
    "IncrementalCandidate",
    "IncrementalDecisionResult",
    "decide_incremental",
)


def _read(relative_path: str) -> str:
    path = PROJECT_ROOT / relative_path
    assert path.exists(), f"expected Stage 10C-1 file is missing: {relative_path}"
    return path.read_text(encoding="utf-8")


def _root_exports() -> tuple[str, ...]:
    tree = ast.parse(_read("app/rag_v2/__init__.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, list)
            assert all(isinstance(item, str) for item in value)
            return tuple(value)
    raise AssertionError("app.rag_v2.__all__ assignment is missing")


def test_acceptance_apis_are_module_local_and_root_exports_unchanged() -> None:
    assert _root_exports() == EXPECTED_ROOT_EXPORTS

    acceptance = _read("app/rag_v2/acceptance.py")
    cli = _read("app/scripts/rag_v2_acceptance.py")
    for name in (
        "AcceptanceContext",
        "AcceptanceReport",
        "build_parser",
        "run_cli",
        "activate_dataset",
    ):
        assert name in acceptance or name in cli
        assert name not in _root_exports()


def test_render_and_env_secret_contracts_remain_safe() -> None:
    render = _read("render.yaml")
    jina_declarations = re.findall(
        r"(?m)^[ \t]*- key: JINA_API_KEY[ \t]*$",
        render,
    )
    assert len(jina_declarations) == 1
    assert re.search(
        r"(?ms)^[ \t]*- key: JINA_API_KEY[ \t]*$\n[ \t]*sync: false[ \t]*$",
        render,
    )
    assert not re.search(
        r"(?ms)^[ \t]*- key: JINA_API_KEY[ \t]*$\n[ \t]*value:",
        render,
    )

    env_example = _read(".env.example")
    assert re.search(r"(?m)^JINA_API_KEY=\s*$", env_example)
    assert not re.search(r"(?m)^JINA_API_KEY=[ \t]*[^\s].*$", env_example)


def test_no_stage10c2_runtime_or_public_endpoint_wiring_exists() -> None:
    runtime = _read("app/main.py")
    cli = _read("app/scripts/rag_v2_acceptance.py")

    assert "rag_v2" not in runtime
    assert "FastAPI" not in cli
    assert "APIRouter" not in cli
    assert "@app." not in cli


def test_no_staging_retrieval_or_migration015_exists() -> None:
    assert not (PROJECT_ROOT / "supabase/migrations/015_rag_v2.sql").exists()

    retrieval = _read("app/rag_v2/retrieval.py")
    cli = _read("app/scripts/rag_v2_acceptance.py")
    for text in (retrieval, cli):
        assert "staging_corpus_version_id" not in text
        assert "query-staging" not in text
        assert "match_rag_v2_chunks" not in text


def test_only_activate_command_owns_activation_boundary() -> None:
    cli = _read("app/scripts/rag_v2_acceptance.py")

    assert cli.count("activate_corpus(") == 1
    assert cli.count("def activate_dataset(") == 1
    assert 'if command == "activate":' in cli
    assert "run_import" in cli
    assert "run_retrieval_check" in cli
    assert "run_post_activation_smoke" in cli


def test_legacy_rag_isolation_remains_intact() -> None:
    legacy_root = PROJECT_ROOT / "app/rag"
    legacy_files = tuple(
        path
        for path in legacy_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    assert legacy_files
    for path in legacy_files:
        assert "rag_v2" not in path.read_text(encoding="utf-8").lower()


def test_reports_are_ignored_and_no_secret_literals_are_committed() -> None:
    gitignore = _read(".gitignore")
    assert ".rag-v2-reports/" in gitignore

    env_example = _read(".env.example")
    render = _read("render.yaml")
    assert re.search(r"(?m)^JINA_API_KEY=\s*$", env_example)
    assert re.search(
        r"(?m)^\s*- key: JINA_API_KEY\s*\n\s*sync: false\s*$", render
    )
    assert "secret provider response" not in render


def test_active_only_retrieval_and_manual_activation_wording_is_preserved() -> None:
    plan = _read("docs/superpowers/plans/2026-09-04-rag-v2-stage10c-1.md")
    spec = _read("docs/superpowers/specs/2026-09-04-rag-v2-stage10c-1-design.md")
    acceptance = _read("app/rag_v2/acceptance.py")
    cli = _read("app/scripts/rag_v2_acceptance.py")

    assert "active-corpus-only" in plan
    assert "manual production activation" in plan
    assert 'corpus.status != "active"' in acceptance
    assert '"retrieval acceptance": "NOT YET RUN"' in cli
    assert "Only the `activate` subcommand may call" in spec
