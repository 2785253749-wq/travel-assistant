from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.core.errors import AppError
from app.footprints.models import CityRecord, FootprintCreate, FootprintUpdate, StoredFootprint
from app.footprints.service import FootprintModule, StaticCityDirectory


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")
TODAY = date(2026, 8, 28)
TIMESTAMP = datetime(2026, 8, 28, 8, 30, tzinfo=UTC)


class InMemoryFootprintRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], StoredFootprint] = {}

    def list_owned(self, user_id: UUID) -> list[StoredFootprint]:
        return [row for (owner_id, _), row in self.rows.items() if owner_id == user_id]

    def upsert_owned(
        self, user_id: UUID, city: CityRecord, visited_at: date
    ) -> StoredFootprint:
        key = user_id, city.city_adcode
        existing = self.rows.get(key)
        row = StoredFootprint(
            id=existing.id if existing is not None else uuid4(),
            user_id=user_id,
            city_adcode=city.city_adcode,
            city_name=city.city_name,
            province_adcode=city.province_adcode,
            province_name=city.province_name,
            center=city.center,
            visited_at=visited_at,
            created_at=existing.created_at if existing is not None else TIMESTAMP,
            updated_at=TIMESTAMP,
        )
        self.rows[key] = row
        return row

    def update_visited_at(
        self, user_id: UUID, footprint_id: UUID, visited_at: date
    ) -> StoredFootprint | None:
        for key, row in self.rows.items():
            if row.user_id == user_id and row.id == footprint_id:
                updated = replace(row, visited_at=visited_at, updated_at=TIMESTAMP)
                self.rows[key] = updated
                return updated
        return None

    def delete_owned(self, user_id: UUID, footprint_id: UUID) -> bool:
        for key, row in list(self.rows.items()):
            if row.user_id == user_id and row.id == footprint_id:
                del self.rows[key]
                return True
        return False


class UnavailableCityDirectory:
    def resolve(self, city_adcode: str) -> CityRecord | None:
        del city_adcode
        raise RuntimeError("upstream failure")


@pytest.fixture
def repository():
    return InMemoryFootprintRepository()


@pytest.fixture
def module(repository):
    return FootprintModule(repository, StaticCityDirectory(), today=lambda: TODAY)


def request(visited_at: date = TODAY) -> FootprintCreate:
    return FootprintCreate(city_adcode="350200", visited_at=visited_at)


def test_static_city_directory_contains_the_four_trial_cities():
    directory = StaticCityDirectory()

    assert directory.resolve("350200") == CityRecord(
        city_adcode="350200",
        city_name="厦门市",
        province_adcode="350000",
        province_name="福建省",
        center=(118.09, 24.48),
    )
    assert directory.resolve("350100").city_name == "福州市"
    assert directory.resolve("532900").city_name == "大理州"
    assert directory.resolve("530700").city_name == "丽江市"


def test_add_uses_authenticated_owner_and_server_city(module, repository):
    result = module.add(USER_A, request())

    assert result.city_name == "厦门市"
    assert repository.rows[(USER_A, "350200")].user_id == USER_A


def test_repeated_city_is_idempotent(module):
    assert module.add(USER_A, request()).id == module.add(USER_A, request()).id


def test_future_visit_is_rejected(module):
    with pytest.raises(AppError, match="FOOTPRINT_VALIDATION_FAILED"):
        module.add(USER_A, request(TODAY + timedelta(days=1)))


def test_unknown_city_is_rejected(module):
    with pytest.raises(AppError, match="FOOTPRINT_CITY_NOT_FOUND"):
        module.add(
            USER_A,
            FootprintCreate(city_adcode="110000", visited_at=TODAY),
        )


def test_other_account_cannot_update_or_delete(module):
    stored = module.add(USER_A, request())

    with pytest.raises(AppError, match="FOOTPRINT_NOT_FOUND"):
        module.update(USER_B, stored.id, FootprintUpdate(visited_at=TODAY))
    with pytest.raises(AppError, match="FOOTPRINT_NOT_FOUND"):
        module.remove(USER_B, stored.id)


def test_list_hides_owner_and_sorts_newest_visit_first(module, repository):
    module.add(USER_A, request(TODAY - timedelta(days=1)))
    module.add(
        USER_A,
        FootprintCreate(city_adcode="350100", visited_at=TODAY),
    )
    module.add(USER_B, request())

    views = module.list(USER_A)

    assert [view.city_adcode for view in views] == ["350100", "350200"]
    assert all("user_id" not in view.model_dump() for view in views)
    assert len(repository.rows) == 3


def test_unavailable_directory_maps_to_a_stable_error(repository):
    module = FootprintModule(repository, UnavailableCityDirectory(), today=lambda: TODAY)

    with pytest.raises(AppError, match="FOOTPRINT_UNAVAILABLE"):
        module.add(USER_A, request())
