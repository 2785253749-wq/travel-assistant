from __future__ import annotations

from app.application.attraction_search import AttractionSearchApplicationResult


class AttractionReplyRenderer:
    _MAX_ITEMS = 3

    def render(self, result: AttractionSearchApplicationResult) -> str:
        attractions = result.attractions
        if attractions.status == "unavailable":
            return self._render_unavailable(result)
        if not attractions.items:
            return self._render_empty(result)

        total = attractions.total
        total_count = total if total is not None else len(attractions.items)
        display_count = min(len(attractions.items), self._MAX_ITEMS)
        count_text = f"找到 {total_count} 个景点"
        if total_count > display_count:
            count_text += f"，先为你展示前 {display_count} 个"

        if result.mode == "city":
            heading = f"{result.city}{count_text}："
        else:
            location = result.location_query or result.city
            radius_text = (
                f" {_format_distance(result.radius)}内" if result.radius is not None else ""
            )
            heading = f"“{location}”附近{radius_text}{count_text}："

        lines = [heading]
        for index, attraction in enumerate(
            attractions.items[: self._MAX_ITEMS], start=1
        ):
            lines.append(f"{index}. {attraction.name}")
            if attraction.rating is not None:
                lines.append(f"   评分：{attraction.rating:g}")
            if attraction.comment_num is not None:
                lines.append(f"   评论数：{attraction.comment_num}")
            if result.mode == "nearby" and attraction.distance is not None:
                lines.append(f"   距离：{_format_distance(attraction.distance)}")
            if attraction.address:
                lines.append(f"   地址：{attraction.address}")
            if attraction.tags:
                lines.append(f"   标签：{'、'.join(attraction.tags)}")
        return "\n".join(lines)

    def _render_unavailable(self, result: AttractionSearchApplicationResult) -> str:
        if result.mode == "city":
            return f"{result.city}的景点信息暂不可用。"
        location = result.location_query or result.city
        return f"“{location}”附近的景点信息暂不可用。"

    def _render_empty(self, result: AttractionSearchApplicationResult) -> str:
        if result.mode == "city":
            return f"未找到{result.city}的景点。"
        location = result.location_query or result.city
        return f"未找到“{location}”附近的景点。"


def _format_distance(meters: int) -> str:
    if meters >= 1000:
        return f"{meters / 1000:g} 公里"
    return f"{meters} 米"
