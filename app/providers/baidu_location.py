from __future__ import annotations

from math import isfinite

import httpx
from pydantic import SecretStr, ValidationError

from app.core.errors import AppError
from app.locations.models import (
    LocationCandidate,
    LocationQuery,
    LocationSearchResult,
)
from app.locations.provider import LocationProvider


BAIDU_PLACE_REGION_URL = "https://api.map.baidu.com/place/v3/region"
BAIDU_LOCATION_PROVIDER_NAME = "baidu"


class BaiduLocationProviderError(AppError):
    """Stable, sanitized failure raised by the Baidu location adapter."""


_ERROR_MESSAGES = {
    "BAIDU_LOCATION_NOT_CONFIGURED": "Baidu location provider is not configured",
    "BAIDU_LOCATION_REGION_REQUIRED": "Baidu location search requires a city",
    "BAIDU_LOCATION_TIMEOUT": "Baidu location request timed out",
    "BAIDU_LOCATION_NETWORK_ERROR": "Baidu location network request failed",
    "BAIDU_LOCATION_HTTP_ERROR": "Baidu location service returned an HTTP error",
    "BAIDU_LOCATION_INVALID_RESPONSE": "Baidu location service returned an invalid response",
    "BAIDU_LOCATION_PROVIDER_ERROR": "Baidu location service returned an error",
}


class BaiduLocationProvider(LocationProvider):
    """Synchronous adapter from Baidu Place API 3.0 to location models."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = _secret_value(api_key)
        if not self._api_key:
            raise _provider_error("BAIDU_LOCATION_NOT_CONFIGURED")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    def search(self, query: LocationQuery) -> LocationSearchResult:
        if query.city is None:
            raise _provider_error("BAIDU_LOCATION_REGION_REQUIRED")

        payload = self._request_json(
            {
                "query": query.query,
                "region": query.city,
                "region_limit": "true",
                "scope": "2",
                "page_num": "0",
                "page_size": "10",
                "ret_coordtype": "gcj02ll",
                "output": "json",
                "ak": self._api_key,
            }
        )
        _check_status(payload)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise _provider_error("BAIDU_LOCATION_INVALID_RESPONSE")

        items: list[LocationCandidate] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            candidate = _parse_candidate(raw_result)
            if candidate is not None:
                items.append(candidate)
        return LocationSearchResult(
            items=items,
            provider=BAIDU_LOCATION_PROVIDER_NAME,
        )

    def _request_json(self, params: dict[str, object]) -> dict[str, object]:
        try:
            response = self._client.get(
                BAIDU_PLACE_REGION_URL,
                params=params,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            raise _provider_error("BAIDU_LOCATION_TIMEOUT") from None
        except httpx.RequestError:
            raise _provider_error("BAIDU_LOCATION_NETWORK_ERROR") from None

        if not response.is_success:
            raise _provider_error("BAIDU_LOCATION_HTTP_ERROR")
        try:
            payload = response.json()
        except ValueError:
            raise _provider_error("BAIDU_LOCATION_INVALID_RESPONSE") from None
        if not isinstance(payload, dict):
            raise _provider_error("BAIDU_LOCATION_INVALID_RESPONSE")
        return payload


def _secret_value(value: SecretStr | str | None) -> str:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value.strip() if isinstance(value, str) else ""


def _provider_error(code: str) -> BaiduLocationProviderError:
    return BaiduLocationProviderError(code, _ERROR_MESSAGES[code])


def _parse_candidate(raw: dict[str, object]) -> LocationCandidate | None:
    name = _required_text(raw.get("name"))
    location = raw.get("location")
    if name is None or not isinstance(location, dict):
        return None

    latitude = _safe_float(location.get("lat"))
    longitude = _safe_float(location.get("lng"))
    if latitude is None or longitude is None:
        return None

    try:
        return LocationCandidate(
            id=_optional_text(raw.get("uid")),
            name=name,
            latitude=latitude,
            longitude=longitude,
            address=_optional_text(raw.get("address")),
            city=_optional_text(raw.get("city")),
            district=_optional_text(raw.get("area")),
            province=_optional_text(raw.get("province")),
            provider=BAIDU_LOCATION_PROVIDER_NAME,
        )
    except ValidationError:
        return None


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


def _check_status(payload: dict[str, object]) -> None:
    if "status" not in payload:
        raise _provider_error("BAIDU_LOCATION_INVALID_RESPONSE")
    status = payload.get("status")
    if isinstance(status, bool) or status not in (0, "0"):
        raise _provider_error("BAIDU_LOCATION_PROVIDER_ERROR")
