from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

import httpx

from app.agent.graph import TrustedEvidence
from app.providers.base import (
    ProviderResult,
    HTTP_TIMEOUT,
    UpstreamHttpError,
    UpstreamPayloadError,
    request_json,
    utc_now,
)


PLACES_SOURCE = "https://photon.komoot.io/api/"
_PLACE_SUFFIX = re.compile(r"(?:景区|风景区|旅游区|公园)$")


@dataclass(frozen=True)
class Place:
    name: str
    city: str | None = None


class PlacesProvider:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT)

    def search(self, city: str, query: str) -> ProviderResult[list[Place]]:
        fetched_at = utc_now()
        try:
            places = self._search(query)
            if not places:
                rewritten = f"{city.strip()} {_normalized_alias(query)}".strip()
                places = self._search(rewritten)
        except httpx.TimeoutException:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_TIMEOUT")
        except httpx.RequestError:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_NETWORK_ERROR")
        except UpstreamHttpError:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_HTTP_ERROR")
        except UpstreamPayloadError:
            return ProviderResult([], PLACES_SOURCE, fetched_at, True, "PLACES_INVALID_RESPONSE")

        evidence = tuple(_place_evidence(place) for place in places)
        return ProviderResult(places, PLACES_SOURCE, fetched_at, evidence=evidence)

    def _search(self, query: str) -> list[Place]:
        payload = request_json(self._client, PLACES_SOURCE, {"q": query, "limit": "10"})
        if payload is None:
            return []
        features = payload.get("features")
        if not isinstance(features, list):
            return []
        results: list[Place] = []
        for feature in features:
            properties = feature.get("properties", feature) if isinstance(feature, dict) else {}
            name = properties.get("name") if isinstance(properties, dict) else None
            city = properties.get("city") if isinstance(properties, dict) else None
            if isinstance(name, str) and name.strip():
                results.append(Place(name.strip(), city.strip() if isinstance(city, str) else None))
        return results


def _normalized_alias(query: str) -> str:
    normalized = _PLACE_SUFFIX.sub("", query.strip())
    return normalized or query.strip()


def _place_evidence(place: Place) -> TrustedEvidence:
    fact = f"{place.name}（{place.city or '地点城市待确认'}）"
    return TrustedEvidence(
        evidence_id=f"place-{sha256(fact.encode('utf-8')).hexdigest()[:16]}",
        fact=fact,
        source_url=PLACES_SOURCE,
        source_type="trusted_provider",
    )
