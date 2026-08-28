from pathlib import Path


MIGRATION = (
    Path(__file__).parents[2]
    / "supabase"
    / "migrations"
    / "013_city_footprints.sql"
)


def test_table_is_owner_scoped_and_city_unique():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table public.user_footprints" in sql
    assert "unique (user_id, city_adcode)" in sql
    assert "unique (id, user_id)" in sql
    assert "alter table public.user_footprints enable row level security" in sql
    assert sql.count("auth.uid() = user_id") >= 5


def test_only_authenticated_users_receive_owner_scoped_crud_permissions():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert (
        "revoke all on table public.user_footprints from public, anon, authenticated"
        in sql
    )
    assert (
        "grant select, insert, update, delete on table public.user_footprints to authenticated"
        in sql
    )
    assert "on table public.user_footprints to service_role" not in sql


def test_owner_sort_index_and_updated_timestamp_trigger_are_present():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "on public.user_footprints (user_id, visited_at desc, created_at desc, id desc)" in sql
    assert "create trigger user_footprints_set_updated_at" in sql
    assert "before update on public.user_footprints" in sql
    assert "for each row execute function public.set_updated_at()" in sql
