from pathlib import Path
import re


MIGRATION = (
    Path(__file__).parents[2]
    / "supabase"
    / "migrations"
    / "011_travel_note_community.sql"
)
FORBIDDEN_PUBLIC_COLUMNS = {"author_id", "source_trip_id", "review_reason"}


def migration_011() -> str:
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    return MIGRATION.read_text(encoding="utf-8").lower()


def function_block(migration: str, function_name: str) -> str:
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+public\.{function_name}\s*"
        rf"\((?P<args>[\s\S]*?)\)\s*"
        rf"returns\s+(?P<returns>[\s\S]*?)\s+language\s+(?P<language>\w+)\s+"
        rf"security\s+(?P<security>\w+)\s+set\s+search_path\s*=\s*(?P<search_path>[\w\s,]+)\s+"
        rf"as\s+\$\$(?P<body>[\s\S]*?)\$\$",
        migration,
    )
    assert match is not None, f"missing function block for {function_name}"
    return match.group(0)


def routine_block(migration: str, function_name: str) -> str:
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+public\.{function_name}\s*"
        rf"\([\s\S]*?\)\s*returns\s+[\w\[\]]+[\s\S]*?"
        rf"as\s+\$\$(?P<body>[\s\S]*?)\$\$\s*;",
        migration,
    )
    assert match is not None, f"missing function block for {function_name}"
    return match.group(0)


def returns_table_columns(function_block_text: str) -> set[str]:
    match = re.search(
        r"returns\s+table\s*\((?P<columns>[\s\S]*?)\)\s+language",
        function_block_text,
    )
    assert match is not None, "expected returns table (...) contract"
    columns: set[str] = set()
    for raw_line in match.group("columns").splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        columns.add(line.split()[0])
    return columns


def test_travel_notes_are_separate_from_legacy_snapshots():
    sql = migration_011()

    assert "create table public.travel_notes" in sql
    assert "create table public.travel_note_images" in sql
    assert "create table public.travel_note_comments" in sql
    assert "create table public.user_roles" in sql
    assert "alter table public.community_posts" not in sql
    assert "status text not null default 'draft'" in sql
    assert "published_at timestamptz" in sql
    assert "deleted_at timestamptz" in sql
    assert "itinerary_snapshot jsonb" in sql


def test_public_rpcs_only_return_approved_projection():
    sql = migration_011()
    listing = function_block(sql, "list_public_travel_notes_internal")
    detail = function_block(sql, "get_public_travel_note_internal")

    for block in (listing, detail):
        assert "security definer" in block
        assert "set search_path = pg_catalog, public" in block
        assert "status = 'approved'" in block
        assert "deleted_at is null" in block
        assert not (returns_table_columns(block) & FORBIDDEN_PUBLIC_COLUMNS)

    assert "grant execute" in sql
    assert "list_public_travel_notes_internal" in sql
    assert "get_public_travel_note_internal" in sql
    assert "to service_role" in sql
    assert not re.search(
        r"grant\s+execute\s+on\s+function\s+public\.list_public_travel_notes_internal\([^)]*\)\s+to\s+anon",
        sql,
    )
    assert not re.search(
        r"grant\s+execute\s+on\s+function\s+public\.get_public_travel_note_internal\([^)]*\)\s+to\s+authenticated",
        sql,
    )


def test_submit_and_review_rpcs_enforce_fixed_search_path_and_role_checks():
    sql = migration_011()
    submit = function_block(sql, "submit_travel_note")
    review_note = function_block(sql, "review_travel_note")
    review_comment = function_block(sql, "review_travel_note_comment")
    admin_check = function_block(sql, "is_community_admin")

    assert "auth.uid()" in submit
    assert "from public.travel_note_images" in submit
    assert "from public.trips" in submit
    assert "pending_review" in submit
    assert "community_public_itinerary_is_valid" in submit
    assert "set_config('travel_notes.allow_moderation_write', 'on', true)" in submit

    for block in (review_note, review_comment):
        assert "public.is_community_admin()" in block
        assert "insert into public.moderation_decisions" in block
        assert "now()" in block
        assert "set_config('travel_notes.allow_moderation_write', 'on', true)" in block

    assert "from public.user_roles" in admin_check
    assert "role = 'admin'" in admin_check
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.submit_travel_note\([^)]*\)\s+to\s+authenticated",
        sql,
    )
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.review_travel_note\([^)]*\)\s+to\s+authenticated",
        sql,
    )
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.review_travel_note_comment\([^)]*\)\s+to\s+authenticated",
        sql,
    )
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.is_community_admin\([^)]*\)\s+to\s+authenticated",
        sql,
    )


def test_submit_and_review_rpcs_lock_rows_and_reject_stale_transitions():
    sql = migration_011()
    submit = function_block(sql, "submit_travel_note")
    review_note = function_block(sql, "review_travel_note")
    review_comment = function_block(sql, "review_travel_note_comment")

    assert re.search(
        r"from public\.travel_notes as note_row[\s\S]*?for update",
        submit,
    )
    assert re.search(
        r"update public\.travel_notes as note_row[\s\S]*?where note_row\.id = v_note\.id[\s\S]*?and note_row\.author_id = v_user_id[\s\S]*?and note_row\.status = v_note\.status[\s\S]*?and note_row\.deleted_at is null",
        submit,
    )
    assert "travel note submission is stale" in submit
    assert "if not found then" in submit

    assert re.search(
        r"from public\.travel_notes as note_row[\s\S]*?for update",
        review_note,
    )
    assert re.search(
        r"update public\.travel_notes as note_row[\s\S]*?where note_row\.id = v_note\.id[\s\S]*?and note_row\.status = 'pending_review'[\s\S]*?and note_row\.deleted_at is null",
        review_note,
    )
    assert "travel note review is stale" in review_note

    assert re.search(
        r"from public\.travel_note_comments as comment_row[\s\S]*?for update",
        review_comment,
    )
    assert re.search(
        r"update public\.travel_note_comments as comment_row[\s\S]*?where comment_row\.id = v_comment\.id[\s\S]*?and comment_row\.status = 'pending_review'[\s\S]*?and comment_row\.deleted_at is null",
        review_comment,
    )
    assert "travel note comment review is stale" in review_comment
    assert re.search(
        r"update public\.travel_note_comments as comment_row[\s\S]*?returning comment_row\.\* into v_comment;[\s\S]*?if not found then[\s\S]*?travel note comment review is stale[\s\S]*?update public\.travel_notes as note_row[\s\S]*?set comment_count = note_row\.comment_count \+ 1",
        review_comment,
    )
    assert re.search(
        r"from public\.travel_notes as note_row[\s\S]*?where note_row\.id = v_comment\.note_id[\s\S]*?and note_row\.status = 'approved'[\s\S]*?and note_row\.deleted_at is null[\s\S]*?for update",
        review_comment,
    )
    assert re.search(
        r"update public\.travel_notes as note_row[\s\S]*?where note_row\.id = v_comment\.note_id[\s\S]*?and note_row\.status = 'approved'[\s\S]*?and note_row\.deleted_at is null[\s\S]*?returning note_row\.comment_count into",
        review_comment,
    )
    assert "travel note comment parent is stale" in review_comment


def test_profiles_gain_random_creator_metadata_without_using_user_uuid():
    sql = migration_011()
    slug_generator = function_block(sql, "generate_creator_slug")

    assert "alter table public.profiles" in sql
    assert "add column if not exists creator_slug text" in sql
    assert "add column if not exists avatar_path text" in sql
    assert "alter column creator_slug set default public.generate_creator_slug()" in sql
    assert "alter column creator_slug set not null" in sql
    assert "create unique index if not exists profiles_creator_slug_key" in sql
    assert "update public.profiles" in sql
    assert "creator_slug = public.generate_creator_slug()" in sql
    assert "gen_random_bytes" in slug_generator
    assert "user_id" not in slug_generator


def test_travel_note_owner_write_path_blocks_direct_moderation_field_changes():
    sql = migration_011()
    guard = routine_block(sql, "enforce_travel_note_client_write_rules")

    assert not re.search(
        r'create\s+policy\s+"authors manage own travel notes"\s+on\s+public\.travel_notes\s+for\s+all',
        sql,
    )
    assert re.search(
        r'create\s+policy\s+"authors view own travel notes"\s+on\s+public\.travel_notes\s+for\s+select\s+to\s+authenticated\s+using\s*\(\s*auth\.uid\(\)\s*=\s*author_id\s*\)',
        sql,
    )
    assert re.search(
        r'create\s+policy\s+"authors create own draft travel notes"\s+on\s+public\.travel_notes\s+for\s+insert\s+to\s+authenticated\s+with\s+check\s*\(\s*auth\.uid\(\)\s*=\s*author_id\s*\)',
        sql,
    )
    assert re.search(
        r'create\s+policy\s+"authors edit own draft or rejected travel notes"\s+on\s+public\.travel_notes\s+for\s+update\s+to\s+authenticated\s+using\s*\(\s*auth\.uid\(\)\s*=\s*author_id\s+and\s+status\s+in\s+\(',
        sql,
    )
    assert not re.search(
        r"grant\s+[^;]*\bdelete\b[^;]*on\s+table\s+public\.travel_notes[^;]*to\s+authenticated",
        sql,
    )

    assert "current_setting('travel_notes.allow_moderation_write', true)" in guard
    assert "if tg_op = 'insert' then" in guard
    assert "new.status <> 'draft'" in guard
    assert "new.review_reason is not null" in guard
    assert "new.submitted_at is not null" in guard
    assert "new.published_at is not null" in guard
    assert "new.itinerary_snapshot is not null" in guard
    assert "new.like_count <> 0" in guard
    assert "new.comment_count <> 0" in guard
    assert "old.status not in ('draft', 'rejected')" in guard
    assert "old.deleted_at is not null" in guard
    assert "new.review_reason is distinct from old.review_reason" in guard
    assert "new.submitted_at is distinct from old.submitted_at" in guard
    assert "new.published_at is distinct from old.published_at" in guard
    assert "new.itinerary_snapshot is distinct from old.itinerary_snapshot" in guard
    assert "new.like_count is distinct from old.like_count" in guard
    assert "new.comment_count is distinct from old.comment_count" in guard
    assert "old.status = 'draft' and new.status <> 'draft'" in guard
    assert "old.status = 'rejected' and new.status not in ('rejected', 'draft')" in guard
    assert "create trigger enforce_travel_note_client_write_rules" in sql


def test_storage_objects_are_owner_scoped():
    sql = migration_011()

    assert "insert into storage.buckets" in sql
    assert "community-media" in sql
    assert "values ('community-media', 'community-media', false)" in sql
    assert "(storage.foldername(name))[1] = auth.uid()::text" in sql
    for policy_name in (
        "users upload own community media",
        "users read own community media",
        "users update own community media",
        "users delete own community media",
    ):
        assert policy_name in sql
    assert "to anon" not in sql
