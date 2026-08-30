"""Application-level ranking explanation for train search results."""

from __future__ import annotations

from typing import Protocol

from app.trains.models import TrainOption, TrainRecommendation, TrainSearchResult


class TrainCandidateRecommender(Protocol):
    def recommend(self, query, candidates: tuple[TrainOption, ...], user_message: str) -> TrainRecommendation: ...


class TrainRecommendationService:
    """Keep selection bounded and render only facts from validated TrainOption values."""

    def __init__(self, recommender: TrainCandidateRecommender | None = None) -> None:
        self._recommender = recommender

    def recommend(self, result: TrainSearchResult, user_message: str = "") -> TrainSearchResult:
        candidates = tuple(result.recommendation_candidates)
        if result.status != "success" or not candidates:
            return result
        recommendation = None
        if self._recommender is not None:
            try:
                candidate_recommendation = self._recommender.recommend(result.query, candidates, user_message)
                if candidate_recommendation.selected_option_id in {item.option_id for item in candidates}:
                    recommendation = candidate_recommendation
            except Exception:
                recommendation = None
        recommendation = recommendation or TrainRecommendation(
            selected_option_id=candidates[0].option_id,
            reason_codes=self._reason_codes(result),
        )
        return result.model_copy(update={"recommendation": recommendation})

    @staticmethod
    def _reason_codes(result: TrainSearchResult) -> list[str]:
        query = result.query
        reasons: list[str] = []
        if query.departure_time_range is not None:
            reasons.append("time_fit")
        if query.preference == "fastest":
            reasons.append("shorter_duration")
        elif query.preference == "cheapest":
            reasons.append("lower_price")
        elif query.preference == "earliest_arrival":
            reasons.append("earlier_arrival")
        if query.seat_type is not None and any(
            seat.seat_name == query.seat_type and seat.availability == "available"
            for option in result.recommendation_candidates for seat in option.seats
        ):
            reasons.append("seat_available")
        return reasons[:5]

    @staticmethod
    def render_reply(result: TrainSearchResult) -> str:
        if result.status != "success" or not result.options:
            return result.warning or "暂时无法查询车次，请稍后重试。"
        selected_id = result.recommendation.selected_option_id if result.recommendation else result.options[0].option_id
        selected = next((item for item in result.options if item.option_id == selected_id), result.options[0])
        reply = ["已查询到以下直达车次，优先推荐：", TrainRecommendationService._format_option(selected, result.query.seat_type)]
        backups = [item for item in result.recommendation_candidates if item.option_id != selected.option_id][:3]
        if backups:
            reply.append("备选：" + "；".join(TrainRecommendationService._format_option(item, result.query.seat_type, compact=True) for item in backups))
        reply.append("票价和余票会变化，请以查询时结果及官方渠道为准。")
        return "\n".join(reply)[:4000]

    @staticmethod
    def _format_option(option: TrainOption, seat_type: str | None, compact: bool = False) -> str:
        seat = next((item for item in option.seats if item.seat_name == seat_type), None) if seat_type else None
        seat = seat or (option.seats[0] if option.seats else None)
        price = f"¥{seat.price_cny:g}" if seat and seat.price_cny is not None else "票价待确认"
        remaining = seat.remaining_label if seat and seat.remaining_label else "余票待确认"
        departure = option.departure_at.strftime("%H:%M")
        arrival = option.arrival_at.strftime("%H:%M")
        duration = f"{option.duration_minutes // 60:02d}:{option.duration_minutes % 60:02d}" if option.duration_minutes is not None else "耗时待确认"
        if compact:
            return f"{option.train_no} {departure}-{arrival}，{price}，{remaining}"
        return f"{option.train_no}：{departure} {option.departure_station}→{option.arrival_station} {arrival}，耗时 {duration}，{seat.seat_name if seat else seat_type or '席别待确认'} {price}（{remaining}）"
