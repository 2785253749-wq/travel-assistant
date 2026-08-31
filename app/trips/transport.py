"""Validated transport facts for a confirmed trip."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
from time import monotonic
from typing import Any, Literal

from pydantic import Field

from app.application.train import TrainRecommendationService
from app.core.logging import operational_context
from app.schemas import TripTransportLegSummary, TripTransportSummary
from app.trains.models import TrainOption, TrainQuery, TrainSchema, TrainSearchResult


class TripTransportContext(TrainSchema):
    """The selected train facts shared by planning, budgeting, and responses."""

    seat_type: Literal["二等座", "一等座", "商务座"] = "二等座"
    outbound_requested: bool = False
    return_requested: bool = False
    outbound: TrainOption | None = None
    return_option: TrainOption | None = None
    outbound_source: str | None = None
    outbound_fetched_at: datetime | None = None
    return_source: str | None = None
    return_fetched_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list, max_length=20)


class TripTransportResolver:
    """Query each confirmed trip leg at most once and select existing rankings."""

    _FALLBACK_WARNING = "实时车次暂时无法确认，本次交通时间和费用为规划参考。"
    _LEG_FAILURE_WARNINGS = {
        "去程": "去程实时车次暂时无法确认，请后续核对官方渠道。",
        "返程": "返程实时车次暂时无法确认，请后续核对官方渠道。",
    }

    def __init__(
        self,
        train_service: Any,
        recommender: TrainRecommendationService | None = None,
    ) -> None:
        self._train_service = train_service
        self._recommender = recommender or TrainRecommendationService()

    def resolve(self, profile: Any, user_message: str = "") -> TripTransportContext:
        transport_started = monotonic()
        origin = (profile.origin or "").strip()
        destination = (profile.destination or "").strip()
        if not origin or not destination or not profile.start_date or origin == destination:
            return TripTransportContext()
        requested_seat = getattr(profile, "train_seat", None) or "二等座"
        try:
            start_date = date.fromisoformat(profile.start_date)
        except ValueError:
            return TripTransportContext()

        outbound_requested = True
        warnings: list[str] = []
        outbound, outbound_source, outbound_fetched_at = self._resolve_leg(
            departure=origin,
            arrival=destination,
            travel_date=start_date,
            direction="去程",
            seat_type=requested_seat,
            user_message=user_message,
            warnings=warnings,
        )
        return_option = None
        return_source = None
        return_fetched_at = None
        return_requested = False
        if profile.end_date:
            try:
                end_date = date.fromisoformat(profile.end_date)
            except ValueError:
                end_date = None
            if end_date is not None:
                return_requested = True
                return_option, return_source, return_fetched_at = self._resolve_leg(
                    departure=destination,
                    arrival=origin,
                    travel_date=end_date,
                    direction="返程",
                    seat_type=requested_seat,
                    user_message=user_message,
                    warnings=warnings,
                )

        logging.getLogger("app.transport").info(
            "transport total",
            extra=operational_context(
                elapsed_seconds=round(monotonic() - transport_started, 3),
            ),
        )

        for label, option in (("去程", outbound), ("返程", return_option)):
            if option is None:
                continue
            if selected_seat_price(option, requested_seat) is None:
                warnings.append(
                    f"推荐车次的{requested_seat}实时票价暂未获取，本次交通费用仍包含估算部分。"
                )
            if not has_confirmed_availability(option, requested_seat):
                warnings.append(f"{label}{requested_seat}余票状态未确认，请以官方渠道为准。")
        return TripTransportContext(
            seat_type=requested_seat,
            outbound_requested=outbound_requested,
            return_requested=return_requested,
            outbound=outbound,
            return_option=return_option,
            outbound_source=outbound_source,
            outbound_fetched_at=outbound_fetched_at,
            return_source=return_source,
            return_fetched_at=return_fetched_at,
            warnings=_unique(warnings),
        )

    def _resolve_leg(
        self,
        *,
        departure: str,
        arrival: str,
        travel_date: date,
        direction: str,
        seat_type: str,
        user_message: str,
        warnings: list[str],
    ) -> tuple[TrainOption | None, str | None, datetime | None]:
        started = monotonic()
        query = TrainQuery(
            departure_station=departure,
            arrival_station=arrival,
            travel_date=travel_date,
            seat_type=seat_type,
        )
        try:
            result = self._train_service.search(query)
        except Exception as exc:
            logging.getLogger("app.transport").warning(
                f"{direction} train search",
                extra=operational_context(
                    train_status="exception",
                    error_code=getattr(exc, "code", type(exc).__name__),
                    elapsed_seconds=round(monotonic() - started, 3),
                    direction=direction,
                ),
            )
            warnings.append(self._LEG_FAILURE_WARNINGS[direction])
            return None, None, None
        logging.getLogger("app.transport").info(
            f"{direction} train search",
            extra=operational_context(
                train_status=result.status if isinstance(result, TrainSearchResult) else "invalid",
                elapsed_seconds=round(monotonic() - started, 3),
                direction=direction,
            ),
        )
        if not isinstance(result, TrainSearchResult) or result.status != "success" or not result.options:
            warnings.append(self._LEG_FAILURE_WARNINGS[direction])
            return None, None, None
        selected_result = self._recommender.recommend(result, user_message)
        selected_id = (
            selected_result.recommendation.selected_option_id
            if selected_result.recommendation is not None
            else selected_result.options[0].option_id
        )
        selected = next(
            (option for option in selected_result.options if option.option_id == selected_id),
            selected_result.options[0],
        )
        return selected, result.source, result.fetched_at


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def selected_seat(option: TrainOption | None, seat_type: str = "二等座"):
    if option is None:
        return None
    return next((seat for seat in option.seats if seat.seat_name == seat_type), None)


def selected_seat_price(option: TrainOption | None, seat_type: str = "二等座") -> Decimal | None:
    seat = selected_seat(option, seat_type)
    return seat.price_cny if seat is not None else None


def second_class_price(option: TrainOption | None) -> Decimal | None:
    return selected_seat_price(option, "二等座")


def has_confirmed_availability(option: TrainOption, seat_type: str = "二等座") -> bool:
    seat = selected_seat(option, seat_type)
    return seat is not None and seat.availability == "available"


def public_transport_summary(context: TripTransportContext) -> TripTransportSummary:
    """Convert selected train facts into the narrow public ChatResponse shape."""
    outbound = _public_leg(
        context.outbound,
        context.seat_type,
        context.outbound_source,
        context.outbound_fetched_at,
    )
    return_trip = _public_leg(
        context.return_option,
        context.seat_type,
        context.return_source,
        context.return_fetched_at,
    )
    requested_prices = []
    if getattr(context, "outbound_requested", context.outbound is not None):
        requested_prices.append(outbound.price if outbound is not None else None)
    if getattr(context, "return_requested", context.return_option is not None):
        requested_prices.append(return_trip.price if return_trip is not None else None)
    if not requested_prices or all(price is None for price in requested_prices):
        pricing_status = "estimated"
    elif all(price is not None for price in requested_prices):
        pricing_status = "live"
    else:
        pricing_status = "partial"
    return TripTransportSummary(
        outbound=outbound,
        return_trip=return_trip,
        pricing_status=pricing_status,
        warnings=list(context.warnings),
    )


def _public_leg(
    option: TrainOption | None,
    seat_type: str,
    source: str | None,
    fetched_at: datetime | None,
) -> TripTransportLegSummary | None:
    if option is None:
        return None
    seat = selected_seat(option, seat_type)
    return TripTransportLegSummary(
        train_no=option.train_no,
        origin_station=option.departure_station,
        destination_station=option.arrival_station,
        departure_at=option.departure_at,
        arrival_at=option.arrival_at,
        duration=option.duration_minutes,
        seat_name=seat.seat_name if seat is not None else seat_type,
        price=seat.price_cny if seat is not None else None,
        remaining_label=seat.remaining_label if seat is not None else None,
        availability=seat.availability if seat is not None else "unknown",
        source=source,
        fetched_at=fetched_at,
    )


TRANSPORT_FALLBACK_WARNING = TripTransportResolver._FALLBACK_WARNING


def render_transport_summary(context: TripTransportContext) -> str:
    lines = ["## 交通参考"]
    seat_type = getattr(context, "seat_type", "二等座")
    for label, option in (("去程", context.outbound), ("返程", context.return_option)):
        if option is None:
            continue
        seat = selected_seat(option, seat_type)
        price = selected_seat_price(option, seat_type)
        price_text = f"{seat_type} ¥{price:g}" if price is not None else f"{seat_type}票价待确认"
        if seat is None or seat.availability == "unknown":
            availability = f"{seat_type}余票状态待确认"
        elif seat.availability == "available":
            availability = f"当前查询：{seat.remaining_label or '有票'}"
        else:
            availability = "当前查询：无票"
        lines.append(
            f"- {label} {option.train_no}：{option.departure_at.astimezone(_CHINA_TIMEZONE).strftime('%H:%M')} "
            f"{option.departure_station} → {option.arrival_at.astimezone(_CHINA_TIMEZONE).strftime('%H:%M')} "
            f"{option.arrival_station}，{price_text}，{availability}。"
        )
    for warning in context.warnings:
        lines.append(f"- {warning}")
    return "\n".join(lines)


_CHINA_TIMEZONE = timezone(timedelta(hours=8))
