"""Safe user-facing rendering for nearby hotel application results."""

from __future__ import annotations

from app.application.hotel_nearby import HotelNearbyApplicationResult


class HotelNearbyReplyRenderer:
    """Render a bounded, provider-independent nearby-hotel reply."""

    _MAX_ITEMS = 3

    def render(self, result: HotelNearbyApplicationResult, *, radius: int) -> str:
        location_name = result.location.name
        radius_text = _format_distance(radius)
        hotels = result.hotels

        if hotels.status == "unavailable":
            return f"“{location_name}”附近 {radius_text}内的酒店信息暂不可用。"
        if not hotels.items:
            return f"未找到“{location_name}”附近 {radius_text}内的酒店。"

        total_count = hotels.total if hotels.total is not None else len(hotels.items)
        display_count = min(len(hotels.items), self._MAX_ITEMS)
        count_text = (
            f"找到 {total_count} 家酒店，先为你展示前 {display_count} 家"
            if total_count > display_count
            else f"找到 {total_count} 家酒店"
        )
        lines = [f"“{location_name}”附近 {radius_text}内{count_text}："]
        for index, hotel in enumerate(hotels.items[: self._MAX_ITEMS], start=1):
            details = [f"{index}. {hotel.name}"]
            if hotel.distance is not None:
                details.append(f"距离：{_format_distance(hotel.distance)}")
            lines.append("，".join(details))
            if hotel.address:
                lines.append(f"   地址：{hotel.address}")
        return "\n".join(lines)


def _format_distance(meters: int) -> str:
    if meters >= 1000:
        kilometers = meters / 1000
        return f"{kilometers:g} 公里"
    return f"{meters} 米"
