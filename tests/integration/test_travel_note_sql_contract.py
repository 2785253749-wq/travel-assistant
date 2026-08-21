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

    for block in (review_note, review_comment):
        assert "public.is_community_admin()" in block
        assert "insert into public.moderation_decisions" in block
        assert "now()" in block

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
