from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import AppError
from app.footprints.models import CityRecord, StoredFootprint


class InMemoryFootprintRepository:
    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, str], StoredFootprint] = {}

    def list_owned(self, user_id: UUID) -> list[StoredFootprint]:
        return [row for (owner_id, _), row in self._rows.items() if owner_id == user_id]

    def upsert_owned(
        self, user_id: UUID, city: CityRecord, visited_at: date
    ) -> StoredFootprint:
        key = user_id, city.city_adcode
        existing = self._rows.get(key)
        now = datetime.now(UTC)
        stored = StoredFootprint(
            id=existing.id if existing is not None else uuid4(),
            user_id=user_id,
            city_adcode=city.city_adcode,
            city_name=city.city_name,
            province_adcode=city.province_adcode,
            province_name=city.province_name,
            center=city.center,
            visited_at=visited_at,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._rows[key] = stored
        return stored

    def update_visited_at(
        self, user_id: UUID, footprint_id: UUID, visited_at: date
    ) -> StoredFootprint | None:
        for key, stored in self._rows.items():
            if stored.user_id == user_id and stored.id == footprint_id:
                updated = replace(
                    stored,
                    visited_at=visited_at,
                    updated_at=datetime.now(UTC),
                )
                self._rows[key] = updated
                return updated
        return None

    def delete_owned(self, user_id: UUID, footprint_id: UUID) -> bool:
        for key, stored in list(self._rows.items()):
            if stored.user_id == user_id and stored.id == footprint_id:
                del self._rows[key]
                return True
        return False


class SupabaseFootprintRepository:
    def __init__(self, client) -> None:
        self._client = client

    def list_owned(self, user_id: UUID) -> list[StoredFootprint]:
        try:
            response = (
                self._client.table("user_footprints")
                .select(
                    "id,user_id,city_adcode,city_name,province_adcode,"
                    "province_name,center_lng,center_lat,visited_at,created_at,updated_at"
                )
                .eq("user_id", str(user_id))
                .order("visited_at", desc=True)
                .order("created_at", desc=True)
                .execute()
            )
            return [self._stored_from_row(row) for row in _rows(response.data)]
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _unavailable() from exc

    def upsert_owned(
        self, user_id: UUID, city: CityRecord, visited_at: date
    ) -> StoredFootprint:
        payload = {
            "user_id": str(user_id),
            "city_adcode": city.city_adcode,
            "city_name": city.city_name,
            "province_adcode": city.province_adcode,
            "province_name": city.province_name,
            "center_lng": city.center[0],
            "center_lat": city.center[1],
            "visited_at": visited_at.isoformat(),
        }
        try:
            response = (
                self._client.table("user_footprints")
                .upsert(payload, on_conflict="user_id,city_adcode")
                .eq("user_id", str(user_id))
                .execute()
            )
            rows = _rows(response.data)
            if not rows:
                raise RuntimeError("footprint upsert returned no row")
            return self._stored_from_row(rows[0])
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _unavailable() from exc

    def update_visited_at(
        self, user_id: UUID, footprint_id: UUID, visited_at: date
    ) -> StoredFootprint | None:
        try:
            response = (
                self._client.table("user_footprints")
                .update({"visited_at": visited_at.isoformat()})
                .eq("id", str(footprint_id))
                .eq("user_id", str(user_id))
                .execute()
            )
            rows = _rows(response.data)
            return self._stored_from_row(rows[0]) if rows else None
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _unavailable() from exc

    def delete_owned(self, user_id: UUID, footprint_id: UUID) -> bool:
        try:
            response = (
                self._client.table("user_footprints")
                .delete()
                .eq("id", str(footprint_id))
                .eq("user_id", str(user_id))
                .execute()
            )
            return bool(_rows(response.data))
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _unavailable() from exc

    @staticmethod
    def _stored_from_row(row: object) -> StoredFootprint:
        if not isinstance(row, dict):
            raise RuntimeError("footprint row is invalid")
        return StoredFootprint(
            id=UUID(str(row["id"])),
            user_id=UUID(str(row["user_id"])),
            city_adcode=str(row["city_adcode"]),
            city_name=str(row["city_name"]),
            province_adcode=str(row["province_adcode"]),
            province_name=str(row["province_name"]),
            center=(float(row["center_lng"]), float(row["center_lat"])),
            visited_at=_parse_date(row["visited_at"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )


def create_user_scoped_footprint_repository(
    url: str, anon_key: str, access_token: str
) -> SupabaseFootprintRepository:
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(access_token)
    return SupabaseFootprintRepository(client)


def _rows(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise RuntimeError("footprint response is invalid")
    return data


def _parse_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise RuntimeError("footprint date is invalid")


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise RuntimeError("footprint timestamp is invalid")


def _unavailable() -> AppError:
    return AppError("FOOTPRINT_UNAVAILABLE", "FOOTPRINT_UNAVAILABLE")


__all__ = [
    "InMemoryFootprintRepository",
    "SupabaseFootprintRepository",
    "create_user_scoped_footprint_repository",
]
