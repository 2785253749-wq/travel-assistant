from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import re

import httpx
from pydantic import SecretStr, ValidationError

from app.core.errors import AppError
from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
    HotelSummary,
)
from app.hotels.provider import HotelProvider


BAIDU_PLACE_REGION_URL = "https://api.map.baidu.com/place/v3/region"
BAIDU_PLACE_AROUND_URL = "https://api.map.baidu.com/place/v3/around"
BAIDU_PLACE_DETAIL_URL = "https://api.map.baidu.com/place/v3/detail"
BAIDU_HOTEL_PROVIDER_NAME = "baidu"
_HOTEL_FILTER = "industry_type:hotel"
_DETAIL_TAG_SEPARATOR = re.compile(r"[,，;；|/]")


class BaiduHotelProviderError(AppError):
    """Stable, sanitized failure raised by the Baidu hotel adapter."""


_ERROR_MESSAGES = {
    "BAIDU_HOTEL_NOT_CONFIGURED": "Baidu hotel provider is not configured",
    "BAIDU_HOTEL_INVALID_REQUEST": "Baidu hotel request is invalid",
    "BAIDU_HOTEL_TIMEOUT": "Baidu hotel request timed out",
    "BAIDU_HOTEL_NETWORK_ERROR": "Baidu hotel network request failed",
    "BAIDU_HOTEL_HTTP_ERROR": "Baidu hotel service returned an HTTP error",
    "BAIDU_HOTEL_INVALID_RESPONSE": "Baidu hotel service returned an invalid response",
    "BAIDU_HOTEL_PROVIDER_ERROR": "Baidu hotel service returned an error",
}


class BaiduHotelProvider(HotelProvider):
    """Synchronous adapter from Baidu Place API 3.0 to hotel domain models."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = _secret_value(api_key)
        if not self._api_key:
            raise _provider_error("BAIDU_HOTEL_NOT_CONFIGURED")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    def search(
        self,
        request: HotelSearchRequest | HotelNearbySearchRequest,
    ) -> HotelSearchResult:
        fetched_at = _utc_now()
        if isinstance(request, HotelSearchRequest):
            url = BAIDU_PLACE_REGION_URL
            params = _region_params(request, self._api_key)
        elif isinstance(request, HotelNearbySearchRequest):
            url = BAIDU_PLACE_AROUND_URL
            params = _around_params(request, self._api_key)
        else:
            raise _provider_error("BAIDU_HOTEL_INVALID_REQUEST")

        payload = self._request_json(url, params)
        _check_status(payload)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE")

        items: list[HotelSummary] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            summary = _parse_summary(raw_result)
            if summary is not None:
                items.append(summary)

        return HotelSearchResult(
            items=items[: request.page_size],
            total=_safe_nonnegative_int(payload.get("total")),
            page=request.page,
            page_size=request.page_size,
            provider=BAIDU_HOTEL_PROVIDER_NAME,
            status="success",
            warning=None,
            fetched_at=fetched_at,
        )

    def get_detail(self, hotel_id: str) -> HotelDetail | None:
        payload = self._request_json(
            BAIDU_PLACE_DETAIL_URL,
            {
                "uid": hotel_id,
                "scope": "2",
                "ret_coordtype": "gcj02ll",
                "output": "json",
                "ak": self._api_key,
            },
        )
        _check_status(payload)
        if "results" not in payload:
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE")
        raw_results = payload["results"]
        if not isinstance(raw_results, list):
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE")
        if not raw_results:
            return None
        raw_result = raw_results[0]
        if not isinstance(raw_result, dict):
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE")

        summary = _parse_summary(raw_result)
        if summary is None:
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE")
        detail_info = _detail_info(raw_result)
        try:
            return HotelDetail(
                **summary.model_dump(),
                tags=_parse_tags(detail_info),
                business_hours=_optional_text(detail_info.get("shop_hours")),
                description=_optional_text(detail_info.get("description")),
                detail_url=_optional_text(detail_info.get("detail_url")),
            )
        except ValidationError:
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE") from None

    def _request_json(
        self,
        url: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        try:
            response = self._client.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            raise _provider_error("BAIDU_HOTEL_TIMEOUT") from None
        except httpx.RequestError:
            raise _provider_error("BAIDU_HOTEL_NETWORK_ERROR") from None

        if not response.is_success:
            raise _provider_error("BAIDU_HOTEL_HTTP_ERROR")
        try:
            payload = response.json()
        except ValueError:
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE") from None
        if not isinstance(payload, dict):
            raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE")
        return payload


def _region_params(request: HotelSearchRequest, api_key: str) -> dict[str, object]:
    return _common_params(request.page, request.page_size, api_key) | {
        "query": request.keyword,
        "region": request.city,
        "region_limit": "true",
    }


def _around_params(
    request: HotelNearbySearchRequest,
    api_key: str,
) -> dict[str, object]:
    return _common_params(request.page, request.page_size, api_key) | {
        "query": request.keyword,
        "location": f"{request.latitude},{request.longitude}",
        "radius": request.radius,
        "radius_limit": "true",
        "coord_type": "2",
    }


def _common_params(page: int, page_size: int, api_key: str) -> dict[str, object]:
    return {
        "scope": "2",
        "page_num": page - 1,
        "page_size": max(page_size, 10),
        "filter": _HOTEL_FILTER,
        "ret_coordtype": "gcj02ll",
        "output": "json",
        "ak": api_key,
    }


def _parse_summary(raw: dict[str, object]) -> HotelSummary | None:
    uid = _required_text(raw.get("uid"))
    name = _required_text(raw.get("name"))
    if uid is None or name is None:
        return None

    detail_info = _detail_info(raw)
    location = raw.get("location")
    location_dict = location if isinstance(location, dict) else {}
    try:
        return HotelSummary(
            id=uid,
            name=name,
            address=_optional_text(raw.get("address")),
            latitude=_safe_float(location_dict.get("lat")),
            longitude=_safe_float(location_dict.get("lng")),
            rating=_safe_nonnegative_float(detail_info.get("overall_rating")),
            telephone=_optional_text(raw.get("telephone")),
            distance=_safe_distance(
                detail_info.get("distance", raw.get("distance"))
            ),
            provider=BAIDU_HOTEL_PROVIDER_NAME,
        )
    except ValidationError:
        return None


def _detail_info(raw: dict[str, object]) -> dict[str, object]:
    value = raw.get("detail_info")
    return value if isinstance(value, dict) else {}


def _parse_tags(detail_info: dict[str, object]) -> list[str]:
    tags: list[str] = []
    for field in ("tag", "classified_poi_tag", "label"):
        value = detail_info.get(field)
        if not isinstance(value, str):
            continue
        for tag in _DETAIL_TAG_SEPARATOR.split(value):
            normalized = tag.strip()
            if normalized and normalized not in tags:
                tags.append(normalized)
    return tags


def _secret_value(value: SecretStr | str | None) -> str:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value.strip() if isinstance(value, str) else ""


def _required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_text(value: object) -> str | None:
    return _required_text(value)


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        if not value.strip():
            return None
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if isfinite(parsed) else None


def _safe_nonnegative_float(value: object) -> float | None:
    parsed = _safe_float(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _safe_distance(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if isfinite(value) and value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            parsed = float(normalized)
        except ValueError:
            return None
        return int(parsed) if isfinite(parsed) and parsed >= 0 and parsed.is_integer() else None
    return None


def _safe_nonnegative_int(value: object) -> int | None:
    parsed = _safe_distance(value)
    return parsed


def _check_status(payload: dict[str, object]) -> None:
    if "status" not in payload:
        raise _provider_error("BAIDU_HOTEL_INVALID_RESPONSE")
    status = payload.get("status")
    if isinstance(status, bool) or status not in (0, "0"):
        raise _provider_error("BAIDU_HOTEL_PROVIDER_ERROR")


def _provider_error(code: str) -> BaiduHotelProviderError:
    return BaiduHotelProviderError(code, _ERROR_MESSAGES[code])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
