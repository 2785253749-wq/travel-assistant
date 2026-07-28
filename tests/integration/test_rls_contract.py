from pathlib import Path
import re
import sqlite3

import pytest


MIGRATION = Path(__file__).parents[2] / "supabase" / "migrations" / "001_initial.sql"


def _migration() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_private_user_tables_enable_rls_and_scope_to_authenticated_owner():
    """Removing an ownership policy must expose the missing isolation contract."""
    migration = _migration()

    for table in ("profiles", "trips", "conversation_messages", "share_links", "ai_usage"):
        assert f"create table public.{table}" in migration
        assert f"alter table public.{table} enable row level security" in migration
        assert f"on public.{table}" in migration
        assert "auth.uid() = user_id" in migration


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
