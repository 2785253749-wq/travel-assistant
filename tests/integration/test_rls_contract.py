from pathlib import Path


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
