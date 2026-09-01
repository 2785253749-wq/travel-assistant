from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "supabase" / "migrations" / "014_rag_v2.sql"
V2_TABLES = {
    "rag_corpus_versions",
    "rag_attractions",
    "rag_attraction_versions",
    "rag_attraction_chunks",
}


def _migration_sql() -> str:
    assert MIGRATION_PATH.exists(), (
        "expected RAG V2 migration artifact does not yet exist"
    )
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _table_block(sql: str, table_name: str) -> str:
    match = re.search(
        rf"create\s+table\s+public\.{re.escape(table_name)}\s*"
        rf"\((?P<body>.*?)\)\s*;",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, f"missing table definition: {table_name}"
    return _normalized(match.group("body"))


def _contains_any(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def test_migration_artifact_exists_and_is_readable() -> None:
    sql = _migration_sql()

    assert sql.strip()


def test_v2_migration_declares_exactly_the_four_business_tables_and_vector_guard() -> None:
    sql = _migration_sql()
    declared_tables = set(
        re.findall(r"create\s+table\s+public\.([a-z0-9_]+)", sql)
    )

    assert declared_tables == V2_TABLES
    assert re.search(r"create\s+extension\s+if\s+not\s+exists\s+vector\s*;?", sql)


def test_corpus_versions_schema_and_active_uniqueness_are_declared() -> None:
    sql = _migration_sql()
    block = _table_block(sql, "rag_corpus_versions")

    for fragment in (
        "corpus_version_id uuid not null",
        "dataset_key text not null",
        "version_label text not null",
        "manifest_hash text not null",
        "status text not null default 'staging'",
        "created_at timestamptz not null default now()",
        "activated_at timestamptz null",
        "superseded_at timestamptz null",
    ):
        assert fragment in block

    assert _contains_any(
        block,
        r"corpus_version_id\s+uuid\s+not\s+null\s+primary\s+key",
        r"primary\s+key\s*\(\s*corpus_version_id\s*\)",
    )
    assert re.search(
        r"unique\s*\(\s*dataset_key\s*,\s*version_label\s*\)", sql
    )
    assert all(
        value in block
        for value in ("'staging'", "'active'", "'superseded'", "'failed'")
    )
    assert re.search(r"check\s*\(\s*status\s+in", block)
    assert "^[0-9a-f]{64}$" in block
    assert re.search(
        r"\(\s*dataset_key\s*\)\s*where\s+status\s*=\s*'active'", sql
    )


def test_stable_attractions_schema_contains_only_identity_lifecycle_contract() -> None:
    sql = _migration_sql()
    block = _table_block(sql, "rag_attractions")

    for fragment in (
        "attraction_id uuid not null",
        "lifecycle_status text not null default 'active'",
        "created_at timestamptz not null default now()",
        "retired_at timestamptz null",
        "merged_into_attraction_id uuid null",
    ):
        assert fragment in block

    assert _contains_any(
        block,
        r"attraction_id\s+uuid\s+not\s+null\s+primary\s+key",
        r"primary\s+key\s*\(\s*attraction_id\s*\)",
    )
    assert re.search(
        r"merged_into_attraction_id.*references\s+public\.rag_attractions\s*"
        r"\(\s*attraction_id\s*\).*on\s+delete\s+restrict.*"
        r"on\s+update\s+restrict",
        block,
    )
    assert all(value in block for value in ("'active'", "'retired'", "'merged'"))
    assert re.search(r"check\s*\(\s*lifecycle_status\s+in", block)
    assert "retired_at is null" in block
    assert "retired_at is not null" in block
    assert "merged_into_attraction_id is null" in block
    assert "merged_into_attraction_id is not null" in block
    assert "attraction_id" in block

    for forbidden in (
        "canonical_name",
        "destination_code",
        "metadata_hash",
        "content",
        "source_url",
    ):
        assert not re.search(rf"\b{forbidden}\b", block)


def test_attraction_versions_schema_keys_domain_checks_and_metadata_hash_are_declared() -> None:
    sql = _migration_sql()
    block = _table_block(sql, "rag_attraction_versions")

    for fragment in (
        "corpus_version_id uuid not null",
        "attraction_id uuid not null",
        "canonical_name text not null",
        "aliases jsonb not null",
        "destination_code text not null",
        "destination_level text not null",
        "destination_name text not null",
        "province_code text not null",
        "province_name text not null",
        "district_name text null",
        "category text null",
        "tags jsonb not null",
        "latitude numeric null",
        "longitude numeric null",
        "status text not null",
        "metadata_hash text not null",
    ):
        assert fragment in block

    assert "primary key (corpus_version_id, attraction_id)" in block
    assert re.search(
        r"foreign\s+key\s*\(\s*corpus_version_id\s*\)\s*references\s+"
        r"public\.rag_corpus_versions\s*\(\s*corpus_version_id\s*\).*"
        r"on\s+delete\s+restrict.*on\s+update\s+restrict",
        block,
    )
    assert re.search(
        r"foreign\s+key\s*\(\s*attraction_id\s*\)\s*references\s+"
        r"public\.rag_attractions\s*\(\s*attraction_id\s*\).*"
        r"on\s+delete\s+restrict.*on\s+update\s+restrict",
        block,
    )
    assert all(value in block for value in ("'included'", "'suppressed'"))
    assert re.search(r"check\s*\(\s*status\s+in", block)
    assert re.search(r"check\s*\(\s*destination_level\s+in", block)
    assert block.count("^[0-9a-f]{64}$") >= 1
    assert "destination_code ~ '^[0-9]{6}$'" in block
    assert "province_code ~ '^[0-9]{6}$'" in block
    assert all(
        value in block
        for value in (
            "'province'",
            "'prefecture_city'",
            "'autonomous_prefecture'",
            "'county_city'",
        )
    )
    assert "latitude between -90 and 90" in block
    assert "longitude between -180 and 180" in block
    for forbidden in ("source_label", "source_url", "source_type", "reviewed_on"):
        assert not re.search(rf"\b{forbidden}\b", block)


def test_attraction_chunks_schema_identity_profile_and_semantic_uniqueness_are_declared() -> None:
    sql = _migration_sql()
    block = _table_block(sql, "rag_attraction_chunks")

    for fragment in (
        "corpus_version_id uuid not null",
        "attraction_id uuid not null",
        "chunk_key text not null",
        "chunk_type text not null",
        "ordinal integer not null",
        "content text not null",
        "content_hash text not null",
        "embedding_input_hash text not null",
        "embedding_input_schema_version text not null",
        "source_label text not null",
        "source_url text not null",
        "source_type text not null",
        "reviewed_on date not null",
        "embedding_model text not null",
        "embedding_task text not null",
        "embedding_dimensions integer not null",
        "embedding vector(1024) null",
        "status text not null",
        "embedding_error_code text null",
        "embedding_error_message text null",
    ):
        assert fragment in block

    assert "primary key (corpus_version_id, chunk_key)" in block
    assert re.search(
        r"unique\s*\(\s*corpus_version_id\s*,\s*attraction_id\s*,\s*"
        r"chunk_type\s*,\s*ordinal\s*\)", sql
    )
    assert re.search(
        r"foreign\s+key\s*\(\s*corpus_version_id\s*,\s*attraction_id\s*\)\s*"
        r"references\s+public\.rag_attraction_versions\s*\(\s*"
        r"corpus_version_id\s*,\s*attraction_id\s*\).*on\s+delete\s+restrict.*"
        r"on\s+update\s+restrict",
        block,
    )
    assert all(
        value in block
        for value in (
            "'overview'",
            "'highlights'",
            "'transport'",
            "'visit_advice'",
            "'seasonal'",
        )
    )
    assert re.search(r"check\s*\(\s*chunk_type\s+in", block)
    assert "ordinal >= 0" in block
    assert "embedding_model = 'jina-embeddings-v3'" in block
    assert "embedding_task = 'retrieval.passage'" in block
    assert "embedding_dimensions = 1024" in block
    assert "embedding_input_schema_version = 'rag-v2-embedding-input-v1'" in block
    assert block.count("^[0-9a-f]{64}$") >= 2


def test_chunk_status_vector_and_error_consistency_checks_are_declared() -> None:
    block = _table_block(_migration_sql(), "rag_attraction_chunks")

    assert all(
        value in block
        for value in ("'pending'", "'embedded'", "'failed'", "'excluded'")
    )
    assert re.search(r"check\s*\(\s*status\s+in", block)
    for fragment in (
        "embedding is null",
        "embedding is not null",
        "embedding_error_code is null",
        "embedding_error_code is not null",
        "embedding_error_message is null",
    ):
        assert fragment in block
    assert "embedding_error_message is not null" not in block


def test_chunk_table_excludes_non_contract_retry_timestamp_and_destination_columns() -> None:
    block = _table_block(_migration_sql(), "rag_attraction_chunks")

    for forbidden in (
        "retry_count",
        "next_retry_at",
        "provider_request_id",
        "created_at",
        "updated_at",
        "embedded_at",
        "failed_at",
        "destination_code",
        "province_code",
        "destination_level",
    ):
        assert not re.search(rf"\b{forbidden}\b", block)


def test_new_migration_preserves_legacy_rag_isolation() -> None:
    sql = _migration_sql()

    forbidden_operations = (
        "alter table public.knowledge_chunks",
        "drop table public.knowledge_chunks",
        "drop function public.match_knowledge_chunks",
        "create or replace function public.match_knowledge_chunks",
    )
    for operation in forbidden_operations:
        assert operation not in sql


def _trigger_function_bodies(sql: str) -> list[str]:
    return [
        match.group("body")
        for match in re.finditer(
            r"create\s+(?:or\s+replace\s+)?function\s+public\.[a-z0-9_]+\s*"
            r"\([^)]*\)\s*returns\s+trigger\b[\s\S]*?"
            r"as\s+\$(?P<tag>[a-z0-9_]*)\$(?P<body>.*?)\$(?P=tag)\$\s*;",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]


def _trigger_bodies_text(sql: str) -> str:
    return " ".join(_trigger_function_bodies(sql))


def _assert_trigger_attached(
    sql: str,
    table_name: str,
    event: str,
) -> None:
    assert re.search(
        rf"create\s+trigger\s+[a-z0-9_]+\s+"
        rf"(?:before|after)\s+[\s\S]*?\b{event}\b[\s\S]*?"
        rf"on\s+public\.{re.escape(table_name)}\b",
        sql,
        flags=re.IGNORECASE,
    ), f"missing {event} trigger attachment for {table_name}"


def _assert_transition(body: str, old: str, new: str) -> None:
    assert re.search(
        rf"old\.(?:status|lifecycle_status)\s*=\s*'{old}'[\s\S]*?"
        rf"new\.(?:status|lifecycle_status)\s*=\s*'{new}'",
        body,
        flags=re.IGNORECASE,
    )


def test_task2_corpus_mutation_guard_trigger_function_contract_is_declared() -> None:
    sql = _migration_sql()
    bodies = _trigger_function_bodies(sql)

    assert bodies, (
        "expected RAG V2 corpus mutation guard trigger/function contract"
    )
    _assert_trigger_attached(sql, "rag_corpus_versions", "update")


def test_task2_corpus_identity_and_lifecycle_guards_use_old_new_and_reject_invalid_changes() -> None:
    sql = _migration_sql()
    body = _trigger_bodies_text(sql)

    _assert_trigger_attached(sql, "rag_corpus_versions", "update")
    assert "old" in body and "new" in body
    for field in (
        "corpus_version_id",
        "dataset_key",
        "version_label",
        "manifest_hash",
        "created_at",
    ):
        assert field in body
    for lifecycle_field in ("status", "activated_at", "superseded_at"):
        assert lifecycle_field in body
    assert re.search(r"raise\s+exception", body, flags=re.IGNORECASE)
    assert re.search(
        r"old\.status\s*<>\s*new\.status|new\.status\s*<>\s*old\.status",
        body,
        flags=re.IGNORECASE,
    )


def test_task2_corpus_lifecycle_guard_preserves_only_approved_transitions() -> None:
    body = _trigger_bodies_text(_migration_sql())

    for old, new in (
        ("staging", "active"),
        ("staging", "failed"),
        ("active", "superseded"),
    ):
        _assert_transition(body, old, new)

    assert re.search(
        r"status\s+not\s+in|not\s*\([\s\S]*status|status\s*<>\s*",
        body,
        flags=re.IGNORECASE,
    )
    for value in (
        "active",
        "staging",
        "failed",
        "superseded",
    ):
        assert f"'{value}'" in body


def test_task2_stable_attraction_guard_protects_identity_and_lifecycle_direction() -> None:
    sql = _migration_sql()
    body = _trigger_bodies_text(sql)

    _assert_trigger_attached(sql, "rag_attractions", "update")
    for field in ("attraction_id", "created_at"):
        assert field in body
    assert "old" in body and "new" in body
    for old, new in (("active", "retired"), ("active", "merged")):
        _assert_transition(body, old, new)
    for value in ("retired", "merged"):
        assert f"'{value}'" in body
    assert re.search(r"raise\s+exception", body, flags=re.IGNORECASE)

def test_task2_version_snapshot_guards_cover_staging_insert_update_and_delete() -> None:
    sql = _migration_sql()
    body = _trigger_bodies_text(sql)

    for event in ("insert", "update", "delete"):
        _assert_trigger_attached(sql, "rag_attraction_versions", event)
    assert "rag_attraction_versions" in body
    assert "rag_corpus_versions" in body
    assert "status = 'staging'" in body
    assert re.search(r"raise\s+exception", body, flags=re.IGNORECASE)
    assert re.search(
        r"update\s+always|always\s+reject|update[\s\S]*raise\s+exception",
        body,
        flags=re.IGNORECASE,
    )


def test_task2_chunk_parent_guards_cover_staging_writes_and_terminal_parents() -> None:
    sql = _migration_sql()
    body = _trigger_bodies_text(sql)

    for event in ("insert", "update", "delete"):
        _assert_trigger_attached(sql, "rag_attraction_chunks", event)
    assert "rag_attraction_chunks" in body
    assert "rag_corpus_versions" in body
    assert "status = 'staging'" in body
    for value in ("active", "superseded", "failed"):
        assert f"'{value}'" in body
    assert re.search(r"raise\s+exception", body, flags=re.IGNORECASE)


def test_task2_chunk_immutable_fields_are_old_new_guarded_with_narrow_mutable_surface() -> None:
    body = _trigger_bodies_text(_migration_sql())

    assert "old" in body and "new" in body
    for field in (
        "corpus_version_id",
        "attraction_id",
        "chunk_key",
        "chunk_type",
        "ordinal",
        "content",
        "content_hash",
        "embedding_input_hash",
        "embedding_input_schema_version",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
        "embedding_model",
        "embedding_task",
        "embedding_dimensions",
    ):
        assert field in body
    for field in (
        "status",
        "embedding",
        "embedding_error_code",
        "embedding_error_message",
    ):
        assert field in body
    assert re.search(r"raise\s+exception", body, flags=re.IGNORECASE)


def test_task2_chunk_transition_guard_preserves_retry_graph_and_clears_retry_errors() -> None:
    body = _trigger_bodies_text(_migration_sql())

    for old, new in (
        ("pending", "embedded"),
        ("pending", "failed"),
        ("failed", "pending"),
    ):
        _assert_transition(body, old, new)
    for value in ("pending", "embedded", "failed"):
        assert f"'{value}'" in body
    assert "embedding_error_code = null" in body
    assert "embedding_error_message = null" in body
    assert "rag v2 chunk embedding transition is invalid" in body
    assert re.search(r"raise\s+exception", body, flags=re.IGNORECASE)
