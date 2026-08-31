"""Deterministic extraction for the MVP nearby-hotel query."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class HotelNearbyQueryExtraction:
    """Provider-independent fields extracted from a nearby-hotel request."""

    location_query: str | None = None
    city: str | None = None
    radius: int | None = None
    invalid_fields: tuple[str, ...] = ()

    @property
    def missing_fields(self) -> tuple[str, ...]:
        if self.location_query in (None, ""):
            return ("location_query",)
        return ()


class HotelNearbyQueryExtractor:
    """Extract only explicit nearby-hotel search parameters."""

    _PREFIX = re.compile(r"^(?:帮我找|帮我查|找一下|查一下|推荐|看看)\s*")
    _SEARCH_VERB = re.compile(r"^(?:找|查|推荐|看看)\s*")
    _NEARBY = re.compile(r"^(?P<location>.*?)\s*(?:附近|周边|周围)")
    _RADIUS = re.compile(
        r"(?:(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>米|公里)|(?P<chinese>[一二三两])\s*公里)"
    )
    _EXPLICIT_CITY = re.compile(r"^(?P<city>厦门|泉州)的(?P<location>.+)$")
    _CITY_PREFIXES = ("厦门", "泉州")
    _CHINESE_KILOMETERS = {"一": 1000, "二": 2000, "两": 2000, "三": 3000}
    _MIN_RADIUS = 500
    _MAX_RADIUS = 20_000

    def extract(self, message: str) -> HotelNearbyQueryExtraction:
        normalized = " ".join(message.strip().split())
        body = self._SEARCH_VERB.sub("", self._PREFIX.sub("", normalized))
        match = self._NEARBY.match(body)
        if match is None:
            return HotelNearbyQueryExtraction()

        location = match.group("location").strip()
        city = None
        explicit_city = self._EXPLICIT_CITY.match(location)
        if explicit_city is not None:
            city = explicit_city.group("city")
            location = explicit_city.group("location").strip()
        for prefix in self._CITY_PREFIXES:
            if city is None and location.startswith(prefix) and len(location) > len(prefix):
                city = prefix
                break

        radius = self._extract_radius(normalized)
        invalid_fields = (
            ("radius",)
            if radius is not None and not self._MIN_RADIUS <= radius <= self._MAX_RADIUS
            else ()
        )
        return HotelNearbyQueryExtraction(
            location_query=location or None,
            city=city,
            radius=radius,
            invalid_fields=invalid_fields,
        )

    @classmethod
    def _extract_radius(cls, message: str) -> int | None:
        match = cls._RADIUS.search(message)
        if match is None:
            return None
        if match.group("chinese") is not None:
            return cls._CHINESE_KILOMETERS[match.group("chinese")]
        value = float(match.group("value"))
        return int(value * (1000 if match.group("unit") == "公里" else 1))
