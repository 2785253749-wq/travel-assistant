from __future__ import annotations

from datetime import datetime, timezone

from app.application.hotel_nearby import HotelNearbyApplicationResult
from app.hotels.models import HotelSearchResult, HotelSummary
from app.locations.models import ResolvedLocation


FETCHED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


def _result(hotels: list[HotelSummary]) -> HotelNearbyApplicationResult:
    return HotelNearbyApplicationResult(
        location=ResolvedLocation(
            id="location-1",
            name="厦门大学",
            latitude=24.438,
            longitude=118.097,
            address="测试地点地址",
            city="厦门市",
            district="思明区",
            province="福建省",
            provider="baidu",
        ),
        hotels=HotelSearchResult(
            items=hotels,
            total=len(hotels),
            page=1,
            page_size=10,
            provider="baidu",
            status="success",
            fetched_at=FETCHED_AT,
        ),
    )


def test_renderer_shows_location_radius_and_first_three_hotels_in_order() -> None:
    from app.application.hotel_nearby_reply import HotelNearbyReplyRenderer

    hotels = [
        HotelSummary(
            id="hotel-1",
            name="厦门大学宾馆",
            address="思明区大学路 1 号",
            distance=320,
            provider="baidu",
        ),
        HotelSummary(
            id="hotel-2",
            name="海景酒店",
            address="思明区环岛路 2 号",
            distance=1200,
            provider="baidu",
        ),
        HotelSummary(
            id="hotel-3",
            name="第三家酒店",
            distance=None,
            provider="baidu",
        ),
        HotelSummary(
            id="hotel-4",
            name="不应展示的酒店",
            provider="baidu",
        ),
    ]

    reply = HotelNearbyReplyRenderer().render(_result(hotels), radius=2000)

    assert "厦门大学" in reply
    assert "2 公里内" in reply
    assert "2 公里 内" not in reply
    assert "找到 4 家酒店，先为你展示前 3 家" in reply
    assert "厦门大学宾馆" in reply
    assert "320 米" in reply
    assert "思明区大学路 1 号" in reply
    assert "海景酒店" in reply
    assert "1.2 公里" in reply
    assert "第三家酒店" in reply
    assert "不应展示的酒店" not in reply
    assert reply.index("厦门大学宾馆") < reply.index("海景酒店") < reply.index("第三家酒店")


def test_renderer_omits_optional_hotel_fields_when_missing() -> None:
    from app.application.hotel_nearby_reply import HotelNearbyReplyRenderer

    reply = HotelNearbyReplyRenderer().render(
        _result([HotelSummary(id="hotel-1", name="简洁酒店", provider="baidu")]),
        radius=500,
    )

    assert "简洁酒店" in reply
    assert "地址：" not in reply
    assert "距离：" not in reply
    assert "前 3 家" not in reply


def test_renderer_does_not_claim_a_prefix_when_all_three_hotels_are_shown() -> None:
    from app.application.hotel_nearby_reply import HotelNearbyReplyRenderer

    hotels = [
        HotelSummary(id=f"hotel-{index}", name=f"酒店{index}", provider="baidu")
        for index in range(1, 4)
    ]

    reply = HotelNearbyReplyRenderer().render(_result(hotels), radius=500)

    assert "500 米内" in reply
    assert "找到 3 家酒店" in reply
    assert "前 3 家" not in reply


def test_renderer_reports_empty_results_without_inventing_hotels() -> None:
    from app.application.hotel_nearby_reply import HotelNearbyReplyRenderer

    reply = HotelNearbyReplyRenderer().render(_result([]), radius=2000)

    assert "厦门大学" in reply
    assert "2 公里内" in reply
    assert "未找到" in reply
