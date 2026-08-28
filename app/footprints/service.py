from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable, Protocol
from uuid import UUID

from app.core.errors import AppError
from app.footprints.models import (
    CityRecord,
    FootprintCreate,
    FootprintUpdate,
    FootprintView,
    StoredFootprint,
)


_CHINA_TIMEZONE = timezone(timedelta(hours=8))


class CityDirectory(Protocol):
    def resolve(self, city_adcode: str) -> CityRecord | None:
        raise NotImplementedError


class FootprintRepository(Protocol):
    def list_owned(self, user_id: UUID) -> list[StoredFootprint]:
        raise NotImplementedError

    def upsert_owned(
        self, user_id: UUID, city: CityRecord, visited_at: date
    ) -> StoredFootprint:
        raise NotImplementedError

    def update_visited_at(
        self, user_id: UUID, footprint_id: UUID, visited_at: date
    ) -> StoredFootprint | None:
        raise NotImplementedError

    def delete_owned(self, user_id: UUID, footprint_id: UUID) -> bool:
        raise NotImplementedError


class StaticCityDirectory:
    """Fallback directory for the four cities available in the exploration trial."""

    _cities = {
        "350200": CityRecord(
            city_adcode="350200",
            city_name="厦门市",
            province_adcode="350000",
            province_name="福建省",
            center=(118.09, 24.48),
        ),
        "350100": CityRecord(
            city_adcode="350100",
            city_name="福州市",
            province_adcode="350000",
            province_name="福建省",
            center=(119.30, 26.08),
        ),
        "532900": CityRecord(
            city_adcode="532900",
            city_name="大理州",
            province_adcode="530000",
            province_name="云南省",
            center=(100.23, 25.60),
        ),
        "530700": CityRecord(
            city_adcode="530700",
            city_name="丽江市",
            province_adcode="530000",
            province_name="云南省",
            center=(100.23, 26.87),
        ),
    }

    def resolve(self, city_adcode: str) -> CityRecord | None:
        city = self._cities.get(city_adcode)
        return city.model_copy() if city is not None else None


class FootprintModule:
    def __init__(
        self,
        repository: FootprintRepository,
        city_directory: CityDirectory,
        *,
        today: Callable[[], date] = lambda: datetime.now(_CHINA_TIMEZONE).date(),
    ) -> None:
        self._repository = repository
        self._city_directory = city_directory
        self._today = today

    def list(self, user_id: UUID) -> list[FootprintView]:
        rows = self._repository_call(lambda: self._repository.list_owned(user_id))
        sorted_rows = sorted(
            rows,
            key=lambda row: (row.visited_at, row.created_at, str(row.id)),
            reverse=True,
        )
        return [self._to_view(row) for row in sorted_rows]

    def add(self, user_id: UUID, request: FootprintCreate) -> FootprintView:
        self._validate_visited_at(request.visited_at)
        city = self._resolve_city(request.city_adcode)
        stored = self._repository_call(
            lambda: self._repository.upsert_owned(user_id, city, request.visited_at)
        )
        return self._to_view(stored)

    def update(
        self, user_id: UUID, footprint_id: UUID, request: FootprintUpdate
    ) -> FootprintView:
        self._validate_visited_at(request.visited_at)
        stored = self._repository_call(
            lambda: self._repository.update_visited_at(
                user_id, footprint_id, request.visited_at
            )
        )
        if stored is None:
            raise _not_found()
        return self._to_view(stored)

    def remove(self, user_id: UUID, footprint_id: UUID) -> None:
        deleted = self._repository_call(
            lambda: self._repository.delete_owned(user_id, footprint_id)
        )
        if not deleted:
            raise _not_found()

    def _resolve_city(self, city_adcode: str) -> CityRecord:
        city = self._repository_call(lambda: self._city_directory.resolve(city_adcode))
        if city is None:
            raise AppError("FOOTPRINT_CITY_NOT_FOUND", "FOOTPRINT_CITY_NOT_FOUND")
        return city

    def _validate_visited_at(self, visited_at: date) -> None:
        if visited_at > self._today():
            raise AppError(
                "FOOTPRINT_VALIDATION_FAILED", "FOOTPRINT_VALIDATION_FAILED"
            )

    @staticmethod
    def _to_view(stored: StoredFootprint) -> FootprintView:
        return FootprintView(
            id=stored.id,
            city_adcode=stored.city_adcode,
            city_name=stored.city_name,
            province_adcode=stored.province_adcode,
            province_name=stored.province_name,
            center=stored.center,
            visited_at=stored.visited_at,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
        )

    @staticmethod
    def _repository_call(operation: Callable[[], object]):
        try:
            return operation()
        except AppError:
            raise
        except Exception as exc:
            raise AppError("FOOTPRINT_UNAVAILABLE", "FOOTPRINT_UNAVAILABLE") from exc


def _not_found() -> AppError:
    return AppError("FOOTPRINT_NOT_FOUND", "FOOTPRINT_NOT_FOUND")


__all__ = [
    "CityDirectory",
    "FootprintModule",
    "FootprintRepository",
    "StaticCityDirectory",
]
