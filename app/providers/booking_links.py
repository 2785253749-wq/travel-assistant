from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, urlunparse

from app.schemas import TravelProfile


_ALLOWED_BOOKING_HOSTS = frozenset({"www.12306.cn", "www.ctrip.com"})
DISCLAIMER = "价格和库存以第三方平台为准；链接仅用于搜索跳转，不代表已确认的价格或库存。"


@dataclass(frozen=True)
class BookingLinks:
    train: str
    hotel: str
    flight: str
    disclaimer: str = DISCLAIMER


class BookingLinkBuilder:
    """Build fixed-provider search links only; callers never supply a base URL."""

    def build(self, profile: TravelProfile) -> BookingLinks:
        values = {
            "origin": profile.origin or "",
            "destination": profile.destination or "",
            "start_date": profile.start_date or "",
            "end_date": profile.end_date or "",
            "travelers": str(profile.travelers or 1),
        }
        return BookingLinks(
            train=_search_url("https://www.12306.cn/index/index.html", {
                "fromStation": values["origin"], "toStation": values["destination"], "startDate": values["start_date"],
            }),
            hotel=_search_url("https://www.ctrip.com/hotels/list", {
                "city": values["destination"], "checkIn": values["start_date"], "checkOut": values["end_date"], "adult": values["travelers"],
            }),
            flight=_search_url("https://www.ctrip.com/flights", {
                "from": values["origin"], "to": values["destination"], "depart": values["start_date"], "adult": values["travelers"],
            }),
        )


def _search_url(base_url: str, parameters: dict[str, str]) -> str:
    parsed = urlparse(base_url)
    try:
        explicit_port = parsed.port is not None
    except ValueError:
        explicit_port = True
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_BOOKING_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or explicit_port
    ):
        raise ValueError("Booking search host is not allowlisted")
    return urlunparse((
        "https",
        parsed.hostname,
        parsed.path or "/",
        "",
        urlencode(parameters, encoding="utf-8", safe=""),
        "",
    ))
