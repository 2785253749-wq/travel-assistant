from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from time import monotonic
from typing import Callable

import httpx

from app.agent.graph import TrustedEvidence
from app.providers.base import (
    ProviderResult,
    HTTP_TIMEOUT,
    OperationDeadline,
    UpstreamHttpError,
    UpstreamPayloadError,
    request_json,
    utc_now,
)


PLACES_SOURCE = "https://photon.komoot.io/api/"
_PLACE_SUFFIX = re.compile(r"(?:景区|风景区|旅游区|公园)$")
_NON_TOURISM_MARKERS = (
    "公司",
    "制药",
    "药厂",
    "工厂",
    "工业园",
    "产业园",
    "办公机构",
    "办公楼",
)
_EXPLICIT_NON_TOURISM_MARKERS = (
    "企业",
    "工业旅游",
    "参观公司",
    "参观企业",
    "工厂参观",
    "工厂旅游",
)


@dataclass(frozen=True)
class Place:
    name: str
    city: str | None = None


class PlacesProvider:
    def __init__(
        self,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT)
        self._clock = clock

    def search(self, city: str, query: str) -> ProviderResult[list[Place]]:
        fetched_at = utc_now()
        deadline = OperationDeadline.start(self._clock)
        try:
            places = self._search(query, deadline)
            allow_non_tourism = _allows_non_tourism(query)
            places = _filter_travel_places(places, allow_non_tourism=allow_non_tourism)
            if not places:
                rewritten = f"{city.strip()} {_normalized_alias(query)}".strip()
                places = self._search(rewritten, deadline)
                places = _filter_travel_places(places, allow_non_tourism=allow_non_tourism)
                if not places:
                    return ProviderResult(
                        [], PLACES_SOURCE, fetched_at, True,
                        "PLACES_EMPTY_AFTER_RETRY",
                    )
        except httpx.TimeoutException:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_TIMEOUT")
        except httpx.RequestError:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_NETWORK_ERROR")
        except UpstreamHttpError:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_HTTP_ERROR")
        except UpstreamPayloadError:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_INVALID_RESPONSE")

        evidence = tuple(_place_evidence(place, fetched_at) for place in places)
        return ProviderResult(places, PLACES_SOURCE, fetched_at, evidence=evidence)

    def _search(self, query: str, deadline: OperationDeadline) -> list[Place]:
        payload = request_json(
            self._client,
            PLACES_SOURCE,
            {"q": query, "limit": "10"},
            deadline,
        )
        features = payload.get("features")
        if not isinstance(features, list):
            raise UpstreamPayloadError
        results: list[Place] = []
        for feature in features:
            if not isinstance(feature, dict):
                raise UpstreamPayloadError
            properties = feature.get("properties")
            if not isinstance(properties, dict):
                raise UpstreamPayloadError
            name = properties.get("name")
            city = properties.get("city")
            if not isinstance(name, str) or not name.strip():
                raise UpstreamPayloadError
            if city is not None and not isinstance(city, str):
                raise UpstreamPayloadError
            results.append(Place(name.strip(), city.strip() if city else None))
        return results


def _normalized_alias(query: str) -> str:
    normalized = _PLACE_SUFFIX.sub("", query.strip())
    return normalized or query.strip()


def _allows_non_tourism(query: str) -> bool:
    return any(marker in query for marker in _EXPLICIT_NON_TOURISM_MARKERS)


def _filter_travel_places(
    places: list[Place], *, allow_non_tourism: bool = False,
) -> list[Place]:
    if allow_non_tourism:
        return places
    return [
        place
        for place in places
        if not any(marker in place.name for marker in _NON_TOURISM_MARKERS)
    ]


def _place_evidence(place: Place, fetched_at) -> TrustedEvidence:
    fact = f"{place.name}（{place.city or '地点城市待确认'}）"
    return TrustedEvidence(
        evidence_id=f"place-{sha256(fact.encode('utf-8')).hexdigest()[:16]}",
        fact=fact,
        source_url=PLACES_SOURCE,
        source_type="trusted_provider",
        fetched_at=fetched_at,
    )
