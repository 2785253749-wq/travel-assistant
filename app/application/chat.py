from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Protocol
from uuid import UUID

from app.agent.graph import ChatResult, SafeTravelAgent
from app.core.errors import AppError
from app.core.usage import ProviderUnavailable, UsageGuard, model_usage_scope
from app.schemas import TravelProfile
from app.trips.models import Trip


class TripOperations(Protocol):
    def create_trip(self, user_id: UUID, profile: TravelProfile) -> Trip: ...
    def get_trip(self, user_id: UUID, trip_id: UUID) -> Trip: ...
    def update_trip(self, user_id: UUID, trip_id: UUID, **changes: object) -> Trip: ...
    def append_message(
        self, user_id: UUID, trip_id: UUID, *, role: str, content: str
    ) -> None: ...


@dataclass(frozen=True)
class PendingConfirmation:
    profile: TravelProfile
    user_message: str


class ConfirmationStore:
    """Bounded server-held profile state; raw conversation history is not retained."""

    def __init__(self, max_entries: int = 500) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str, str], PendingConfirmation] = OrderedDict()
        self._lock = RLock()

    def get(self, subject: str, thread_id: str, trip_id: UUID | None) -> PendingConfirmation | None:
        key = self._key(subject, thread_id, trip_id)
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(
        self,
        subject: str,
        thread_id: str,
        trip_id: UUID | None,
        profile: TravelProfile,
        user_message: str,
    ) -> None:
        key = self._key(subject, thread_id, trip_id)
        value = PendingConfirmation(
            TravelProfile.model_validate(profile.model_dump()), user_message
        )
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    @staticmethod
    def _key(subject: str, thread_id: str, trip_id: UUID | None) -> tuple[str, str, str]:
        return subject, thread_id, str(trip_id) if trip_id else "new"


class TravelChatApplication:
    """Collection and confirmed planning behind one small application interface."""

    def __init__(
        self,
        *,
        agent_factory: Callable[[TravelProfile], SafeTravelAgent],
        usage_guard: UsageGuard,
        confirmation_store: ConfirmationStore,
        trip_service: TripOperations | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._usage_guard = usage_guard
        self._confirmation_store = confirmation_store
        self._trip_service = trip_service

    def collect(
        self,
        *,
        user_id: UUID | None,
        subject: str,
        thread_id: str,
        trip_id: UUID | None,
        message: str,
    ) -> ChatResult:
        trip = self._load_trip(user_id, trip_id)
        previous = self._confirmation_store.get(subject, thread_id, trip_id)
        initial = previous.profile if previous is not None else (trip.profile if trip else TravelProfile())
        result = self._agent_factory(initial).collect(message, trip)
        if result.profile:
            self._confirmation_store.put(
                subject,
                thread_id,
                trip_id,
                TravelProfile.model_validate(result.profile),
                message,
            )
        result.trip_id = trip_id
        return result

    def confirm(
        self,
        *,
        user_id: UUID | None,
        subject: str,
        thread_id: str,
        trip_id: UUID | None,
        message: str,
    ) -> ChatResult:
        trip = self._load_trip(user_id, trip_id)
        pending = self._confirmation_store.get(subject, thread_id, trip_id)
        if pending is None:
            if trip is None:
                raise AppError("CONFIRMATION_REQUIRED", "Collect and confirm trip details first")
            pending = PendingConfirmation(trip.profile, message)

        reservation = self._usage_guard.reserve(subject)
        try:
            with model_usage_scope() as usage:
                result = self._agent_factory(pending.profile).plan_confirmed(
                    pending.profile, trip, user_id, pending.user_message
                )
        except Exception:
            reservation.rollback()
            raise
        if result.stage != "planned" or result.itinerary is None:
            reservation.rollback()
            return result
        reservation.commit(max(usage.input_tokens, usage.calls), usage.output_tokens)

        if user_id is not None:
            if self._trip_service is None:
                raise RuntimeError("authenticated chat requires trip persistence")
            if trip is None:
                trip = self._trip_service.create_trip(user_id, pending.profile)
            trip = self._trip_service.update_trip(
                user_id,
                trip.id,
                profile=pending.profile,
                status="planned",
                itinerary=result.itinerary.model_dump(mode="json"),
            )
            self._trip_service.append_message(
                user_id, trip.id, role="user", content=pending.user_message
            )
            self._trip_service.append_message(
                user_id, trip.id, role="assistant", content=result.reply
            )
            result.trip_id = trip.id
        return result

    def _load_trip(self, user_id: UUID | None, trip_id: UUID | None) -> Trip | None:
        if trip_id is None:
            return None
        if user_id is None or self._trip_service is None:
            raise AppError("AUTH_REQUIRED", "Authentication is required for saved trips")
        return self._trip_service.get_trip(user_id, trip_id)
