from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
from threading import RLock
from typing import Callable, Protocol
from uuid import UUID

from app.agent.graph import ChatResult, SafeTravelAgent
from app.agent.intent import Intent
from app.core.errors import AppError
from app.core.logging import operational_context
from app.core.usage import ProviderUnavailable, UsageGuard, model_usage_scope
from app.schemas import Itinerary, TravelProfile
from app.trips.models import Trip


class TripOperations(Protocol):
    def create_trip(self, user_id: UUID, profile: TravelProfile) -> Trip: ...
    def get_trip(self, user_id: UUID, trip_id: UUID) -> Trip: ...
    def update_trip(self, user_id: UUID, trip_id: UUID, **changes: object) -> Trip: ...
    def append_message(
        self, user_id: UUID, trip_id: UUID, *, role: str, content: str
    ) -> None: ...
    def persist_planned_chat(
        self,
        user_id: UUID,
        trip: Trip | None,
        profile: TravelProfile,
        itinerary: Itinerary,
        user_message: str,
        assistant_message: str,
    ) -> Trip: ...


@dataclass(frozen=True)
class PendingConfirmation:
    profile: TravelProfile
    user_message: str
    intent: Intent = "plan_trip"


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
        intent: Intent = "plan_trip",
    ) -> None:
        key = self._key(subject, thread_id, trip_id)
        value = PendingConfirmation(
            TravelProfile.model_validate(profile.model_dump()), user_message, intent
        )
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def discard(self, subject: str, thread_id: str, trip_id: UUID | None) -> None:
        key = self._key(subject, thread_id, trip_id)
        with self._lock:
            self._entries.pop(key, None)

    def take(
        self, subject: str, thread_id: str, trip_id: UUID | None
    ) -> PendingConfirmation | None:
        """Atomically claim a pending confirmation so it cannot be replayed."""
        key = self._key(subject, thread_id, trip_id)
        with self._lock:
            return self._entries.pop(key, None)

    def restore_if_absent(
        self,
        subject: str,
        thread_id: str,
        trip_id: UUID | None,
        pending: PendingConfirmation,
    ) -> bool:
        """Restore a failed claim without overwriting newer collected details."""
        key = self._key(subject, thread_id, trip_id)
        with self._lock:
            if key in self._entries:
                return False
            self._entries[key] = PendingConfirmation(
                TravelProfile.model_validate(pending.profile.model_dump()),
                pending.user_message,
                pending.intent,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return True

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
        if result.profile and result.stage != "planned":
            self._confirmation_store.put(
                subject,
                thread_id,
                trip_id,
                TravelProfile.model_validate(result.profile),
                message,
                result.intent or "plan_trip",
            )
        elif result.stage == "planned":
            self._confirmation_store.discard(subject, thread_id, trip_id)
        result.trip_id = trip_id
        return result

    def confirm(
        self,
        *,
        user_id: UUID | None,
        subject: str,
        quota_subject: str | None = None,
        thread_id: str,
        trip_id: UUID | None,
        message: str,
    ) -> ChatResult:
        trip = self._load_trip(user_id, trip_id)
        pending = self._confirmation_store.take(subject, thread_id, trip_id)
        if pending is None:
            raise AppError("CONFIRMATION_REQUIRED", "Collect and confirm trip details first")

        def restore_pending() -> None:
            self._confirmation_store.restore_if_absent(
                subject, thread_id, trip_id, pending
            )

        def attach_pending_intent(error: Exception) -> None:
            """Carry the claimed business intent through API fallback logging."""
            try:
                error.intent = pending.intent  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass

        try:
            reservation = self._usage_guard.reserve(quota_subject or subject)
        except Exception as exc:
            attach_pending_intent(exc)
            restore_pending()
            raise
        usage = None
        try:
            with model_usage_scope() as usage:
                result = self._agent_factory(pending.profile).plan_confirmed(
                    pending.profile,
                    trip,
                    user_id,
                    pending.user_message,
                    intent=pending.intent,
                )
        except Exception as exc:
            attach_pending_intent(exc)
            try:
                if usage is not None and usage.calls:
                    self._commit_usage(reservation, usage)
                else:
                    reservation.rollback()
            finally:
                restore_pending()
            raise
        if result.stage != "planned" or result.itinerary is None:
            try:
                if usage.calls:
                    self._commit_usage(reservation, usage)
                else:
                    reservation.rollback()
            finally:
                restore_pending()
            return result
        if user_id is not None:
            if self._trip_service is None:
                error = RuntimeError("authenticated chat requires trip persistence")
                attach_pending_intent(error)
                try:
                    reservation.rollback()
                finally:
                    restore_pending()
                raise error
            try:
                trip = self._trip_service.persist_planned_chat(
                    user_id,
                    trip,
                    pending.profile,
                    result.itinerary,
                    pending.user_message,
                    result.reply,
                )
            except Exception as exc:
                attach_pending_intent(exc)
                try:
                    if usage.calls:
                        self._commit_usage(reservation, usage)
                    else:
                        reservation.rollback()
                finally:
                    restore_pending()
                raise
            result.trip_id = trip.id
            result.persisted_this_request = True
        self._commit_usage(reservation, usage)
        return result

    @staticmethod
    def _commit_usage(reservation: object, usage: object) -> None:
        estimate = getattr(reservation, "estimate_cost_micros", lambda *_: 0)(
            usage.input_tokens, usage.output_tokens
        )
        try:
            reservation.commit(
                usage.input_tokens,
                usage.output_tokens,
                usage.calls,
            )
        except Exception as exc:
            logging.getLogger("app.model").error(
                "model_usage_commit_failed",
                extra=operational_context(
                    model_calls=usage.calls,
                    model_input_tokens=usage.input_tokens,
                    model_output_tokens=usage.output_tokens,
                    estimated_cost_micros=estimate,
                    error_code="USAGE_ACCOUNTING_UNAVAILABLE",
                    exception_type=type(exc).__name__,
                ),
            )
            return
        logging.getLogger("app.model").info(
            "model_usage",
            extra=operational_context(
                model_calls=usage.calls,
                model_input_tokens=usage.input_tokens,
                model_output_tokens=usage.output_tokens,
                estimated_cost_micros=estimate,
                cost_estimate_configured=estimate > 0,
            ),
        )

    def _load_trip(self, user_id: UUID | None, trip_id: UUID | None) -> Trip | None:
        if trip_id is None:
            return None
        if user_id is None or self._trip_service is None:
            raise AppError("AUTH_REQUIRED", "Authentication is required for saved trips")
        return self._trip_service.get_trip(user_id, trip_id)
