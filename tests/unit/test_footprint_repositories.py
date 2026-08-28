from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.footprints.models import CityRecord
from app.footprints.repositories import (
    InMemoryFootprintRepository,
    SupabaseFootprintRepository,
    create_user_scoped_footprint_repository,
)


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")
FOOTPRINT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TODAY = date(2026, 8, 28)
XIAMEN = CityRecord(
    city_adcode="350200",
    city_name="厦门市",
    province_adcode="350000",
    province_name="福建省",
    center=(118.09, 24.48),
)


def _row(*, user_id: UUID = USER_A, footprint_id: UUID = FOOTPRINT_ID) -> dict[str, str | float]:
    return {
        "id": str(footprint_id),
        "user_id": str(user_id),
        "city_adcode": "350200",
        "city_name": "厦门市",
        "province_adcode": "350000",
        "province_name": "福建省",
        "center_lng": 118.09,
        "center_lat": 24.48,
        "visited_at": "2026-08-28",
        "created_at": "2026-08-28T08:30:00+00:00",
        "updated_at": "2026-08-28T08:30:00+00:00",
    }


class FakeQuery:
    def __init__(self, response_data: object) -> None:
        self.response_data = response_data
        self.payload: dict[str, object] | None = None
        self.on_conflict: str | None = None
        self.filters: list[tuple[str, str]] = []
        self.orders: list[tuple[str, bool]] = []
        self.operation: str | None = None

    def select(self, _columns: str):
        self.operation = "select"
        return self

    def upsert(self, payload: dict[str, object], *, on_conflict: str):
        self.operation = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def update(self, payload: dict[str, object]):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, name: str, value: str):
        self.filters.append((name, value))
        return self

    def order(self, name: str, *, desc: bool):
        self.orders.append((name, desc))
        return self

    def execute(self):
        if isinstance(self.response_data, Exception):
            raise self.response_data
        return SimpleNamespace(data=self.response_data)


class FakeClient:
    def __init__(self, response_data: object) -> None:
        self.query = FakeQuery(response_data)
        self.table_names: list[str] = []

    def table(self, name: str) -> FakeQuery:
        self.table_names.append(name)
        return self.query


class FakePostgrest:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def auth(self, token: str) -> None:
        self.tokens.append(token)


class FactoryClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([_row()])
        self.postgrest = FakePostgrest()


@pytest.fixture
def client() -> FakeClient:
    return FakeClient([_row()])


@pytest.fixture
def repository(client: FakeClient) -> SupabaseFootprintRepository:
    return SupabaseFootprintRepository(client)


def test_in_memory_repository_is_owner_scoped_and_idempotent():
    repository = InMemoryFootprintRepository()

    first = repository.upsert_owned(USER_A, XIAMEN, TODAY)
    repeated = repository.upsert_owned(USER_A, XIAMEN, TODAY)
    repository.upsert_owned(USER_B, XIAMEN, TODAY)

    assert repeated.id == first.id
    assert [row.user_id for row in repository.list_owned(USER_A)] == [USER_A]
    assert repository.update_visited_at(USER_B, first.id, TODAY) is None
    assert not repository.delete_owned(USER_B, first.id)


def test_user_scoped_factory_binds_only_the_verified_access_token(monkeypatch):
    fake = FactoryClient()
    monkeypatch.setitem(
        __import__("sys").modules,
        "supabase",
        SimpleNamespace(create_client=lambda _url, _key: fake),
    )

    repository = create_user_scoped_footprint_repository(
        "https://project.test", "anon-key", "verified-access-token"
    )

    assert isinstance(repository, SupabaseFootprintRepository)
    assert fake.postgrest.tokens == ["verified-access-token"]


def test_list_filters_the_verified_owner_and_orders_by_visit_then_creation(
    repository, client
):
    stored = repository.list_owned(USER_A)

    assert stored[0].id == FOOTPRINT_ID
    assert client.table_names == ["user_footprints"]
    assert client.query.filters == [("user_id", str(USER_A))]
    assert client.query.orders == [("visited_at", True), ("created_at", True)]


def test_upsert_uses_verified_owner_and_composite_conflict(repository, client):
    repository.upsert_owned(USER_A, XIAMEN, TODAY)

    assert client.query.payload == {
        "user_id": str(USER_A),
        "city_adcode": "350200",
        "city_name": "厦门市",
        "province_adcode": "350000",
        "province_name": "福建省",
        "center_lng": 118.09,
        "center_lat": 24.48,
        "visited_at": "2026-08-28",
    }
    assert client.query.on_conflict == "user_id,city_adcode"


def test_update_filters_id_and_owner(repository, client):
    updated = repository.update_visited_at(USER_A, FOOTPRINT_ID, TODAY)

    assert updated is not None
    assert client.query.payload == {"visited_at": "2026-08-28"}
    assert ("id", str(FOOTPRINT_ID)) in client.query.filters
    assert ("user_id", str(USER_A)) in client.query.filters


def test_delete_filters_id_and_owner(repository, client):
    assert repository.delete_owned(USER_A, FOOTPRINT_ID)

    assert ("id", str(FOOTPRINT_ID)) in client.query.filters
    assert ("user_id", str(USER_A)) in client.query.filters


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("update", None),
        ("delete", False),
    ],
)
def test_no_matching_row_is_not_an_upstream_failure(operation, expected):
    repository = SupabaseFootprintRepository(FakeClient([]))

    result = (
        repository.update_visited_at(USER_A, FOOTPRINT_ID, TODAY)
        if operation == "update"
        else repository.delete_owned(USER_A, FOOTPRINT_ID)
    )

    assert result is expected


@pytest.mark.parametrize(
    "operation",
    [
        lambda repository: repository.list_owned(USER_A),
        lambda repository: repository.upsert_owned(USER_A, XIAMEN, TODAY),
        lambda repository: repository.update_visited_at(USER_A, FOOTPRINT_ID, TODAY),
        lambda repository: repository.delete_owned(USER_A, FOOTPRINT_ID),
    ],
)
def test_supabase_failures_normalize_to_footprint_unavailable(operation):
    repository = SupabaseFootprintRepository(FakeClient(RuntimeError("database down")))

    with pytest.raises(AppError) as error:
        operation(repository)

    assert (error.value.code, error.value.message) == (
        "FOOTPRINT_UNAVAILABLE",
        "FOOTPRINT_UNAVAILABLE",
    )


def test_invalid_database_rows_normalize_to_footprint_unavailable():
    repository = SupabaseFootprintRepository(FakeClient([{"id": str(FOOTPRINT_ID)}]))

    with pytest.raises(AppError, match="FOOTPRINT_UNAVAILABLE"):
        repository.list_owned(USER_A)
