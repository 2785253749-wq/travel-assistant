import importlib
import subprocess
import sys

import pytest


EXPECTED_EXPORTS = [
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
]


def test_rag_v2_exports_exact_approved_surface_and_order():
    rag_v2 = importlib.import_module("app.rag_v2")

    assert rag_v2.__all__ == EXPECTED_EXPORTS


@pytest.mark.parametrize("name", EXPECTED_EXPORTS)
def test_every_rag_v2_export_resolves(name):
    rag_v2 = importlib.import_module("app.rag_v2")

    assert name in rag_v2.__all__
    assert hasattr(rag_v2, name)
    getattr(rag_v2, name)


@pytest.mark.parametrize(
    ("name", "module_name"),
    [
        ("RagV2Schema", "app.rag_v2.models"),
        ("EmbeddingInput", "app.rag_v2.models"),
        ("ManifestInput", "app.rag_v2.models"),
        ("SemanticChunk", "app.rag_v2.models"),
        ("normalize_content", "app.rag_v2.hashing"),
        ("embedding_input_hash", "app.rag_v2.hashing"),
        ("manifest_hash", "app.rag_v2.hashing"),
        ("AttractionIdentitySource", "app.rag_v2.identity"),
        ("merge_attraction", "app.rag_v2.identity"),
        ("SemanticChunker", "app.rag_v2.chunking"),
        ("chunk_key_for", "app.rag_v2.chunking"),
        ("IncrementalDecisionResult", "app.rag_v2.incremental"),
        ("decide_incremental", "app.rag_v2.incremental"),
    ],
)
def test_package_exports_resolve_to_task_owned_objects(name, module_name):
    rag_v2 = importlib.import_module("app.rag_v2")
    owner = importlib.import_module(module_name)

    assert getattr(rag_v2, name) is getattr(owner, name)


def test_private_helpers_are_not_package_public():
    rag_v2 = importlib.import_module("app.rag_v2")
    private_names = {
        "_normalize_string",
        "_sha256_hex",
        "_split_oversized",
        "_IDENTITY_FIELDS",
        "_identity_matches",
        "_vector_is_eligible",
    }

    assert private_names.isdisjoint(rag_v2.__all__)


def test_administrative_code_is_not_package_public():
    rag_v2 = importlib.import_module("app.rag_v2")

    assert "AdministrativeCode" not in rag_v2.__all__


def test_stdlib_and_framework_symbols_are_not_package_public():
    rag_v2 = importlib.import_module("app.rag_v2")
    incidental_names = {
        "BaseModel",
        "ConfigDict",
        "Field",
        "UUID",
        "date",
        "datetime",
        "Enum",
        "Protocol",
        "json",
        "re",
        "unicodedata",
        "sha256",
        "dataclass",
        "isfinite",
    }

    assert incidental_names.isdisjoint(rag_v2.__all__)


def test_legacy_app_rag_remains_importable():
    legacy = importlib.import_module("app.rag")

    assert legacy is not None


def test_rag_v2_import_has_no_provider_database_or_runtime_dependency():
    script = """
import sys
import app.rag_v2

blocked_prefixes = (
    "supabase",
    "pgvector",
    "jina",
    "render",
    "app.agent",
    "app.composition",
)
blocked = sorted(
    name
    for name in sys.modules
    if name == "planner" or name.startswith(blocked_prefixes)
)
if blocked:
    raise SystemExit("unexpected imports: " + ", ".join(blocked))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_rag_v2_package_documentation_is_non_empty():
    rag_v2 = importlib.import_module("app.rag_v2")

    assert rag_v2.__doc__
    assert rag_v2.__doc__.strip()
