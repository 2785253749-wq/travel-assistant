"""Deterministic extraction for Attraction Search queries."""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.attractions.models import AttractionSearchMode, AttractionSortBy


@dataclass(frozen=True)
class AttractionSearchQueryExtraction:
    """Provider-independent fields extracted from an attraction search request."""

    mode: AttractionSearchMode | None = None
    city: str | None = None
    location_query: str | None = None
    radius: int | None = None
    sort_by: AttractionSortBy | None = None
    invalid_fields: tuple[str, ...] = ()

    @property
    def missing_fields(self) -> tuple[str, ...]:
        if self.mode == "city":
            return ("city",) if self.city in (None, "") else ()
        if self.mode == "nearby":
            missing: list[str] = []
            if self.city in (None, ""):
                missing.append("city")
            if self.location_query in (None, ""):
                missing.append("location_query")
            return tuple(missing)
        return ()


class AttractionSearchQueryExtractor:
    """Extract only explicit city and nearby attraction-search parameters."""

    _PREFIX = re.compile(r"^(?:帮我找|帮我查|找一下|查一下|推荐|看看)\s*")
    _SEARCH_VERB = re.compile(r"^(?:找|查|推荐|看看)\s*")
    _NEARBY = re.compile(r"^(?P<location>.*?)\s*(?:附近|周边|周围)")
    _RADIUS = re.compile(
        r"(?:(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>米|公里)|(?P<chinese>[一二三两])\s*公里)"
    )
    _SUPPORTED_CITIES = ("厦门", "泉州", "福州", "杭州", "北京", "成都")
    _CITY_PREFIX_PATTERN = "|".join(
        re.escape(city) for city in _SUPPORTED_CITIES
    )
    _EXPLICIT_CITY = re.compile(
        rf"^(?P<city>{_CITY_PREFIX_PATTERN})的(?P<location>.+)$"
    )
    _CITY_PREFIXES = _SUPPORTED_CITIES
    _CHINESE_KILOMETERS = {"一": 1000, "二": 2000, "两": 2000, "三": 3000}
    _MIN_RADIUS = 500
    _MAX_RADIUS = 20_000

    def extract(self, message: str) -> AttractionSearchQueryExtraction:
        normalized = " ".join(message.strip().split())
        body = self._SEARCH_VERB.sub("", self._PREFIX.sub("", normalized))
        if "景点" not in body:
            return AttractionSearchQueryExtraction()

        nearby_match = self._NEARBY.match(body)
        radius = self._extract_radius(normalized)
        sort_by = self._extract_sort_by(normalized)
        invalid_fields: list[str] = []
        if radius is not None and not self._MIN_RADIUS <= radius <= self._MAX_RADIUS:
            invalid_fields.append("radius")

        if nearby_match is not None:
            location = nearby_match.group("location").strip()
            city: str | None = None

            explicit_city = self._EXPLICIT_CITY.match(location)
            if explicit_city is not None:
                city = explicit_city.group("city")
                location = explicit_city.group("location").strip()
            elif location in self._CITY_PREFIXES:
                city = location
                location = ""
            else:
                for prefix in self._CITY_PREFIXES:
                    if location.startswith(prefix) and len(location) > len(prefix):
                        city = prefix
                        break

            mode: AttractionSearchMode = "city" if not location and city else "nearby"
            if mode == "city" and sort_by == "distance":
                invalid_fields.append("sort_by")
            return AttractionSearchQueryExtraction(
                mode=mode,
                city=city,
                location_query=location or None,
                radius=radius,
                sort_by=sort_by,
                invalid_fields=tuple(invalid_fields),
            )

        city = next(
            (prefix for prefix in self._CITY_PREFIXES if body.startswith(prefix)),
            None,
        )
        if sort_by == "distance":
            invalid_fields.append("sort_by")
        return AttractionSearchQueryExtraction(
            mode="city",
            city=city,
            radius=radius,
            sort_by=sort_by,
            invalid_fields=tuple(invalid_fields),
        )

    @classmethod
    def _extract_radius(cls, message: str) -> int | None:
        match = cls._RADIUS.search(message)
        if match is None:
            return None
        if match.group("chinese") is not None:
            return cls._CHINESE_KILOMETERS[match.group("chinese")]
        value = float(match.group("value"))
        multiplier = 1000 if match.group("unit") == "公里" else 1
        return int(value * multiplier)

    @staticmethod
    def _extract_sort_by(message: str) -> AttractionSortBy | None:
        if any(term in message for term in ("评分最高", "评分好")):
            return "rating"
        if any(term in message for term in ("最近", "离这里最近")):
            return "distance"
        return None
