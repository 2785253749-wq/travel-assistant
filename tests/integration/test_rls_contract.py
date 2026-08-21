from pathlib import Path
import re
import sqlite3

import pytest


MIGRATIONS = Path(__file__).parents[2] / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "001_initial.sql"
OWNER_SCOPED_TABLES = {
    "profiles": "user_id",
    "trips": "user_id",
    "conversation_messages": "user_id",
    "share_links": "user_id",
    "ai_usage": "user_id",
    "travel_notes": "author_id",
    "travel_note_images": "owner_id",
}
OWNER_DELETE_ONLY_TABLES = {"community_posts": "user_id"}
OWNER_INSERT_DELETE_TABLES = {
    "travel_note_likes": "user_id",
    "travel_note_bookmarks": "user_id",
}
OWNER_INSERT_ONLY_TABLES = {
    "travel_note_comments": "author_id",
    "travel_note_reports": "reporter_id",
}
SERVICE_ROLE_TABLES = (
    "ai_usage_counters",
    "ai_usage_reservations",
    "ai_model_cost_counters",
    "user_roles",
    "moderation_decisions",
    "community_media_cleanup_jobs",
)
PRIVATE_TABLES = (
    tuple(OWNER_SCOPED_TABLES)
    + tuple(OWNER_DELETE_ONLY_TABLES)
    + tuple(OWNER_INSERT_DELETE_TABLES)
    + tuple(OWNER_INSERT_ONLY_TABLES)
    + SERVICE_ROLE_TABLES
)
AUDITED_PRIVATE_TABLE_ALTERS = {
    (
        "ai_usage_reservations",
        "add column if not exists reserved_model_calls integer not null "
        "default 1 check (reserved_model_calls between 1 and 2)",
    ),
    (
        "ai_usage_reservations",
        "add column if not exists incurred_model_calls integer not null "
        "default 0 check (incurred_model_calls between 0 and 2)",
    ),
    ("profiles", "alter column preferences set default '{}'::jsonb"),
    (
        "profiles",
        "add constraint profiles_preferences_is_object "
        "check (jsonb_typeof(preferences) = 'object')",
    ),
    (
        "profiles",
        "add constraint profiles_preferences_bio_is_valid "
        "check (not (preferences ? 'bio') or "
        "(jsonb_typeof(preferences -> 'bio') = 'string' and "
        "char_length(btrim(preferences ->> 'bio')) <= 160))",
    ),
    (
        "profiles",
        "add constraint profiles_preferences_home_city_is_valid "
        "check (not (preferences ? 'home_city') or "
        "(jsonb_typeof(preferences -> 'home_city') = 'string' and "
        "char_length(btrim(preferences ->> 'home_city')) <= 40))",
    ),
    (
        "profiles",
        "add constraint profiles_preferences_travel_styles_are_valid "
        "check (not (preferences ? 'travel_styles') or "
        "(jsonb_typeof(preferences -> 'travel_styles') = 'array' and "
        "jsonb_array_length(preferences -> 'travel_styles') <= 5 and "
        "public.profile_travel_styles_are_valid(preferences -> 'travel_styles')))",
    ),
    ("profiles", "add column if not exists creator_slug text"),
    ("profiles", "add column if not exists avatar_path text"),
    (
        "profiles",
        "alter column creator_slug set default public.generate_creator_slug()",
    ),
    ("profiles", "alter column creator_slug set not null"),
    (
        "profiles",
        "add constraint profiles_creator_slug_format "
        "check (creator_slug ~ '^[a-z0-9-]{8,40}$')",
    ),
    (
        "profiles",
        "add constraint profiles_avatar_path_length "
        "check (avatar_path is null or char_length(btrim(avatar_path)) "
        "between 5 and 500)",
    ),
}


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


def _next_sql_token(
    statement: str, index: int
) -> tuple[str, str, int, int] | None:
    while index < len(statement) and statement[index].isspace():
        index += 1
    if index == len(statement):
        return None

    start = index
    char = statement[index]
    if (
        char.lower() == "u"
        and statement[index + 1 : index + 3] == '&"'
    ):
        index += 3
        while index < len(statement):
            if statement[index] != '"':
                index += 1
                continue
            if index + 1 < len(statement) and statement[index + 1] == '"':
                index += 2
                continue
            index += 1
            return ("unicode_identifier", statement[start:index], start, index)
        raise AssertionError("unterminated Unicode identifier in ALTER TABLE")

    if char == '"':
        index += 1
        while index < len(statement):
            if statement[index] != '"':
                index += 1
                continue
            if index + 1 < len(statement) and statement[index + 1] == '"':
                index += 2
                continue
            index += 1
            return ("identifier", statement[start:index], start, index)
        raise AssertionError("unterminated quoted identifier in ALTER TABLE")

    if char == "_" or char.isalpha():
        index += 1
        while index < len(statement):
            char = statement[index]
            if char == "_" or char == "$" or char.isalnum():
                index += 1
                continue
            break
        return ("identifier", statement[start:index], start, index)

    return ("punctuation", char, start, index + 1)


def _is_keyword(token: tuple[str, str, int, int] | None, keyword: str) -> bool:
    return (
        token is not None
        and token[0] == "identifier"
        and not token[1].startswith('"')
        and token[1].lower() == keyword
    )


def _alter_table_target(statement: str) -> tuple[str | None, str, str] | None:
    """Read the ALTER TABLE target using PostgreSQL token boundaries."""
    token = _next_sql_token(statement, 0)
    if not _is_keyword(token, "alter"):
        return None
    token = _next_sql_token(statement, token[3])
    if not _is_keyword(token, "table"):
        return None

    token = _next_sql_token(statement, token[3])
    if _is_keyword(token, "if"):
        token = _next_sql_token(statement, token[3])
        if not _is_keyword(token, "exists"):
            raise AssertionError("unmodeled ALTER TABLE IF clause")
        token = _next_sql_token(statement, token[3])

    if _is_keyword(token, "only"):
        token = _next_sql_token(statement, token[3])

    parenthesized = token is not None and token[1] == "("
    if parenthesized:
        token = _next_sql_token(statement, token[3])
    if token is None or token[0] != "identifier":
        raise AssertionError("unmodeled ALTER TABLE target")

    identifiers = [token[1]]
    token = _next_sql_token(statement, token[3])
    while token is not None and token[1] == ".":
        token = _next_sql_token(statement, token[3])
        if token is None or token[0] != "identifier":
            raise AssertionError("invalid qualified ALTER TABLE target")
        identifiers.append(token[1])
        token = _next_sql_token(statement, token[3])
    if len(identifiers) > 3:
        raise AssertionError("unmodeled qualified ALTER TABLE target")

    if token is not None and token[1] == "*":
        token = _next_sql_token(statement, token[3])
    if parenthesized:
        if token is None or token[1] != ")":
            raise AssertionError("unterminated parenthesized ALTER TABLE target")
        token = _next_sql_token(statement, token[3])
        if token is not None and token[1] == "*":
            token = _next_sql_token(statement, token[3])

    action_start = token[2] if token is not None else len(statement)
    actions = statement[action_start:].strip()
    normalized = [_normalized_identifier(identifier) for identifier in identifiers]
    schema = normalized[-2] if len(normalized) >= 2 else None
    return schema, normalized[-1], actions


def _normalized_policy_identifier(
    token: tuple[str, str, int, int],
) -> str:
    if token[0] == "identifier":
        return _normalized_identifier(token[1])
    if token[0] != "unicode_identifier":
        raise AssertionError("unmodeled policy identifier")

    quoted_identifier = token[1][2:]
    contents = quoted_identifier[1:-1].replace('""', '"')
    if not contents.isascii() or "\\" in contents:
        raise AssertionError("unmodeled Unicode escape in policy identifier")
    return contents.lower()


def _policy_relation_target(
    statement: str, index: int
) -> tuple[str | None, str, int]:
    token = _next_sql_token(statement, index)
    if token is None or token[0] not in {"identifier", "unicode_identifier"}:
        raise AssertionError("unmodeled policy table target")

    identifier_kind = token[0]
    identifiers = [_normalized_policy_identifier(token)]
    token = _next_sql_token(statement, token[3])
    if identifier_kind == "unicode_identifier" and _is_keyword(token, "uescape"):
        raise AssertionError("unmodeled UESCAPE in policy table target")
    while token is not None and token[1] == ".":
        token = _next_sql_token(statement, token[3])
        if token is None or token[0] not in {"identifier", "unicode_identifier"}:
            raise AssertionError("invalid qualified policy table target")
        identifier_kind = token[0]
        identifiers.append(_normalized_policy_identifier(token))
        token = _next_sql_token(statement, token[3])
        if identifier_kind == "unicode_identifier" and _is_keyword(
            token, "uescape"
        ):
            raise AssertionError("unmodeled UESCAPE in policy table target")

    if len(identifiers) > 2:
        raise AssertionError("unmodeled qualified policy table target")
    schema = identifiers[0] if len(identifiers) == 2 else None
    remainder_start = token[2] if token is not None else len(statement)
    return schema, identifiers[-1], remainder_start


def _policy_statement(
    statement: str,
) -> tuple[str, str | None, str, str, str] | None:
    """Read CREATE/DROP/ALTER POLICY names and table targets by SQL tokens."""
    token = _next_sql_token(statement, 0)
    operations = ("create", "drop", "alter")
    operation = next(
        (candidate for candidate in operations if _is_keyword(token, candidate)),
        None,
    )
    if operation is None:
        return None

    token = _next_sql_token(statement, token[3])
    if not _is_keyword(token, "policy"):
        return None
    token = _next_sql_token(statement, token[3])

    if operation == "drop" and _is_keyword(token, "if"):
        token = _next_sql_token(statement, token[3])
        if not _is_keyword(token, "exists"):
            raise AssertionError("unmodeled DROP POLICY IF clause")
        token = _next_sql_token(statement, token[3])

    if token is None or token[0] not in {"identifier", "unicode_identifier"}:
        raise AssertionError("unmodeled policy name")
    policy = _normalized_policy_identifier(token)

    token = _next_sql_token(statement, token[3])
    if not _is_keyword(token, "on"):
        raise AssertionError("policy statement is missing ON target")
    schema, table, remainder_start = _policy_relation_target(statement, token[3])
    remainder = statement[remainder_start:].strip()
    return operation, schema, table, policy, remainder


def _policy_blocks(migration: str) -> list[tuple[str, str]]:
    """Apply CREATE/DROP statements and return policies in their final order."""
    policies: dict[tuple[str, str], str] = {}
    for statement in _sql_statements(migration):
        parsed = _policy_statement(statement)
        if parsed is None:
            continue
        operation, schema, table, policy, remainder = parsed
        if schema not in {None, "public"} or table not in PRIVATE_TABLES:
            continue

        key = (table, policy)
        if operation == "create":
            policies[key] = remainder.lower()
        elif operation == "drop":
            assert remainder.lower() in {"", "cascade", "restrict"}, (
                f"unmodeled DROP POLICY action on private table {table}"
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
    created_tables: set[str] = set()
    rls_enabled = {table: False for table in PRIVATE_TABLES}

    for statement in statements:
        policy_statement = _policy_statement(statement)
        if (
            policy_statement is not None
            and policy_statement[0] == "alter"
            and policy_statement[1] in {None, "public"}
            and policy_statement[2] in PRIVATE_TABLES
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

        alter_target = _alter_table_target(statement)
        if alter_target is None:
            continue
        schema, table, raw_actions = alter_target
        if table not in rls_enabled or schema not in {None, "public"}:
            continue
        actions = re.sub(r"\s+", " ", raw_actions).strip().lower()
        if actions == "enable row level security":
            rls_enabled[table] = True
            continue
        assert (table, actions) in AUDITED_PRIVATE_TABLE_ALTERS, (
            f"unmodeled or unsafe ALTER TABLE on private table {table}"
        )

    for table in PRIVATE_TABLES:
        assert table in created_tables
        assert rls_enabled[table]

    for table, owner_column in OWNER_SCOPED_TABLES.items():
        table_policies = [body for policy_table, body in policies if policy_table == table]
        assert table_policies
        for policy in table_policies:
            assert re.search(r"\bfor\s+all\b", policy)
            assert re.search(
                rf"\busing\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)", policy
            )
            assert re.search(
                rf"\bwith\s+check\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
                policy,
            )

    for table, owner_column in OWNER_DELETE_ONLY_TABLES.items():
        table_policies = [body for policy_table, body in policies if policy_table == table]
        assert table_policies
        allowed_patterns = (
            rf"\bfor\s+select\b[\s\S]*\busing\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
            rf"\bfor\s+delete\b[\s\S]*\busing\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
        )
        assert len(table_policies) == len(allowed_patterns)
        for policy in table_policies:
            assert any(re.fullmatch(pattern, policy) for pattern in allowed_patterns)
            assert not re.search(r"\bfor\s+all\b", policy)
            assert not re.search(r"\bfor\s+insert\b", policy)
            assert not re.search(r"\bfor\s+update\b", policy)
            assert not re.search(r"\bwith\s+check\b", policy)

    for table, owner_column in OWNER_INSERT_DELETE_TABLES.items():
        table_policies = [body for policy_table, body in policies if policy_table == table]
        assert table_policies
        allowed_patterns = (
            rf"\bfor\s+select\b[\s\S]*\busing\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
            rf"\bfor\s+insert\b[\s\S]*\bwith\s+check\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
            rf"\bfor\s+delete\b[\s\S]*\busing\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
        )
        assert len(table_policies) == len(allowed_patterns)
        for policy in table_policies:
            assert any(re.fullmatch(pattern, policy) for pattern in allowed_patterns)
            assert not re.search(r"\bfor\s+all\b", policy)
            assert not re.search(r"\bfor\s+update\b", policy)

    for table, owner_column in OWNER_INSERT_ONLY_TABLES.items():
        table_policies = [body for policy_table, body in policies if policy_table == table]
        assert table_policies
        allowed_patterns = (
            rf"\bfor\s+select\b[\s\S]*\busing\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
            rf"\bfor\s+insert\b[\s\S]*\bwith\s+check\s*\(\s*auth\.uid\(\)\s*=\s*{owner_column}\s*\)",
        )
        assert len(table_policies) == len(allowed_patterns)
        for policy in table_policies:
            assert any(re.fullmatch(pattern, policy) for pattern in allowed_patterns)
            assert not re.search(r"\bfor\s+all\b", policy)
            assert not re.search(r"\bfor\s+update\b", policy)
            assert not re.search(r"\bfor\s+delete\b", policy)

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
    fourth = "add column if not exists reserved_model_calls"
    assert first in migration
    assert second in migration
    assert third in migration
    assert fourth in migration
    assert migration.index(first) < migration.index(second) < migration.index(third)
    assert migration.index(third) < migration.index(fourth)


def test_rls_contract_accepts_the_audited_model_call_reservation_column():
    migration = _migration() + """
    alter table public.ai_usage_reservations
      add column if not exists reserved_model_calls integer not null default 1
      check (reserved_model_calls between 1 and 2);
    """

    _assert_private_rls_contract(migration)


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
        'alter table if exists public.trips disable row level security;',
        'alter table if exists public.trips*disable row level security;',
        (
            'alter table if exists only"public"."trips"*'
            'disable row level security;'
        ),
        'alter table u&"public".u&"trips" disable row level security;',
        (
            'alter table public.trips add column audit_marker text, '
            'disable row level security;'
        ),
        'alter table public.trips add column audit_marker text;',
        (
            'alter table if exists only public.trips * '
            'add column audit_marker text;'
        ),
        'create policy weak on "public"."trips" for select using (true);',
        'alter table public.ai_usage_counters disable row level security;',
        'create policy weak on public.ai_usage_counters for select using (true);',
        'alter policy "users manage own trips" on public.trips using (true);',
        'drop policy "users manage own trips" on public.trips;',
        'create policy weak_unqualified on trips for select using (true);',
        'drop policy "users manage own trips" on trips;',
        'alter policy "users manage own trips" on trips using (true);',
        'alter table public.community_posts disable row level security;',
        'create policy weak on public.community_posts for select using (true);',
        (
            'create policy "users insert own community posts" '
            'on public.community_posts for insert '
            'with check (auth.uid() = user_id);'
        ),
        (
            'create policy "users update own community posts" '
            'on public.community_posts for update '
            'using (auth.uid() = user_id) '
            'with check (auth.uid() = user_id);'
        ),
        (
            'create policy weak_unicode on u&"public".u&"trips" '
            'for select using (true);'
        ),
        (
            "create policy weak_unicode_escape on u&\"tr!0069ps\" "
            "uescape '!' for select using (true);"
        ),
    ],
)
def test_final_rls_contract_rejects_later_security_regressions(later_migration):
    with pytest.raises(AssertionError):
        _assert_private_rls_contract(_migration() + "\n" + later_migration.lower())


def test_private_user_tables_enable_rls_and_scope_to_authenticated_owner():
    """Removing an ownership policy must expose the missing isolation contract."""
    _assert_private_rls_contract(_migration())


def test_community_posts_table_is_private_and_authenticated_cannot_insert_or_update_directly():
    migration = _migration()

    assert (
        "revoke all on table public.community_posts from public, anon, authenticated"
        in migration
    )
    assert (
        "grant select, delete on table public.community_posts to authenticated"
        in migration
    )
    assert not re.search(
        r"grant\s+[^;]*\binsert\b[^;]*on\s+table\s+public\.community_posts[^;]*to\s+authenticated",
        migration,
    )
    assert not re.search(
        r"grant\s+[^;]*\bupdate\b[^;]*on\s+table\s+public\.community_posts[^;]*to\s+authenticated",
        migration,
    )


def test_share_links_store_only_hashes_and_have_no_public_read_policy():
    """Storing plaintext tokens or public select policy must fail this security contract."""
    migration = _migration()

    assert "token_hash" in migration
    assert "token_hash text not null unique" in migration
    assert "create policy \"public" not in migration
    assert "using (true)" not in migration


def test_profiles_creator_metadata_columns_are_private_audited_extensions():
    migration = _migration()

    assert "add column if not exists creator_slug text" in migration
    assert "add column if not exists avatar_path text" in migration
    assert "alter column creator_slug set default public.generate_creator_slug()" in migration
    assert "alter column creator_slug set not null" in migration
    assert "create unique index if not exists profiles_creator_slug_key" in migration


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


def test_planned_trip_and_both_messages_share_one_owner_scoped_database_transaction():
    migration = _migration()
    function = re.search(
        r"create\s+function\s+public\.persist_planned_chat\s*\([\s\S]*?"
        r"security\s+invoker[\s\S]*?as\s+\$\$(?P<body>[\s\S]*?)\$\$",
        migration,
    )

    assert function is not None
    body = function.group("body")
    assert "auth.uid()" in body
    assert "insert into public.trips" in body
    assert "update public.trips" in body
    assert body.count("insert into public.conversation_messages") == 2
    assert "raise exception" in body
    assert re.search(
        r"revoke\s+all\s+on\s+function\s+public\.persist_planned_chat",
        migration,
    )
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.persist_planned_chat[\s\S]*?to\s+authenticated",
        migration,
    )
