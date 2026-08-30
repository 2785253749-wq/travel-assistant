from datetime import date, datetime, timedelta, timezone

from app.application.train import TrainRecommendationService
from app.trains.models import TrainOption, TrainQuery, TrainRecommendation, TrainSearchResult


_CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _option(train_no: str, departure_hour: int, price: int) -> TrainOption:
    return TrainOption(
        option_id=f"{train_no}-2026-09-10",
        train_no=train_no,
        departure_station="福州",
        arrival_station="上海",
        departure_at=datetime(2026, 9, 10, departure_hour, tzinfo=_CHINA_TIMEZONE),
        arrival_at=datetime(2026, 9, 10, departure_hour + 4, tzinfo=_CHINA_TIMEZONE),
        duration_minutes=240,
        bookable=True,
        seats=[{"seat_name": "二等座", "price_cny": price, "remaining_label": "有", "availability": "available"}],
    )


def _result(options: list[TrainOption]) -> TrainSearchResult:
    return TrainSearchResult(
        query=TrainQuery(
            departure_station="福州", arrival_station="上海", travel_date=date(2026, 9, 10), seat_type="二等座"
        ),
        options=options,
        recommendation_candidates=options[:3],
        fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        source="https://www.juhe.cn/docs/api/id/817",
        status="success",
    )


class StubRecommender:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def recommend(self, query, candidates, user_message):
        self.calls.append((query, tuple(candidates), user_message))
        if self.error:
            raise self.error
        return self.response


def test_recommendation_service_limits_model_input_to_candidates_and_accepts_valid_id() -> None:
    result = _result([_option("G1", 8, 320), _option("G2", 9, 280), _option("G3", 10, 300)])
    recommender = StubRecommender(TrainRecommendation(selected_option_id="G2-2026-09-10", reason_codes=["lower_price"]))

    updated = TrainRecommendationService(recommender=recommender).recommend(result, "尽量便宜")

    assert [item.train_no for item in recommender.calls[0][1]] == ["G1", "G2", "G3"]
    assert updated.recommendation.selected_option_id == "G2-2026-09-10"


def test_recommendation_service_falls_back_for_unknown_model_id() -> None:
    result = _result([_option("G1", 8, 320), _option("G2", 9, 280)])
    recommender = StubRecommender(TrainRecommendation(selected_option_id="FAKE", reason_codes=["lower_price"]))

    updated = TrainRecommendationService(recommender=recommender).recommend(result, "尽量便宜")

    assert updated.recommendation.selected_option_id == "G1-2026-09-10"


def test_recommendation_service_falls_back_when_model_fails() -> None:
    result = _result([_option("G1", 8, 320), _option("G2", 9, 280)])

    updated = TrainRecommendationService(recommender=StubRecommender(error=RuntimeError("model unavailable"))).recommend(result, "尽量便宜")

    assert updated.recommendation.selected_option_id == "G1-2026-09-10"


def test_recommendation_reply_uses_selected_option_facts_not_model_text() -> None:
    result = _result([_option("G1", 8, 320), _option("G2", 9, 280)])
    recommender = StubRecommender(TrainRecommendation(selected_option_id="G2-2026-09-10", reason_codes=["lower_price"]))

    updated = TrainRecommendationService(recommender=recommender).recommend(result, "尽量便宜")
    reply = TrainRecommendationService.render_reply(updated)

    assert "G2" in reply
    assert "¥280" in reply
    assert "G1" in reply
    assert "票价和余票会变化" in reply
    assert "FAKE" not in reply
