from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "supabase" / "migrations" / "014_rag_v2.sql"
V2_TABLES = (
    "rag_corpus_versions",
    "rag_attractions",
    "rag_attraction_versions",
    "rag_attraction_chunks",
)
CRUD_PRIVILEGES = {"select", "insert", "update", "delete"}
END_USER_ROLES = {"public", "anon", "authenticated"}


def _migration_sql() -> str:
    assert MIGRATION_PATH.exists(), (
        "expected RAG V2 migration artifact does not yet exist"
    )
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def _privilege_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z_][a-z0-9_]*", value.lower()))


def _role_tokens(value: str) -> set[str]:
    return _privilege_tokens(value)


def _table_grants(sql: str) -> list[tuple[str, set[str], set[str]]]:
    return [
        (
            match.group("table"),
            _privilege_tokens(match.group("privileges")),
            _role_tokens(match.group("roles")),
        )
        for match in re.finditer(
            r"grant\s+(?P<privileges>[^;]+?)\s+on\s+table\s+"
            r"public\.(?P<table>[a-z0-9_]+)\s+to\s+(?P<roles>[^;]+);",
            sql,
            flags=re.IGNORECASE,
        )
    ]


def _table_revokes(sql: str) -> list[tuple[str, set[str], set[str]]]:
    return [
        (
            match.group("table"),
            _privilege_tokens(match.group("privileges")),
            _role_tokens(match.group("roles")),
        )
        for match in re.finditer(
            r"revoke\s+(?P<privileges>[^;]+?)\s+on\s+table\s+"
            r"public\.(?P<table>[a-z0-9_]+)\s+from\s+(?P<roles>[^;]+);",
            sql,
            flags=re.IGNORECASE,
        )
    ]


def _v2_policy_statements(sql: str) -> list[str]:
    policies = []
    for match in re.finditer(r"create\s+policy\b(?P<body>.*?);", sql, re.DOTALL):
        body = match.group("body")
        if any(
            re.search(rf"\bon\s+public\.{re.escape(table)}\b", body)
            for table in V2_TABLES
        ):
            policies.append(body)
    return policies


def test_rag_v2_tables_enable_rls() -> None:
    sql = _migration_sql()

    for table in V2_TABLES:
        assert re.search(
            rf"alter\s+table\s+public\.{re.escape(table)}\s+"
            r"enable\s+row\s+level\s+security\s*;",
            sql,
            flags=re.IGNORECASE,
        ), f"expected RAG V2 tables to enable row level security: {table}"


def test_rag_v2_tables_revoke_direct_crud_from_public_anon_and_authenticated() -> None:
    revokes = _table_revokes(_migration_sql())

    for table in V2_TABLES:
        table_revokes = [
            (privileges, roles)
            for revoked_table, privileges, roles in revokes
            if revoked_table == table
        ]
        for role in END_USER_ROLES:
            assert any(
                role in roles and ("all" in privileges or CRUD_PRIVILEGES <= privileges)
                for privileges, roles in table_revokes
            ), f"missing direct CRUD revoke for {role} on {table}"


def test_rag_v2_tables_grant_only_service_role_crud() -> None:
    grants = _table_grants(_migration_sql())

    for table in V2_TABLES:
        table_grants = [
            (privileges, roles)
            for granted_table, privileges, roles in grants
            if granted_table == table
        ]
        assert table_grants, f"missing V2 table grant for {table}"
        for privileges, roles in table_grants:
            assert roles == {"service_role"}, (
                f"V2 table grant has an unexpected role on {table}: {roles}"
            )
            assert privileges <= CRUD_PRIVILEGES, (
                f"V2 table grant has non-CRUD privileges on {table}: {privileges}"
            )
            assert "references" not in privileges
            assert "trigger" not in privileges
        assert any(
            roles == {"service_role"} and CRUD_PRIVILEGES <= privileges
            for privileges, roles in table_grants
        ), f"service_role lacks complete CRUD authority on {table}"


def test_v2_security_artifact_has_no_end_user_or_owner_policies() -> None:
    for policy in _v2_policy_statements(_migration_sql()):
        assert not re.search(r"\bto\s+(?:public|anon|authenticated)\b", policy)
        assert not re.search(r"\bauth\.uid\s*\(\)|\b(?:owner_id|user_id)\b", policy)


def test_v2_security_artifact_does_not_grant_sequence_privileges() -> None:
    sql = _migration_sql()

    assert not re.search(r"grant\s+[^;]+\s+on\s+sequence\b", sql)


def test_task3_v2_security_artifact_preserves_legacy_rag_security_isolation() -> None:
    sql = _migration_sql()

    forbidden_operations = (
        "alter table public.knowledge_chunks",
        "drop table public.knowledge_chunks",
        "drop function public.match_knowledge_chunks",
        "create or replace function public.match_knowledge_chunks",
    )
    for operation in forbidden_operations:
        assert operation not in sql
