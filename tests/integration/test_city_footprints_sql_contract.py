from pathlib import Path
import re

import pytest


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


def test_footprint_table_enforces_owner_city_and_visit_data_constraints():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "user_id uuid not null references auth.users(id) on delete cascade" in sql
    assert "city_adcode text not null check (city_adcode ~ '^[0-9]{6}$')" in sql
    assert "province_adcode text not null check (province_adcode ~ '^[0-9]{6}$')" in sql
    assert "char_length(btrim(city_name)) between 1 and 40" in sql
    assert "char_length(btrim(province_name)) between 1 and 40" in sql
    assert "center_lng double precision not null check (center_lng between 73 and 136)" in sql
    assert "center_lat double precision not null check (center_lat between 3 and 54)" in sql
    assert "visited_at date not null check (visited_at <= current_date)" in sql


def _assert_footprint_grant_contract(sql: str) -> None:
    grants = []
    for statement in sql.split(";"):
        grant = re.fullmatch(
            r"\s*grant\s+(.+?)\s+on\s+table\s+public\.user_footprints\s+to\s+(.+?)\s*",
            statement,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if grant:
            grants.append(grant.groups())
    allowed_authenticated_privileges = {"select", "insert", "update", "delete"}

    assert grants
    for raw_privileges, raw_roles in grants:
        roles = {role.strip().lower() for role in raw_roles.split(",")}
        privileges = {privilege.strip().lower() for privilege in raw_privileges.split(",")}
        assert roles == {"authenticated"}
        assert privileges <= allowed_authenticated_privileges


def test_footprint_grants_exclude_anon_and_authenticated_privilege_escalation():
    _assert_footprint_grant_contract(MIGRATION.read_text(encoding="utf-8").lower())


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT SELECT ON TABLE public.user_footprints TO PUBLIC;",
        "GRANT SELECT ON TABLE public.user_footprints TO reporting_role;",
        "GRANT SELECT, REFERENCES ON TABLE public.user_footprints TO authenticated;",
    ],
)
def test_footprint_grant_contract_rejects_unintended_roles_and_privileges(grant):
    sql = MIGRATION.read_text(encoding="utf-8").lower() + "\n" + grant

    with pytest.raises(AssertionError):
        _assert_footprint_grant_contract(sql)


def test_footprint_grant_contract_does_not_treat_revoke_as_a_grant():
    sql = (
        MIGRATION.read_text(encoding="utf-8").lower()
        + "\nREVOKE SELECT ON TABLE public.user_footprints FROM PUBLIC;"
    )

    _assert_footprint_grant_contract(sql)


def test_owner_sort_index_and_updated_timestamp_trigger_are_present():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "on public.user_footprints (user_id, visited_at desc, created_at desc, id desc)" in sql
    assert "create trigger user_footprints_set_updated_at" in sql
    assert "before update on public.user_footprints" in sql
    assert "for each row execute function public.set_updated_at()" in sql
