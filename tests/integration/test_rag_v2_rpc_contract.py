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
