from __future__ import annotations

from datetime import datetime
from math import isfinite
import re

import httpx

from app.core.config import Settings
from app.footprints.models import CityRecord, DistrictBoundary
from app.providers.base import ProviderResult, utc_now


AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
AMAP_DISTRICT_SOURCE = "amap-district"
_ADCODE_PATTERN = re.compile(r"\d{6}")
_DIRECT_MUNICIPALITY_ADCODES = frozenset({"110000", "120000", "310000", "500000"})


class AmapDistrictProvider:
    """Server-only adapter for the AMap district Web Service API."""

    def __init__(
        self,
        *,
        settings: Settings,
        client: httpx.Client | None = None,
    ) -> None:
        self._key = (
            settings.amap_web_service_key.get_secret_value().strip()
            if settings.amap_web_service_key is not None
            else ""
        )
        self._timeout_seconds = settings.district_timeout_seconds
        self._max_points = settings.district_max_points
        self._client = client or httpx.Client(timeout=self._timeout_seconds)

    def search(self, query: str) -> list[CityRecord]:
        """Return canonical city candidates, or an empty safe result on failure."""
        if not self._key or not isinstance(query, str) or not query.strip():
            return []
        try:
            districts = self._request(query, extensions="base")
            cities: list[CityRecord] = []
            for district in districts:
                city = _city_from_district(district)
                if city is not None:
                    cities.append(city)
            return cities
        except (
            httpx.RequestError,
            _DistrictHttpError,
            _DistrictProviderError,
            ValueError,
            TypeError,
        ):
            return []

    def boundary(self, adcode: str) -> ProviderResult[DistrictBoundary]:
        fetched_at = utc_now()
        if not self._key:
            return _failure(fetched_at, "DISTRICT_NOT_CONFIGURED")
        if not isinstance(adcode, str) or not _ADCODE_PATTERN.fullmatch(adcode):
            return _failure(fetched_at, "DISTRICT_INVALID_REQUEST")
        try:
            districts = self._request(adcode, extensions="all")
        except httpx.TimeoutException:
            return _failure(fetched_at, "DISTRICT_TIMEOUT")
        except httpx.RequestError:
            return _failure(fetched_at, "DISTRICT_NETWORK_ERROR")
        except _DistrictHttpError:
            return _failure(fetched_at, "DISTRICT_HTTP_ERROR")
        except _DistrictProviderError:
            return _failure(fetched_at, "DISTRICT_PROVIDER_ERROR")
        except (ValueError, TypeError):
            return _failure(fetched_at, "DISTRICT_INVALID_RESPONSE")

        if len(districts) != 1:
            return _failure(fetched_at, "DISTRICT_INVALID_RESPONSE")
        try:
            city = _city_from_district(districts[0])
            if city is None:
                raise ValueError("district is not a city")
            rings = _parse_polyline(districts[0].get("polyline"), self._max_points)
            boundary = DistrictBoundary(city=city, rings=rings, fetched_at=fetched_at)
        except (ValueError, TypeError):
            return _failure(fetched_at, "DISTRICT_INVALID_RESPONSE")
        return ProviderResult(boundary, AMAP_DISTRICT_SOURCE, fetched_at)

    def _request(self, keywords: str, *, extensions: str) -> list[dict[str, object]]:
        response = self._client.get(
            AMAP_DISTRICT_URL,
            params={
                "key": self._key,
                "keywords": keywords,
                "subdistrict": "0",
                "extensions": extensions,
                "output": "JSON",
            },
            headers={"Accept": "application/json"},
            timeout=self._timeout_seconds,
        )
        if not response.is_success:
            raise _DistrictHttpError
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("district payload must be an object")
        if payload.get("status") != "1" or payload.get("infocode") != "10000":
            raise _DistrictProviderError
        districts = payload.get("districts")
        if not isinstance(districts, list) or not all(
            isinstance(district, dict) for district in districts
        ):
            raise ValueError("districts must be a list of objects")
        return districts


class _DistrictHttpError(Exception):
    pass


class _DistrictProviderError(Exception):
    pass


def _city_from_district(district: dict[str, object]) -> CityRecord | None:
    adcode = _required_adcode(district, "adcode")
    level = _required_string(district, "level")
    name = _required_string(district, "name")
    center = _parse_center(district.get("center"))
    if level == "city":
        return CityRecord(
            city_adcode=adcode,
            city_name=name,
            province_adcode=f"{adcode[:2]}0000",
            province_name=_required_string(district, "province"),
            center=center,
        )
    if level == "province" and adcode in _DIRECT_MUNICIPALITY_ADCODES:
        return CityRecord(
            city_adcode=adcode,
            city_name=name,
            province_adcode=adcode,
            province_name=name,
            center=center,
        )
    return None


def _parse_center(value: object) -> tuple[float, float]:
    if not isinstance(value, str):
        raise TypeError("center must be a string")
    return _parse_point(value)


def _parse_polyline(value: object, max_points: int) -> list[list[tuple[float, float]]]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("polyline is required")
    rings: list[list[tuple[float, float]]] = []
    point_count = 0
    for raw_ring in value.split("|"):
        if not raw_ring:
            raise ValueError("empty polyline ring")
        ring = [_parse_point(raw_point) for raw_point in raw_ring.split(";")]
        if len(ring) < 3 or len(set(ring)) < 3:
            raise ValueError("polyline ring needs three distinct points")
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        point_count += len(ring)
        if point_count > max_points:
            raise ValueError("polyline exceeds point limit")
        rings.append(ring)
    return rings


def _parse_point(value: str) -> tuple[float, float]:
    pieces = value.split(",")
    if len(pieces) != 2:
        raise ValueError("point must have longitude and latitude")
    longitude, latitude = (float(piece) for piece in pieces)
    if not (
        isfinite(longitude)
        and isfinite(latitude)
        and -180.0 <= longitude <= 180.0
        and -90.0 <= latitude <= 90.0
    ):
        raise ValueError("point is outside geographic bounds")
    return longitude, latitude


def _required_adcode(district: dict[str, object], field: str) -> str:
    value = _required_string(district, field)
    if not _ADCODE_PATTERN.fullmatch(value):
        raise ValueError("adcode must have six digits")
    return value


def _required_string(district: dict[str, object], field: str) -> str:
    value = district.get(field)
    if not isinstance(value, str) or not (result := value.strip()):
        raise ValueError(f"{field} is required")
    return result


def _failure(fetched_at: datetime, error_code: str) -> ProviderResult[DistrictBoundary]:
    return ProviderResult(
        data=None,
        source=AMAP_DISTRICT_SOURCE,
        fetched_at=fetched_at,
        degraded=True,
        error_code=error_code,
    )
