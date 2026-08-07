from pathlib import Path
import re
import sqlite3

import pytest


MIGRATIONS = Path(__file__).parents[2] / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "001_initial.sql"
OWNER_SCOPED_TABLES = (
    "profiles",
    "trips",
    "conversation_messages",
    "share_links",
    "ai_usage",
)
SERVICE_ROLE_TABLES = ("ai_usage_counters", "ai_usage_reservations")
PRIVATE_TABLES = OWNER_SCOPED_TABLES + SERVICE_ROLE_TABLES


def _migration() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MIGRATIONS.glob("*.sql"), key=lambda path: path.name)
    ).lower()


def _sql_statements(migration: str) -> list[str]:
    """Split PostgreSQL SQL outside comments, quoted strings and dollar bodies."""
    statements: list[str] = []
    current: list[str] = []
    index = 0
    state = "normal"
    dollar_tag = ""
    block_comment_depth = 0

    while index < len(migration):
        pair = migration[index : index + 2]
        char = migration[index]

        if state == "line_comment":
            if char == "\n":
                current.append(char)
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            if pair == "/*":
                block_comment_depth += 1
                index += 2
            elif pair == "*/":
                block_comment_depth -= 1
                index += 2
                if block_comment_depth == 0:
                    current.append(" ")
                    state = "normal"
            else:
                index += 1
            continue

        if state == "single_quote":
            current.append(char)
            if char == "'":
                if index + 1 < len(migration) and migration[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "double_quote":
            current.append(char)
            if char == '"':
                if index + 1 < len(migration) and migration[index + 1] == '"':
                    current.append('"')
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "dollar_quote":
            if migration.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                state = "normal"
            else:
                current.append(char)
                index += 1
            continue

        if pair == "--":
            state = "line_comment"
            index += 2
            continue
        if pair == "/*":
            state = "block_comment"
            block_comment_depth = 1
            index += 2
            continue
        if char == "'":
            state = "single_quote"
            current.append(char)
            index += 1
            continue
        if char == '"':
            state = "double_quote"
            current.append(char)
            index += 1
            continue
        if char == "$":
            match = re.match(r"\$(?:[a-z_][a-z0-9_]*)?\$", migration[index:])
            if match is not None:
                dollar_tag = match.group(0)
                state = "dollar_quote"
                current.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


_IDENTIFIER = r'(?:"(?:[^"]|"")+"|[a-z_][a-z0-9_$]*)'
_QUALIFIED_TABLE = (
    rf'(?P<schema>{_IDENTIFIER})\s*\.\s*(?P<table>{_IDENTIFIER})'
)


def _normalized_identifier(identifier: str) -> str:
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"').lower()
    return identifier.lower()


def _policy_blocks(migration: str) -> list[tuple[str, str]]:
    """Apply CREATE/DROP statements and return policies in their final order."""
    create_pattern = re.compile(
        rf'^create\s+policy\s+(?P<policy>{_IDENTIFIER})\s+on\s+'
        rf'{_QUALIFIED_TABLE}\s+(?P<body>[\s\S]+)$',
        re.IGNORECASE,
    )
    drop_pattern = re.compile(
        rf'^drop\s+policy(?:\s+if\s+exists)?\s+'
        rf'(?P<policy>{_IDENTIFIER})\s+on\s+{_QUALIFIED_TABLE}'
        r'(?:\s+(?:cascade|restrict))?$',
        re.IGNORECASE,
    )
    policies: dict[tuple[str, str], str] = {}
    for statement in _sql_statements(migration):
        create_match = create_pattern.match(statement)
        if (
            create_match is not None
            and _normalized_identifier(create_match.group("schema")) == "public"
        ):
            key = (
                _normalized_identifier(create_match.group("table")),
                _normalized_identifier(create_match.group("policy")),
            )
            policies[key] = create_match.group("body").strip().lower()
            continue

        drop_match = drop_pattern.match(statement)
        if (
            drop_match is not None
            and _normalized_identifier(drop_match.group("schema")) == "public"
        ):
            key = (
                _normalized_identifier(drop_match.group("table")),
                _normalized_identifier(drop_match.group("policy")),
            )
            policies.pop(key, None)

    return [(table, body) for (table, _policy), body in policies.items()]


def _assert_private_rls_contract(migration: str) -> None:
    statements = _sql_statements(migration)
    policies = _policy_blocks(migration)
    create_table = re.compile(
        rf'^create\s+table(?:\s+if\s+not\s+exists)?\s+'
        rf'{_QUALIFIED_TABLE}\b',
        re.IGNORECASE,
    )
    alter_rls = re.compile(
        rf'^alter\s+table(?:\s+only)?\s+{_QUALIFIED_TABLE}\s+'
        r'(?P<action>enable|disable)\s+row\s+level\s+security$',
        re.IGNORECASE,
    )
    alter_policy = re.compile(
        rf'^alter\s+policy\s+{_IDENTIFIER}\s+on\s+'
        rf'{_QUALIFIED_TABLE}\b',
        re.IGNORECASE,
    )
    created_tables: set[str] = set()
    rls_enabled = {table: False for table in PRIVATE_TABLES}

    for statement in statements:
        policy_match = alter_policy.match(statement)
        if (
            policy_match is not None
            and _normalized_identifier(policy_match.group("schema")) == "public"
            and _normalized_identifier(policy_match.group("table")) in PRIVATE_TABLES
        ):
            raise AssertionError(
                "ALTER POLICY on a private table requires explicit audit support"
            )

        create_match = create_table.match(statement)
        if (
            create_match is not None
            and _normalized_identifier(create_match.group("schema")) == "public"
        ):
            created_tables.add(_normalized_identifier(create_match.group("table")))

        alter_match = alter_rls.match(statement)
        if (
            alter_match is None
            or _normalized_identifier(alter_match.group("schema")) != "public"
        ):
            continue
        table = _normalized_identifier(alter_match.group("table"))
        if table not in rls_enabled:
            continue
        action = alter_match.group("action").lower()
        assert action != "disable", f"RLS disabled for private table {table}"
        rls_enabled[table] = True

    for table in PRIVATE_TABLES:
        assert table in created_tables
        assert rls_enabled[table]

    for table in OWNER_SCOPED_TABLES:
        table_policies = [body for policy_table, body in policies if policy_table == table]
        assert table_policies
        for policy in table_policies:
            assert re.search(r"\bfor\s+all\b", policy)
            assert re.search(
                r"\busing\s*\(\s*auth\.uid\(\)\s*=\s*user_id\s*\)", policy
            )
            assert re.search(
                r"\bwith\s+check\s*\(\s*auth\.uid\(\)\s*=\s*user_id\s*\)",
                policy,
            )

    for table in SERVICE_ROLE_TABLES:
        assert not [body for policy_table, body in policies if policy_table == table]


def test_policy_parser_does_not_skip_unquoted_policy_names():
    migration = _migration() + (
        "\ncreate policy weak on public.trips for select using (true);"
    )

    trip_policies = [
        body for table, body in _policy_blocks(migration) if table == "trips"
    ]

    assert len(trip_policies) == 2
    assert "using (true)" in trip_policies[1]


def test_contract_loader_includes_every_sorted_migration():
    migration = _migration()

    first = "create table if not exists public.ai_usage_counters"
    second = "create or replace function public.get_shared_trip_by_token_hash"
    third = "drop function if exists public.reserve_ai_usage"
    assert first in migration
    assert second in migration
    assert third in migration
    assert migration.index(first) < migration.index(second) < migration.index(third)


def test_policy_parser_handles_comments_quoted_identifiers_and_statement_boundaries():
    migration = """
    -- create policy fake on public.trips for select using (true);
    create function public.fake_policy_text() returns void language plpgsql as $$
    begin
      perform 'create policy fake on public.trips for select using (true);';
    end
    $$;
    create policy "owner policy" on "public"."trips"
      for all using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
    create policy weak on public.trips for select using (true);
    alter table public.trips enable row level security;
    """

    assert _policy_blocks(migration) == [
        (
            "trips",
            "for all using (auth.uid() = user_id)\n"
            "      with check (auth.uid() = user_id)",
        ),
        ("trips", "for select using (true)"),
    ]


@pytest.mark.parametrize(
    "later_migration",
    [
        'alter table "public"."trips" disable row level security;',
        'create policy weak on "public"."trips" for select using (true);',
        'alter table public.ai_usage_counters disable row level security;',
        'create policy weak on public.ai_usage_counters for select using (true);',
        'alter policy "users manage own trips" on public.trips using (true);',
        'drop policy "users manage own trips" on public.trips;',
    ],
)
def test_final_rls_contract_rejects_later_security_regressions(later_migration):
    with pytest.raises(AssertionError):
        _assert_private_rls_contract(_migration() + "\n" + later_migration.lower())


def test_private_user_tables_enable_rls_and_scope_to_authenticated_owner():
    """Removing an ownership policy must expose the missing isolation contract."""
    _assert_private_rls_contract(_migration())


def test_share_links_store_only_hashes_and_have_no_public_read_policy():
    """Storing plaintext tokens or public select policy must fail this security contract."""
    migration = _migration()

    assert "token_hash" in migration
    assert "token_hash text not null unique" in migration
    assert "create policy \"public" not in migration
    assert "using (true)" not in migration


@pytest.mark.parametrize("child_table", ["conversation_messages", "share_links"])
def test_cross_user_trip_references_are_rejected_by_composite_foreign_key(child_table):
    """Dropping tenant ownership from either trip foreign key must permit this insert."""
    migration = _migration()
    unique_match = re.search(
        r"unique\s*\(\s*(id\s*,\s*user_id)\s*\)", migration
    )
    fk_match = re.search(
        rf"create table public\.{child_table}\s*\([\s\S]*?"
        r"foreign key\s*\(\s*(trip_id\s*,\s*user_id)\s*\)\s*"
        r"references public\.trips\s*\(\s*(id\s*,\s*user_id)\s*\)",
        migration,
    )
    assert unique_match is not None
    assert fk_match is not None

    trip_key = unique_match.group(1)
    child_key, parent_key = fk_match.groups()
    database = sqlite3.connect(":memory:")
    database.execute("pragma foreign_keys = on")
    database.execute(
        f"create table trips (id text, user_id text, unique ({trip_key}))"
    )
    database.execute(
        f"create table child (trip_id text, user_id text, "
        f"foreign key ({child_key}) references trips ({parent_key}))"
    )
    database.execute("insert into trips values ('trip-a', 'user-a')")

    with pytest.raises(sqlite3.IntegrityError):
        database.execute("insert into child values ('trip-a', 'user-b')")
