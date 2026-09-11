from __future__ import annotations

from datetime import UTC, datetime

from app.application.attraction_search import AttractionSearchApplicationResult
from app.attractions.models import AttractionSearchResult, AttractionSummary


FETCHED_AT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _renderer_type():
    from app.application.attraction_search_reply import AttractionReplyRenderer

    return AttractionReplyRenderer


def _summary(
    name: str,
    *,
    item_id: str | None = None,
    address: str | None = None,
    rating: float | None = None,
    comment_num: int | None = None,
    distance: int | None = None,
    tags: tuple[str, ...] = (),
    provider: str = "fake-provider",
) -> AttractionSummary:
    return AttractionSummary(
        id=item_id,
        name=name,
        address=address,
        rating=rating,
        comment_num=comment_num,
        distance=distance,
        tags=tags,
        provider=provider,
    )


def _result(
    items: list[AttractionSummary],
    *,
    mode: str = "city",
    city: str = "厦门",
    location_query: str | None = None,
    radius: int | None = None,
    total: int | None = None,
    status: str = "success",
    warning: str | None = None,
) -> AttractionSearchApplicationResult:
    return AttractionSearchApplicationResult(
        attractions=AttractionSearchResult(
            items=items,
            total=total,
            page=1,
            page_size=10,
            provider="fake-provider",
            status=status,
            warning=warning,
            fetched_at=FETCHED_AT,
        ),
        mode=mode,  # type: ignore[arg-type]
        city=city,
        location_query=location_query,
        radius=radius,
    )


def test_city_renderer_uses_total_and_shows_at_most_three() -> None:
    renderer = _renderer_type()()
    reply = renderer.render(
        _result(
            [
                _summary("A"),
                _summary("B"),
                _summary("C"),
                _summary("D"),
            ],
            total=10,
        )
    )

    assert "厦门" in reply
    assert "10 个景点" in reply
    assert all(name in reply for name in ("A", "B", "C"))
    assert "D" not in reply
    assert reply.index("A") < reply.index("B") < reply.index("C")


def test_city_renderer_never_shows_distance() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result([_summary("厦门大学", distance=320)], total=1)
    )

    assert "厦门大学" in reply
    assert "距离：" not in reply


def test_nearby_renderer_shows_location_radius_and_distance() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result(
            [_summary("附近景点", distance=320)],
            mode="nearby",
            location_query="厦门大学",
            radius=2000,
            total=1,
        )
    )

    assert "厦门大学" in reply
    assert "2 公里" in reply
    assert "距离：320 米" in reply


def test_nearby_renderer_formats_kilometer_distance() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result(
            [_summary("附近景点", distance=1500)],
            mode="nearby",
            location_query="厦门大学",
            radius=2000,
            total=1,
        )
    )

    assert "距离：1.5 公里" in reply


def test_renderer_uses_len_items_when_total_is_none() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result([_summary("A"), _summary("B")], total=None)
    )

    assert "2 个景点" in reply
    assert "0 个景点" not in reply


def test_renderer_omits_missing_optional_fields_without_fake_values() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result(
            [
                _summary(
                    "简洁景点",
                    rating=None,
                    comment_num=None,
                    distance=None,
                    address=None,
                    tags=(),
                )
            ],
            total=1,
        )
    )

    assert "评分：" not in reply
    assert "评论数：" not in reply
    assert "距离：" not in reply
    assert "地址：" not in reply
    assert "标签：" not in reply
    assert "暂无" not in reply


def test_renderer_displays_present_optional_fields() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result(
            [
                _summary(
                    "鼓浪屿",
                    rating=4.8,
                    comment_num=123,
                    address="鼓浪屿示例地址",
                    tags=("亲子", "自然"),
                )
            ],
            total=1,
        )
    )

    assert "评分：4.8" in reply
    assert "评论数：123" in reply
    assert "地址：鼓浪屿示例地址" in reply
    assert "标签：亲子、自然" in reply


def test_renderer_never_exposes_internal_fields() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result(
            [
                _summary(
                    "景点",
                    item_id="secret-internal-id",
                    provider="baidu",
                )
            ],
            total=1,
        )
    )

    assert "secret-internal-id" not in reply
    assert "baidu" not in reply
    assert "price" not in reply
    assert "票价" not in reply
    assert "预订" not in reply
    assert "booking" not in reply


def test_unavailable_result_returns_safe_message_without_warning_code() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result(
            [],
            status="unavailable",
            warning="BAIDU_ATTRACTION_NOT_CONFIGURED",
            total=None,
        )
    )

    assert "暂不可用" in reply
    assert "BAIDU_ATTRACTION_NOT_CONFIGURED" not in reply


def test_empty_success_is_not_rendered_as_unavailable() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(_result([], status="success", total=None))

    assert "未找到" in reply
    assert "暂不可用" not in reply


def test_provider_order_is_preserved() -> None:
    renderer = _renderer_type()()

    reply = renderer.render(
        _result(
            [
                _summary("A", rating=3),
                _summary("B", rating=5),
                _summary("C", rating=4),
            ],
            total=3,
        )
    )

    assert reply.index("A") < reply.index("B") < reply.index("C")
