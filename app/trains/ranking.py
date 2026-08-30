from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from app.trains.models import TrainOption, TrainQuery, TrainSeat


def get_seat(option: TrainOption, seat_type: str | None) -> TrainSeat | None:
    if seat_type is None:
        return None
    return next((seat for seat in option.seats if seat.seat_name == seat_type), None)


def get_effective_price(option: TrainOption, seat_type: str | None) -> Decimal | None:
    if seat_type is not None:
        seat = get_seat(option, seat_type)
        return seat.price_cny if seat is not None else None
    prices = [seat.price_cny for seat in option.seats if seat.price_cny is not None]
    return min(prices) if prices else None


def rank_train_options(
    options: Iterable[TrainOption],
    query: TrainQuery,
) -> tuple[TrainOption, ...]:
    values = tuple(options)
    if query.preference == "cheapest":
        return tuple(sorted(values, key=lambda option: _price_key(option, query)))
    if query.preference == "fastest":
        return tuple(sorted(values, key=_duration_key))
    if query.preference == "earliest_arrival":
        return tuple(sorted(values, key=lambda option: option.arrival_at))
    return tuple(sorted(values, key=_default_key))


def _price_key(option: TrainOption, query: TrainQuery) -> tuple[bool, Decimal]:
    price = get_effective_price(option, query.seat_type)
    return (price is None, price if price is not None else Decimal("0"))


def _duration_key(option: TrainOption) -> tuple[bool, int]:
    duration = option.duration_minutes
    return (duration is None, duration if duration is not None else 0)


def _default_key(option: TrainOption) -> tuple[int, object, bool, int, str]:
    has_availability = option.bookable is True or any(
        seat.availability == "available" for seat in option.seats
    )
    duration = option.duration_minutes
    return (
        0 if has_availability else 1,
        option.departure_at,
        duration is None,
        duration if duration is not None else 0,
        option.train_no.strip().casefold(),
    )
