from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import re
from typing import Literal, Mapping

import httpx
from pydantic import SecretStr, ValidationError

from app.attractions.models import (
    AttractionNearbySearchRequest,
    AttractionSearchRequest,
    AttractionSearchResult,
    AttractionSortBy,
    AttractionSummary,
)
from app.attractions.provider import AttractionProvider
from app.core.errors import AppError


BAIDU_PLACE_REGION_URL = "https://api.map.baidu.com/place/v3/region"
BAIDU_PLACE_AROUND_URL = "https://api.map.baidu.com/place/v3/around"
BAIDU_ATTRACTION_PROVIDER_NAME = "baidu"
_TAG_SEPARATOR = re.compile(r"[,，;；|/]")
_DEFAULT_SORT_FILTERS: Mapping[AttractionSortBy, str] = {
    "rating": "industry_type:life|sort_name:overall_rating|sort_rule:0",
    "distance": "industry_type:life|sort_name:distance|sort_rule:1",
}


class BaiduAttractionProviderError(AppError):
    """Stable, sanitized failure raised by the Baidu attraction adapter."""


_ERROR_MESSAGES = {
    "BAIDU_ATTRACTION_NOT_CONFIGURED": "Baidu attraction provider is not configured",
    "BAIDU_ATTRACTION_INVALID_REQUEST": "Baidu attraction request is invalid",
    "BAIDU_ATTRACTION_TIMEOUT": "Baidu attraction request timed out",
    "BAIDU_ATTRACTION_NETWORK_ERROR": "Baidu attraction network request failed",
    "BAIDU_ATTRACTION_HTTP_ERROR": "Baidu attraction service returned an HTTP error",
    "BAIDU_ATTRACTION_INVALID_RESPONSE": "Baidu attraction service returned an invalid response",
    "BAIDU_ATTRACTION_PROVIDER_ERROR": "Baidu attraction service returned an error",
}


class BaiduAttractionProvider(AttractionProvider):
    """Synchronous adapter from Baidu Place API 3.0 to attraction models."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
        category_filter: str | None = None,
        sort_filters: Mapping[AttractionSortBy, str] | None = None,
        contract_state: Literal["configured", "unverified"] | None = None,
    ) -> None:
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self._api_key = _secret_value(api_key)
        self._timeout = timeout
        self._category_filter = _configured_text(category_filter)
        configured_sort_filters = (
            _DEFAULT_SORT_FILTERS if sort_filters is None else sort_filters
        )
        self._sort_filters = {
            key: value.strip()
            for key, value in configured_sort_filters.items()
            if isinstance(value, str) and value.strip()
        }
        if contract_state is not None:
            self.contract_state = contract_state
        else:
            has_complete_custom_contract = (
                self._category_filter
                and all(key in self._sort_filters for key in ("rating", "distance"))
            )
            uses_builtin_contract = category_filter is None and sort_filters is None
            self.contract_state = (
                "configured"
                if self._api_key
                and (uses_builtin_contract or has_complete_custom_contract)
                else "unverified"
            )
        self._client = client or httpx.Client(
            timeout=timeout,
            trust_env=False,
        )

    def search(
        self,
        request: AttractionSearchRequest | AttractionNearbySearchRequest,
    ) -> AttractionSearchResult:
        if not isinstance(
            request, (AttractionSearchRequest, AttractionNearbySearchRequest)
        ):
            raise _provider_error("BAIDU_ATTRACTION_INVALID_REQUEST")
        if not self._api_key:
            return _unavailable(request, "BAIDU_ATTRACTION_NOT_CONFIGURED")
        if self.contract_state != "configured":
            return _unavailable(
                request,
                "BAIDU_ATTRACTION_NOT_CONFIGURED",
            )

        if isinstance(request, AttractionSearchRequest):
            url = BAIDU_PLACE_REGION_URL
            params = _region_params(request, self._api_key, self._filter(request.sort_by))
        else:
            url = BAIDU_PLACE_AROUND_URL
            params = _around_params(request, self._api_key, self._filter(request.sort_by))

        payload = self._request_json(url, params)
        _check_status(payload)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise _provider_error("BAIDU_ATTRACTION_INVALID_RESPONSE")

        items: list[AttractionSummary] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            summary = _parse_summary(raw_result)
            if summary is not None:
                items.append(summary)

        return AttractionSearchResult(
            items=items[: request.page_size],
            total=_safe_nonnegative_int(payload.get("total")),
            page=request.page,
            page_size=request.page_size,
            provider=BAIDU_ATTRACTION_PROVIDER_NAME,
            status="success",
            warning=None,
            fetched_at=_utc_now(),
        )

    def _filter(self, sort_by: AttractionSortBy | None) -> str:
        if sort_by is None:
            return self._category_filter or ""
        sort_filter = self._sort_filters[sort_by]
        if not self._category_filter:
            return sort_filter
        if sort_filter == self._category_filter or sort_filter.startswith(
            f"{self._category_filter}|"
        ):
            return sort_filter
        return f"{self._category_filter}|{sort_filter}"

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
            raise _provider_error("BAIDU_ATTRACTION_TIMEOUT") from None
        except httpx.RequestError:
            raise _provider_error("BAIDU_ATTRACTION_NETWORK_ERROR") from None

        if not response.is_success:
            raise _provider_error("BAIDU_ATTRACTION_HTTP_ERROR")
        try:
            payload = response.json()
        except ValueError:
            raise _provider_error("BAIDU_ATTRACTION_INVALID_RESPONSE") from None
        if not isinstance(payload, dict):
            raise _provider_error("BAIDU_ATTRACTION_INVALID_RESPONSE")
        return payload


def _region_params(
    request: AttractionSearchRequest,
    api_key: str,
    filter_expression: str,
) -> dict[str, object]:
    return _common_params(request.page, request.page_size, api_key, filter_expression) | {
        "query": request.keyword,
        "region": request.city,
        "region_limit": "true",
    }


def _around_params(
    request: AttractionNearbySearchRequest,
    api_key: str,
    filter_expression: str,
) -> dict[str, object]:
    return _common_params(request.page, request.page_size, api_key, filter_expression) | {
        "query": request.keyword,
        "location": f"{request.latitude},{request.longitude}",
        "radius": request.radius,
        "radius_limit": "true",
        "coord_type": "2",
    }


def _common_params(
    page: int,
    page_size: int,
    api_key: str,
    filter_expression: str,
) -> dict[str, object]:
    return {
        "scope": "2",
        "page_num": page - 1,
        "page_size": max(page_size, 10),
        "filter": filter_expression,
        "ret_coordtype": "gcj02ll",
        "output": "json",
        "ak": api_key,
    }


def _parse_summary(raw: dict[str, object]) -> AttractionSummary | None:
    name = _required_text(raw.get("name"))
    if name is None:
        return None

    location = raw.get("location")
    location_dict = location if isinstance(location, dict) else {}
    detail_info = _detail_info(raw)
    try:
        return AttractionSummary(
            id=_optional_text(raw.get("uid")),
            name=name,
            address=_optional_text(raw.get("address")),
            latitude=_safe_coordinate(location_dict.get("lat"), -90, 90),
            longitude=_safe_coordinate(location_dict.get("lng"), -180, 180),
            rating=_safe_nonnegative_float(detail_info.get("overall_rating")),
            comment_num=_safe_nonnegative_int(detail_info.get("comment_num")),
            distance=_safe_distance(
                detail_info.get("distance", raw.get("distance"))
            ),
            tags=_parse_tags(detail_info),
            provider=BAIDU_ATTRACTION_PROVIDER_NAME,
        )
    except ValidationError:
        return None


def _detail_info(raw: dict[str, object]) -> dict[str, object]:
    value = raw.get("detail_info")
    return value if isinstance(value, dict) else {}


def _parse_tags(detail_info: dict[str, object]) -> tuple[str, ...]:
    tags: list[str] = []
    for field in ("tag", "classified_poi_tag"):
        value = detail_info.get(field)
        if not isinstance(value, str):
            continue
        for tag in _TAG_SEPARATOR.split(value):
            normalized = tag.strip()
            if normalized and normalized not in tags:
                tags.append(normalized)
    return tuple(tags)


def _secret_value(value: SecretStr | str | None) -> str:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value.strip() if isinstance(value, str) else ""


def _configured_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _required_text(value: object) -> str | None:
    normalized = _configured_text(value)
    return normalized


def _optional_text(value: object) -> str | None:
    return _configured_text(value)


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


def _safe_coordinate(value: object, minimum: float, maximum: float) -> float | None:
    parsed = _safe_float(value)
    return parsed if parsed is not None and minimum <= parsed <= maximum else None


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
    return _safe_distance(value)


def _check_status(payload: dict[str, object]) -> None:
    if "status" not in payload:
        raise _provider_error("BAIDU_ATTRACTION_INVALID_RESPONSE")
    status = payload.get("status")
    if isinstance(status, bool) or status not in (0, "0"):
        raise _provider_error("BAIDU_ATTRACTION_PROVIDER_ERROR")


def _unavailable(
    request: AttractionSearchRequest | AttractionNearbySearchRequest,
    warning: str,
) -> AttractionSearchResult:
    return AttractionSearchResult(
        items=[],
        total=None,
        page=request.page,
        page_size=request.page_size,
        provider=BAIDU_ATTRACTION_PROVIDER_NAME,
        status="unavailable",
        warning=warning,
        fetched_at=_utc_now(),
    )


def _provider_error(code: str) -> BaiduAttractionProviderError:
    return BaiduAttractionProviderError(code, _ERROR_MESSAGES[code])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
