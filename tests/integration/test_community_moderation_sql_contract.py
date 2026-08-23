from pathlib import Path


MIGRATION = Path("supabase/migrations/012_community_moderation.sql")
SCRIPT = Path("supabase/scripts/initialize_community_admin.py")


def test_moderation_migration_uses_admin_only_rpc_grants_and_signed_image_projection():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "list_pending_travel_notes_for_moderation" in sql
    assert "list_pending_travel_note_comments_for_moderation" in sql
    assert "list_pending_travel_note_reports_for_moderation" in sql
    assert "resolve_travel_note_report" in sql
    assert "public.is_community_admin()" in sql
    assert "grant execute on function" in sql
    assert "revoke all on function" in sql
    assert "p_cursor_time" in sql
    assert "p_cursor_id" in sql
    assert "submitted_at > p_cursor_time" in sql
    assert "created_at > p_cursor_time" in sql
    assert "storage_path" not in sql
    assert "auth.users" not in sql.split("returns table", 1)[-1]


def test_admin_initialization_requires_user_id_argument_and_has_no_hardcoded_uuid():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--user-id" in source
    assert "insert into public.user_roles" in source.lower()
    assert "uuid.UUID" in source or "UUID" in source
    assert "11111111-1111-1111-1111-111111111111" not in source



def test_migration_has_explicit_hide_function_and_service_grants():
    text = Path("supabase/migrations/012_community_moderation.sql").read_text(encoding="utf-8")
    assert "hide_travel_note_moderation_target" in text
    assert "deleted_at = now()" in text
    assert "travel_notes.allow_moderation_write" in text
    assert "decision, reason" in text
    assert "values (v_target_type, p_target_id, auth.uid(), 'hide_content', null)" in text
