from pathlib import Path


MIGRATION = Path(__file__).parents[2] / "supabase" / "migrations" / "002_secure_public_share_rpc.sql"


def test_public_share_is_a_security_definer_allowlist_rpc_not_table_access():
    migration = MIGRATION.read_text(encoding="utf-8").lower()

    assert "security definer" in migration
    assert "set search_path = pg_catalog, public" in migration
    assert "get_shared_trip_by_token_hash" in migration
    assert "token_hash = p_token_hash" in migration
    assert "revoked_at is null" in migration
    assert "expires_at > now()" in migration
    assert "revoke all on table public.share_links from public, anon" in migration
    assert "revoke all on table public.profiles from public, anon" in migration
    assert "revoke all on table public.ai_usage from public, anon" in migration
    assert "grant execute on function public.get_shared_trip_by_token_hash(text) to anon, authenticated" in migration
    assert "conversation_messages" not in migration.split("returns table", 1)[1].split("language sql", 1)[0]
