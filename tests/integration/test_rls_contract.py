from pathlib import Path
import re
import sqlite3

import pytest


MIGRATION = Path(__file__).parents[2] / "supabase" / "migrations" / "001_initial.sql"


def _migration() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def _policy_blocks(migration: str) -> list[tuple[str, str]]:
    """Return every policy separately so duplicate weak policies cannot hide."""
    pattern = re.compile(
        r'create\s+policy\s+(?:"[^"]+"|[a-z_][a-z0-9_$]*)'
        r'\s+on\s+public\.(?P<table>[a-z_]+)'
        r'(?P<body>[\s\S]*?)(?=create\s+policy\b|\Z)'
    )
    return [
        (match.group("table"), match.group("body"))
        for match in pattern.finditer(migration)
    ]


def test_policy_parser_does_not_skip_unquoted_policy_names():
    migration = _migration() + (
        "\ncreate policy weak on public.trips for select using (true);"
    )

    trip_policies = [
        body for table, body in _policy_blocks(migration) if table == "trips"
    ]

    assert len(trip_policies) == 2
    assert "using (true)" in trip_policies[1]


def test_private_user_tables_enable_rls_and_scope_to_authenticated_owner():
    """Removing an ownership policy must expose the missing isolation contract."""
    migration = _migration()
    policies = _policy_blocks(migration)

    for table in ("profiles", "trips", "conversation_messages", "share_links", "ai_usage"):
        assert f"create table public.{table}" in migration
        assert f"alter table public.{table} enable row level security" in migration
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
