from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "supabase" / "migrations" / "014_rag_v2.sql"
ACTIVATION_SIGNATURE = (
    "p_dataset_key text, "
    "p_corpus_version_id uuid, "
    "p_expected_active_corpus_version_id uuid default null"
)
EXECUTE_ROLES = {"public", "anon", "authenticated", "service_role"}


def _migration_sql() -> str:
    assert MIGRATION_PATH.exists(), (
        "expected RAG V2 migration artifact does not yet exist"
    )
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _activation_definition(sql: str) -> re.Match[str]:
    match = re.search(
        r"create\s+(?:or\s+replace\s+)?function\s+"
        r"public\.activate_rag_v2_corpus\s*\((?P<args>.*?)\)\s*"
        r"returns\s+(?P<returns>void)\b(?P<header>.*?)\bas\s+"
        r"\$(?P<tag>[a-z0-9_]*)\$(?P<body>.*?)\$(?P=tag)\$\s*;",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        "expected RAG V2 activation RPC exact three-argument signature"
    )
    return match


def _activation_body(sql: str) -> str:
    return _activation_definition(sql).group("body")


def _roles(value: str) -> set[str]:
    return set(re.findall(r"[a-z_][a-z0-9_]*", value.lower()))


def _execute_statements(sql: str, action: str) -> list[tuple[set[str], str]]:
    return [
        (_roles(match.group("roles")), match.group("signature"))
        for match in re.finditer(
            rf"{action}\s+execute\s+on\s+function\s+"
            r"public\.activate_rag_v2_corpus\s*"
            r"\((?P<signature>text\s*,\s*uuid\s*,\s*uuid)\)\s+"
            rf"(?:from|to)\s+(?P<roles>[^;]+);",
            sql,
            flags=re.IGNORECASE,
        )
    ]


def _assert_before(body: str, earlier: str, later: str) -> None:
    earlier_position = body.find(earlier)
    later_position = body.find(later)
    assert earlier_position >= 0, f"missing activation fragment: {earlier}"
    assert later_position >= 0, f"missing activation fragment: {later}"
    assert earlier_position < later_position, (
        f"activation fragment order is invalid: {earlier} must precede {later}"
    )


def _corpus_update_statements(body: str) -> list[re.Match[str]]:
    return list(
        re.finditer(
            r"update\s+public\.rag_corpus_versions\b[^;]*;",
            body,
            flags=re.IGNORECASE,
        )
    )


def _has_included_embedded_per_attraction_check(body: str) -> bool:
    nested_not_exists = re.search(
        r"not\s+exists\s*\(\s*select[\s\S]{0,1400}?"
        r"from\s+public\.rag_attraction_versions[\s\S]{0,1400}?"
        r"status\s*=\s*'included'[\s\S]{0,1400}?"
        r"not\s+exists\s*\(\s*select[\s\S]{0,1400}?"
        r"from\s+public\.rag_attraction_chunks[\s\S]{0,1400}?"
        r"status\s*=\s*'embedded'[\s\S]{0,1400}?"
        r"(?:corpus_version_id|attraction_id)",
        body,
        flags=re.IGNORECASE,
    )
    grouped_condition = re.search(
        r"from\s+public\.rag_attraction_versions[\s\S]{0,1800}?"
        r"status\s*=\s*'included'[\s\S]{0,1800}?"
        r"public\.rag_attraction_chunks[\s\S]{0,1800}?"
        r"group\s+by[\s\S]{0,600}?"
        r"having[\s\S]{0,600}?(?:embedded|count)",
        body,
        flags=re.IGNORECASE,
    )
    return nested_not_exists is not None or grouped_condition is not None


def _has_active_stable_attraction_gate(body: str) -> bool:
    invalid_lifecycle_gate = re.search(
        r"rag_attraction_versions[\s\S]{0,1200}?"
        r"rag_attractions[\s\S]{0,1200}?"
        r"status\s*=\s*'included'[\s\S]{0,900}?"
        r"lifecycle_status\s*(?:<>|!=)\s*'active'[\s\S]{0,500}?"
        r"rag_v2_invalid_lifecycle:",
        body,
        flags=re.IGNORECASE,
    )
    active_only_gate = re.search(
        r"rag_attraction_versions[\s\S]{0,1200}?"
        r"not\s+exists\s*\([\s\S]{0,1200}?"
        r"rag_attractions[\s\S]{0,1200}?"
        r"lifecycle_status\s*=\s*'active'[\s\S]{0,500}?"
        r"rag_v2_invalid_lifecycle:",
        body,
        flags=re.IGNORECASE,
    )
    return invalid_lifecycle_gate is not None or active_only_gate is not None


def test_activate_rag_v2_corpus_exact_signature_is_declared() -> None:
    definition = _activation_definition(_migration_sql())

    assert _normalized(definition.group("args")) == ACTIVATION_SIGNATURE
    assert definition.group("returns").lower() == "void"


def test_activation_rpc_has_security_definer_and_public_search_path() -> None:
    definition = _activation_definition(_migration_sql())
    header = _normalized(definition.group("header"))

    assert re.search(r"\bsecurity\s+definer\b", header)
    assert re.search(r"\bset\s+search_path\s*=\s*public\b", header)
    assert not re.search(r"\bsecurity\s+invoker\b", header)


def test_activation_rpc_has_service_role_only_execute_boundary() -> None:
    sql = _migration_sql()
    revokes = _execute_statements(sql, "revoke")
    grants = _execute_statements(sql, "grant")

    assert revokes
    assert grants
    assert all(signature.replace(" ", "") == "text,uuid,uuid" for _, signature in revokes)
    assert all(signature.replace(" ", "") == "text,uuid,uuid" for _, signature in grants)
    revoked_roles = set().union(*(roles for roles, _ in revokes))
    granted_roles = set().union(*(roles for roles, _ in grants))
    assert {"public", "anon", "authenticated"} <= revoked_roles
    assert granted_roles == {"service_role"}
    assert revoked_roles <= EXECUTE_ROLES


def test_activation_uses_dataset_keyed_transaction_advisory_lock() -> None:
    body = _activation_body(_migration_sql())

    assert re.search(
        r"pg_catalog\.pg_advisory_xact_lock\s*\(\s*"
        r"pg_catalog\.hashtextextended\s*\(\s*"
        r"'rag-v2-corpus:'\s*\|\|\s*p_dataset_key\s*,\s*0\s*\)",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )


def test_activation_loads_target_and_validates_dataset_ownership() -> None:
    body = _activation_body(_migration_sql())

    assert re.search(
        r"from\s+public\.rag_corpus_versions[\s\S]{0,1000}?"
        r"corpus_version_id\s*=\s*p_corpus_version_id",
        body,
        flags=re.IGNORECASE,
    )
    assert "rag_v2_not_found:" in body
    assert re.search(
        r"dataset_key[\s\S]{0,300}?p_dataset_key|"
        r"p_dataset_key[\s\S]{0,300}?dataset_key",
        body,
        flags=re.IGNORECASE,
    )
    assert "rag_v2_version_conflict:" in body


def test_activation_rereads_current_active_for_dataset() -> None:
    body = _activation_body(_migration_sql())

    assert re.search(
        r"from\s+public\.rag_corpus_versions[\s\S]{0,1000}?"
        r"status\s*=\s*'active'[\s\S]{0,1000}?"
        r"dataset_key[\s\S]{0,300}?p_dataset_key|"
        r"from\s+public\.rag_corpus_versions[\s\S]{0,1000}?"
        r"p_dataset_key[\s\S]{0,300}?dataset_key[\s\S]{0,1000}?"
        r"status\s*=\s*'active'",
        body,
        flags=re.IGNORECASE,
    )


def test_already_current_target_returns_before_expected_active_cas() -> None:
    body = _activation_body(_migration_sql())

    idempotent_branch = re.search(
        r"\bif\s+(?P<condition>[^;\n]*"
        r"(?:current[a-z_]*\s*=\s*p_corpus_version_id|"
        r"p_corpus_version_id\s*=\s*current[a-z_]*)"
        r"[^;\n]*)\s+then\s+return\s*;",
        body,
        flags=re.IGNORECASE,
    )
    assert idempotent_branch is not None, (
        "missing already-current activation idempotent return branch"
    )
    cas_mismatch = re.search(
        r"p_expected_active_corpus_version_id[\s\S]{0,900}?"
        r"rag_v2_activation_conflict:",
        body,
        flags=re.IGNORECASE,
    )
    assert cas_mismatch is not None, "missing expected-active CAS conflict branch"
    not_found = body.find("rag_v2_not_found:")
    version_conflict = body.find("rag_v2_version_conflict:")
    assert not_found >= 0, "missing target-not-found branch"
    assert version_conflict >= 0, "missing dataset-conflict branch"
    assert not_found < version_conflict < idempotent_branch.start() < cas_mismatch.start()
    assert body.find("pg_catalog.pg_advisory_xact_lock") < idempotent_branch.start()


def test_expected_null_requires_no_current_active_or_raises_cas_conflict() -> None:
    body = _activation_body(_migration_sql())

    assert re.search(
        r"p_expected_active_corpus_version_id\s+is\s+null[\s\S]{0,800}?"
        r"(?:current|active)[\s\S]{0,400}?(?:is\s+not\s+null|is\s+distinct)[\s\S]{0,400}?"
        r"rag_v2_activation_conflict:",
        body,
        flags=re.IGNORECASE,
    )


def test_expected_value_requires_exact_current_active_or_raises_cas_conflict() -> None:
    body = _activation_body(_migration_sql())

    assert re.search(
        r"p_expected_active_corpus_version_id\s+is\s+not\s+null[\s\S]{0,1000}?"
        r"(?:<>|!=|is\s+distinct\s+from|is\s+null)[\s\S]{0,500}?"
        r"rag_v2_activation_conflict:",
        body,
        flags=re.IGNORECASE,
    )


def test_activation_requires_staging_target_after_idempotency_and_cas() -> None:
    body = _activation_body(_migration_sql())

    _assert_before(body, "rag_v2_activation_conflict:", "status = 'staging'")
    assert "rag_v2_invalid_lifecycle:" in body
    assert "status = 'staging'" in body


def test_activation_requires_at_least_one_included_attraction_version() -> None:
    body = _activation_body(_migration_sql())

    assert "rag_attraction_versions" in body
    assert "status = 'included'" in body
    assert "rag_v2_invalid_lifecycle:" in body
    assert re.search(
        r"not\s+exists[\s\S]{0,1200}?rag_attraction_versions[\s\S]{0,1000}?"
        r"status\s*=\s*'included'|"
        r"count\s*\([^)]+\)[\s\S]{0,500}?status\s*=\s*'included'",
        body,
        flags=re.IGNORECASE,
    )


def test_activation_requires_embedded_evidence_for_every_included_attraction() -> None:
    body = _activation_body(_migration_sql())

    assert _has_included_embedded_per_attraction_check(body), (
        "activation must check embedded evidence per included attraction"
    )
    assert "rag_v2_invalid_lifecycle:" in body


def test_activation_blocks_pending_failed_included_chunks_but_allows_excluded() -> None:
    body = _activation_body(_migration_sql())

    assert re.search(r"pending[\s\S]{0,500}?failed|failed[\s\S]{0,500}?pending", body)
    assert "rag_attraction_versions" in body
    assert "rag_attraction_chunks" in body
    assert "status = 'included'" in body
    assert "rag_v2_invalid_lifecycle:" in body
    assert re.search(
        r"(?:status\s+in\s*\(\s*'pending'\s*,\s*'failed'\s*\)|"
        r"status\s*=\s*'pending'[\s\S]{0,300}?status\s*=\s*'failed')",
        body,
        flags=re.IGNORECASE,
    )


def test_activation_requires_active_stable_attractions_for_included_versions() -> None:
    body = _activation_body(_migration_sql())

    assert _has_active_stable_attraction_gate(body), (
        "activation must reject included versions whose stable attraction is not active"
    )


def test_activation_requires_non_empty_included_chunk_provenance() -> None:
    body = _activation_body(_migration_sql())

    for field in ("source_label", "source_url", "source_type"):
        assert re.search(rf"btrim\s*\(\s*{field}\s*\)\s*<>\s*''", body)


def test_activation_relies_on_existing_row_checks_without_manifest_recomputation() -> None:
    body = _activation_body(_migration_sql())

    assert not re.search(
        r"\b(?:sha256|digest|json_build_object|jsonb_build_object)\b|"
        r"\bencode\s*\(",
        body,
        flags=re.IGNORECASE,
    )
    assert not re.search(
        r"\b(?:expected_attraction_count|expected_chunk_count|"
        r"expected_embedded_count|validated)\b",
        body,
        flags=re.IGNORECASE,
    )


def test_activation_updates_superseded_before_active_with_timestamps() -> None:
    body = _activation_body(_migration_sql())

    statements = _corpus_update_statements(body)
    supersede = [
        statement
        for statement in statements
        if re.search(r"status\s*=\s*'superseded'", statement.group(), re.IGNORECASE)
        and re.search(r"superseded_at\s*=\s*now\s*\(\)", statement.group(), re.IGNORECASE)
    ]
    activate = [
        statement
        for statement in statements
        if re.search(r"status\s*=\s*'active'", statement.group(), re.IGNORECASE)
        and re.search(r"activated_at\s*=\s*now\s*\(\)", statement.group(), re.IGNORECASE)
    ]
    assert supersede, "missing current-active supersession update"
    assert activate, "missing target activation update"
    assert supersede[0].start() < activate[0].start()


def test_activation_uses_stable_error_prefixes_and_no_explicit_transaction_control() -> None:
    body = _activation_body(_migration_sql())

    for prefix in (
        "rag_v2_not_found:",
        "rag_v2_version_conflict:",
        "rag_v2_invalid_lifecycle:",
        "rag_v2_activation_conflict:",
    ):
        assert prefix in body
    assert not re.search(
        r"\bbegin\s+transaction\b|\bcommit\b|\brollback\b",
        body,
        flags=re.IGNORECASE,
    )


MATCH_SIGNATURE = (
    "p_dataset_key text, "
    "p_query_embedding vector(1024), "
    "p_destination_code text default null, "
    "p_destination_level text default null, "
    "p_province_code text default null, "
    "p_attraction_id uuid default null, "
    "p_candidate_k integer default 40"
)
MATCH_PRIVILEGE_SIGNATURE = (
    r"text\s*,\s*vector\s*,\s*text\s*,\s*text\s*,\s*text\s*,\s*uuid\s*,\s*integer"
)
MATCH_RETURN_COLUMNS = (
    "corpus_version_id uuid, "
    "attraction_id uuid, "
    "chunk_key text, "
    "chunk_type text, "
    "content text, "
    "content_hash text, "
    "source_label text, "
    "source_url text, "
    "source_type text, "
    "reviewed_on date, "
    "score real"
)


def _match_definition(sql: str) -> re.Match[str]:
    match = re.search(
        r"create\s+(?:or\s+replace\s+)?function\s+"
        r"public\.match_rag_v2_chunks\s*\((?P<args>.*?)\)\s*"
        r"returns\s+table\s*\((?P<returns>.*?)\)\s*"
        r"(?P<header>.*?)\bas\s+\$(?P<tag>[a-z0-9_]*)\$"
        r"(?P<body>.*?)\$(?P=tag)\$\s*;",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        "expected RAG V2 candidate retrieval RPC exact seven-argument signature"
    )
    return match


def _match_body(sql: str) -> str:
    return _match_definition(sql).group("body")


def _match_execute_statements(
    sql: str,
    action: str,
) -> list[tuple[set[str], str]]:
    return [
        (_roles(match.group("roles")), match.group("signature"))
        for match in re.finditer(
            rf"{action}\s+execute\s+on\s+function\s+"
            r"public\.match_rag_v2_chunks\s*"
            rf"\(\s*(?P<signature>{MATCH_PRIVILEGE_SIGNATURE})\s*\)\s+"
            rf"(?:from|to)\s+(?P<roles>[^;]+);",
            sql,
            flags=re.IGNORECASE,
        )
    ]


def _hnsw_index_statements(sql: str) -> list[re.Match[str]]:
    return list(
        re.finditer(
            r"create\s+index\s+[a-z0-9_]+\s+on\s+"
            r"public\.rag_attraction_chunks\s+using\s+hnsw\s*"
            r"\((?P<columns>.*?)\)\s+where\s+(?P<predicate>.*?);",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def _has_exact_filter(body: str, parameter: str, column: str) -> bool:
    has_null_branch = re.search(
        rf"\b{parameter}\s+is\s+null\b",
        body,
        flags=re.IGNORECASE,
    )
    has_equality = re.search(
        rf"(?:\b{column}\b\s*=\s*\b{parameter}\b|"
        rf"\b{parameter}\b\s*=\s*\b{column}\b)",
        body,
        flags=re.IGNORECASE,
    )
    return has_null_branch is not None and has_equality is not None


def test_match_rag_v2_chunks_exact_signature_is_declared() -> None:
    definition = _match_definition(_migration_sql())

    assert _normalized(definition.group("args")) == MATCH_SIGNATURE


def test_match_rag_v2_chunks_returns_exact_eleven_columns() -> None:
    definition = _match_definition(_migration_sql())

    assert _normalized(definition.group("returns")) == MATCH_RETURN_COLUMNS
    assert "distance" not in definition.group("returns").lower()
    assert "embedding" not in definition.group("returns").lower()
    assert "destination" not in definition.group("returns").lower()


def test_match_rag_v2_chunks_is_security_invoker() -> None:
    definition = _match_definition(_migration_sql())
    header = _normalized(definition.group("header"))

    assert re.search(r"\bsecurity\s+invoker\b", header)
    assert not re.search(r"\bsecurity\s+definer\b", header)


def test_match_rag_v2_chunks_has_service_role_only_execute_boundary() -> None:
    sql = _migration_sql()
    revokes = _match_execute_statements(sql, "revoke")
    grants = _match_execute_statements(sql, "grant")

    assert revokes
    assert grants
    revoked_roles = set().union(*(roles for roles, _ in revokes))
    granted_roles = set().union(*(roles for roles, _ in grants))
    assert {"public", "anon", "authenticated"} <= revoked_roles
    assert granted_roles == {"service_role"}
    assert revoked_roles <= EXECUTE_ROLES


def test_match_rag_v2_chunks_rejects_null_and_out_of_range_candidate_k() -> None:
    body = _match_body(_migration_sql())

    for condition in (
        r"p_candidate_k\s+is\s+null",
        r"p_candidate_k\s*<\s*1",
        r"p_candidate_k\s*>\s*200",
    ):
        assert re.search(condition, body, flags=re.IGNORECASE)
    assert re.search(r"raise\s+exception", body, flags=re.IGNORECASE)
    assert not re.search(r"coalesce\s*\(\s*p_candidate_k", body, re.IGNORECASE)


def test_match_rag_v2_chunks_rejects_null_query_embedding_before_ranking() -> None:
    body = _match_body(_migration_sql())

    assert re.search(
        r"p_query_embedding\s+is\s+null[\s\S]{0,400}?raise\s+exception",
        body,
        flags=re.IGNORECASE,
    )


def test_match_rag_v2_chunks_distinguishes_zero_one_and_multiple_active_corpora() -> None:
    body = _match_body(_migration_sql())

    assert re.search(
        r"rag_corpus_versions[\s\S]{0,1800}?status\s*=\s*'active'",
        body,
        flags=re.IGNORECASE,
    )
    assert not re.search(
        r"select\s+into\s+strict[\s\S]{0,1200}?status\s*=\s*'active'",
        body,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"(?:count\s*\(|array_agg\s*\(|cardinality\s*\(|array_length\s*\(|"
        r"active_count|active_corpus_count)",
        body,
        flags=re.IGNORECASE,
    )
    assert re.search(r"(?:>\s*1|>=\s*2)", body)
    assert re.search(r"(?:=\s*0|is\s+null|coalesce\s*\([^)]*,\s*0\))", body)
    assert not re.search(
        r"from\s+public\.rag_corpus_versions[\s\S]{0,600}?"
        r"status\s*=\s*'active'[\s\S]{0,300}?limit\s+1",
        body,
        flags=re.IGNORECASE,
    )


def test_match_rag_v2_chunks_requires_included_active_stable_candidates() -> None:
    body = _match_body(_migration_sql())

    assert re.search(
        r"rag_attraction_versions[\s\S]{0,2500}?"
        r"rag_attractions[\s\S]{0,2500}?"
        r"status\s*=\s*'included'",
        body,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"lifecycle_status\s*(?:=|<>|!=)\s*'active'|"
        r"not\s+exists[\s\S]{0,1200}?lifecycle_status\s*=\s*'active'",
        body,
        flags=re.IGNORECASE,
    )


def test_match_rag_v2_chunks_requires_embedded_non_null_vectors_in_candidate_relation() -> None:
    body = _match_body(_migration_sql())

    assert re.search(
        r"rag_attraction_chunks[\s\S]{0,2200}?"
        r"status\s*=\s*'embedded'[\s\S]{0,700}?"
        r"embedding\s+is\s+not\s+null",
        body,
        flags=re.IGNORECASE,
    )


def test_match_rag_v2_chunks_applies_all_optional_filters_as_exact_nullable_filters() -> None:
    body = _match_body(_migration_sql())

    for parameter, column in (
        ("p_destination_code", "destination_code"),
        ("p_destination_level", "destination_level"),
        ("p_province_code", "province_code"),
        ("p_attraction_id", "attraction_id"),
    ):
        assert _has_exact_filter(body, parameter, column), (
            f"missing NULL/exact filter contract for {parameter}"
        )


def test_match_rag_v2_chunks_filters_before_inner_ann_limit() -> None:
    body = _match_body(_migration_sql())
    distance_order = re.search(
        r"order\s+by[\s\S]{0,250}?embedding\s*<=>\s*p_query_embedding"
        r"\s*(?:asc\b)?",
        body,
        flags=re.IGNORECASE,
    )
    assert distance_order is not None, "missing inner raw-distance ANN ordering"
    limit_position = body.find("limit p_candidate_k", distance_order.end())
    assert limit_position >= 0, "missing inner candidate_k limit"

    for witness in (
        "status = 'included'",
        "status = 'embedded'",
        "embedding is not null",
        "p_destination_code",
        "p_destination_level",
        "p_province_code",
        "p_attraction_id",
    ):
        assert body.find(witness) < distance_order.start(), (
            f"metadata/lifecycle witness occurs after ANN ordering: {witness}"
        )


def test_match_rag_v2_chunks_uses_raw_cosine_distance_for_inner_ann_order() -> None:
    body = _match_body(_migration_sql())

    assert re.search(
        r"order\s+by[\s\S]{0,250}?embedding\s*<=>\s*p_query_embedding"
        r"\s+asc[\s\S]{0,200}?limit\s+p_candidate_k",
        body,
        flags=re.IGNORECASE,
    )
    assert not re.search(
        r"order\s+by\s+1\s*-\s*\(?\s*embedding\s*<=>\s*"
        r"p_query_embedding\s*\)?\s+desc[\s\S]{0,200}?limit\s+p_candidate_k",
        body,
        flags=re.IGNORECASE,
    )


def test_match_rag_v2_chunks_projects_explicit_real_score() -> None:
    body = _match_body(_migration_sql())

    assert re.search(
        r"1\s*-\s*(?:distance|[a-z_][a-z0-9_]*\.embedding\s*<=>\s*"
        r"p_query_embedding)[^,;]{0,100}?::\s*real\s+as\s+score",
        body,
        flags=re.IGNORECASE,
    ) or re.search(
        r"cast\s*\([\s\S]{0,250}?1\s*-\s*[\s\S]{0,180}?"
        r"as\s+real\s*\)[\s\S]{0,80}?as\s+score",
        body,
        flags=re.IGNORECASE,
    )


def test_match_rag_v2_chunks_orders_selected_rows_deterministically() -> None:
    body = _match_body(_migration_sql())
    inner_order = re.search(
        r"order\s+by[\s\S]{0,250}?embedding\s*<=>\s*p_query_embedding"
        r"\s+asc[\s\S]{0,200}?limit\s+p_candidate_k",
        body,
        flags=re.IGNORECASE,
    )
    outer_order = re.search(
        r"order\s+by[\s\S]{0,250}?score\s+desc[\s\S]{0,250}?"
        r"attraction_id\s+asc[\s\S]{0,250}?chunk_key\s+asc",
        body,
        flags=re.IGNORECASE,
    )
    assert inner_order is not None
    assert outer_order is not None
    assert inner_order.start() < outer_order.start()


def test_match_rag_v2_chunks_candidate_k_is_after_all_approved_filters() -> None:
    body = _match_body(_migration_sql())

    inner_order = re.search(
        r"order\s+by[\s\S]{0,250}?embedding\s*<=>\s*p_query_embedding"
        r"\s+asc[\s\S]{0,200}?limit\s+p_candidate_k",
        body,
        flags=re.IGNORECASE,
    )
    assert inner_order is not None
    assert body.find("status = 'included'") < inner_order.start()
    assert body.find("status = 'embedded'") < inner_order.start()
    assert body.find("embedding is not null") < inner_order.start()


def test_match_rag_v2_chunks_declares_default_hnsw_cosine_partial_index() -> None:
    sql = _migration_sql()
    indexes = _hnsw_index_statements(sql)

    assert len(indexes) == 1
    statement = indexes[0]
    assert "vector_cosine_ops" in statement.group("columns")
    assert "embedding is not null" in statement.group("predicate")
    assert "status = 'embedded'" in statement.group("predicate")
    assert "with (" not in statement.group().lower()
    assert "ivfflat" not in statement.group().lower()


def test_match_rag_v2_chunks_hnsw_predicate_is_chunk_local() -> None:
    sql = _migration_sql()
    indexes = _hnsw_index_statements(sql)

    assert indexes
    predicate = indexes[0].group("predicate").lower()
    assert "rag_corpus_versions" not in predicate
    assert "status = 'embedded'" in predicate
    assert "embedding is not null" in predicate


def test_match_rag_v2_chunks_declares_approved_btree_indexes() -> None:
    sql = _migration_sql()

    assert re.search(
        r"create\s+index\s+[a-z0-9_]+\s+on\s+"
        r"public\.rag_attraction_versions\s*\(\s*"
        r"corpus_version_id\s*,\s*destination_code\s*,\s*destination_level\s*\)",
        sql,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"create\s+index\s+[a-z0-9_]+\s+on\s+"
        r"public\.rag_attraction_versions\s*\(\s*"
        r"corpus_version_id\s*,\s*province_code\s*\)",
        sql,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"create\s+index\s+[a-z0-9_]+\s+on\s+"
        r"public\.rag_attraction_chunks\s*\(\s*"
        r"corpus_version_id\s*,\s*status\s*\)",
        sql,
        flags=re.IGNORECASE,
    )


def test_match_rag_v2_chunks_has_no_later_ranking_or_state_mutation() -> None:
    body = _match_body(_migration_sql()).lower()

    for forbidden in (
        "threshold",
        "score >= 0.70",
        "distinct on (content_hash)",
        "group by content_hash",
        "final_k",
        "diversity",
        "jina",
        "retrieval.query",
        "planner",
    ):
        assert forbidden not in body
    assert not re.search(r"\b(?:insert|update|delete)\b", body)


def test_match_rag_v2_chunks_preserves_legacy_and_activation_isolation() -> None:
    sql = _migration_sql()

    for forbidden in (
        "alter table public.knowledge_chunks",
        "drop table public.knowledge_chunks",
        "drop function public.match_knowledge_chunks",
        "create or replace function public.match_knowledge_chunks",
    ):
        assert forbidden not in sql
    assert sql.count("create or replace function public.activate_rag_v2_corpus") == 1
    assert "match_rag_v2_chunks" in sql


REPOSITORY_PATH = PROJECT_ROOT / "app" / "rag_v2" / "repository.py"


def _repository_source() -> str:
    return REPOSITORY_PATH.read_text(encoding="utf-8")


def _repository_method_source(source: str, method_name: str) -> str:
    match = re.search(
        rf"(?ms)^    def {re.escape(method_name)}\(.*?(?=^    def |\Z)",
        source,
    )
    assert match is not None
    return match.group()


def test_task8_repository_declares_candidate_and_typed_rpc_methods() -> None:
    source = _repository_source()

    assert "class RagV2Candidate" in source
    assert "def activate_corpus" in source
    assert "def match_chunks" in source
    for forbidden in (
        "def raw_rpc",
        "def call_rpc",
    ):
        assert forbidden not in source


def test_task8_activation_repository_uses_exact_three_parameter_rpc() -> None:
    body = _repository_method_source(_repository_source(), "activate_corpus")

    assert 'self._client.rpc("activate_rag_v2_corpus"' in body
    for parameter in (
        '"p_dataset_key"',
        '"p_corpus_version_id"',
        '"p_expected_active_corpus_version_id"',
    ):
        assert parameter in body
    assert "expected_active_corpus_version_id" in body
    assert ".table(" not in body


def test_task8_match_repository_uses_exact_seven_parameter_rpc() -> None:
    body = _repository_method_source(_repository_source(), "match_chunks")

    assert 'self._client.rpc("match_rag_v2_chunks"' in body
    for parameter in (
        '"p_dataset_key"',
        '"p_query_embedding"',
        '"p_destination_code"',
        '"p_destination_level"',
        '"p_province_code"',
        '"p_attraction_id"',
        '"p_candidate_k"',
    ):
        assert parameter in body
    assert ".table(" not in body


def test_task8_repository_does_not_expose_generic_rpc_or_retrieval_reuse() -> None:
    source = _repository_source()

    for forbidden in (
        "def raw_rpc",
        "def call_rpc",
        "def execute_sql",
        "def generic_rpc",
    ):
        assert forbidden not in source
