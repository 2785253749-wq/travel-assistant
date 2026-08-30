"""Deterministic extraction for the MVP train-search intent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

from app.trains.models import DepartureTimeRange, TrainPreference, TrainQuery, TrainType


_CHINA_TIMEZONE = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class TrainQueryExtraction:
    departure_station: str | None = None
    arrival_station: str | None = None
    travel_date: date | None = None
    train_types: tuple[TrainType, ...] | None = None
    departure_time_range: DepartureTimeRange | None = None
    seat_type: str | None = None
    require_available: bool = False
    preference: TrainPreference = "default"

    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, value in (
                ("departure_station", self.departure_station),
                ("arrival_station", self.arrival_station),
                ("travel_date", self.travel_date),
            )
            if value in (None, "")
        )

    @property
    def query(self) -> TrainQuery | None:
        if self.missing_fields:
            return None
        return TrainQuery(
            departure_station=self.departure_station or "",
            arrival_station=self.arrival_station or "",
            travel_date=self.travel_date,
            train_types=self.train_types,
            departure_time_range=self.departure_time_range,
            seat_type=self.seat_type,
            require_available=self.require_available,
            preference=self.preference,
        )


class TrainQueryExtractor:
    """Extract only explicit rail-search fields; it never guesses a city or date."""

    _ROUTE = re.compile(
        r"(?:从|由)?\s*([\u4e00-\u9fffA-Za-z·]{1,30}?)\s*"
        r"(?:前往|去往|到达|抵达|到|去|往|to)\s*"
        r"([\u4e00-\u9fffA-Za-z·]{1,30}?)(?=\s*(?:的|有|查|看|找|哪些|什么|车次|车|高铁|动车|城际|火车|列车|上午|早上|下午|晚上|凌晨|二等|一等|商务|最|便宜|最快|耗时|，|,|。|！|!|\d|$))",
        re.IGNORECASE,
    )
    _DESTINATION_ONLY = re.compile(
        r"^(?:前往|去往|到达|抵达|到|去|往)\s*([\u4e00-\u9fffA-Za-z·]{1,30}?)(?=\s*(?:有|查|看|找|哪些|什么|车次|车|高铁|动车|城际|火车|列车|$))",
        re.IGNORECASE,
    )
    _DEPARTURE_ONLY = re.compile(
        r"^([\u4e00-\u9fffA-Za-z·]{1,30}?)\s*(?:坐|乘|搭|出发)(?=\s*(?:高铁|动车|城际|火车|列车|车|$))",
        re.IGNORECASE,
    )
    _ISO_DATE = re.compile(r"(?<!\d)(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.]([0-3]?\d)")
    _CHINESE_DATE = re.compile(r"(?<!\d)(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月\s*([0-3]?\d)\s*[日号]?")
    _SHORT_DATE = re.compile(r"(?<![\d年月])\s*(0?[1-9]|1[0-2])\s*月\s*([0-3]?\d)\s*[日号]?")

    def __init__(self, reference_date: date | None = None) -> None:
        self.reference_date = reference_date or datetime.now(_CHINA_TIMEZONE).date()

    def extract(self, message: str) -> TrainQueryExtraction:
        normalized = message.strip()
        departure_station, arrival_station = self._extract_route(normalized)
        return TrainQueryExtraction(
            departure_station=departure_station,
            arrival_station=arrival_station,
            travel_date=self._extract_date(normalized),
            train_types=self._extract_train_types(normalized),
            departure_time_range=self._extract_time_range(normalized),
            seat_type=self._extract_seat(normalized),
            require_available=(
                any(term in normalized for term in ("有票", "还有票", "能买到", "只看有票"))
                and not any(term in normalized for term in ("没有票", "无票"))
            ),
            preference=self._extract_preference(normalized),
        )

    @classmethod
    def _extract_route(cls, message: str) -> tuple[str | None, str | None]:
        route_message = re.sub(
            r"^\s*(?:今天|明天|后天|大后天|20\d{2}[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}\s*[日号]?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?)\s*",
            "",
            message,
        )
        match = cls._ROUTE.search(route_message)
        if match is None:
            destination_only = cls._DESTINATION_ONLY.search(route_message)
            if destination_only is not None:
                return None, destination_only.group(1).strip(" ，,。！？!?") or None
            departure_only = cls._DEPARTURE_ONLY.search(route_message)
            if departure_only is not None:
                return departure_only.group(1).strip(" ，,。！？!?") or None, None
            return None, None
        departure, arrival = (part.strip(" ，,。！？!?的") for part in match.groups())
        if not departure or not arrival or departure == arrival:
            return departure or None, arrival or None
        return departure, arrival

    def _extract_date(self, message: str) -> date | None:
        if "大后天" in message:
            return self.reference_date + timedelta(days=3)
        if "后天" in message:
            return self.reference_date + timedelta(days=2)
        if "明天" in message:
            return self.reference_date + timedelta(days=1)
        if "今天" in message:
            return self.reference_date
        candidates: list[tuple[int, date]] = []
        for pattern in (self._ISO_DATE, self._CHINESE_DATE):
            for match in pattern.finditer(message):
                try:
                    candidates.append((match.start(), date(*(int(value) for value in match.groups()))))
                except ValueError:
                    continue
        for match in self._SHORT_DATE.finditer(message):
            try:
                candidates.append((match.start(), date(self.reference_date.year, int(match.group(1)), int(match.group(2)))))
            except ValueError:
                continue
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    @staticmethod
    def _extract_train_types(message: str) -> tuple[TrainType, ...] | None:
        found: list[TrainType] = []
        for term, train_type in (("高铁", "G"), ("动车", "D"), ("城际", "C")):
            if term in message and train_type not in found:
                found.append(train_type)
        return tuple(found) or None

    @staticmethod
    def _extract_time_range(message: str) -> DepartureTimeRange | None:
        for terms, value in (
            (("凌晨",), "凌晨"),
            (("上午", "早上"), "上午"),
            (("下午",), "下午"),
            (("晚上", "傍晚"), "晚上"),
        ):
            if any(term in message for term in terms):
                return value
        return None

    @staticmethod
    def _extract_seat(message: str) -> str | None:
        for terms, value in (
            (("商务座", "商务"), "商务座"),
            (("一等座", "一等"), "一等座"),
            (("二等座", "二等"), "二等座"),
        ):
            if any(term in message for term in terms):
                return value
        return None

    @staticmethod
    def _extract_preference(message: str) -> TrainPreference:
        if any(term in message for term in ("最快", "时间最短", "耗时最短", "耗时短", "用时最短", "少坐一会")):
            return "fastest"
        if any(term in message for term in ("最便宜", "便宜一点", "尽量便宜", "价格低", "省钱")):
            return "cheapest"
        if any(term in message for term in ("最早到", "早点到", "尽早到达")):
            return "earliest_arrival"
        return "default"
