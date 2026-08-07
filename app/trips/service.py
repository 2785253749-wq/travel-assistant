import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AppError
from app.schemas import Itinerary, TravelProfile
from app.trips.models import (
    ConversationMessage,
    ShareLink,
    Trip,
    copied_trip_title,
    destination_trip_title,
    validate_trip_title,
)
from app.trips.repository import PublicShareRepository, TripRepository


class TripService:
    def __init__(self, repository: TripRepository, public_repository: PublicShareRepository | None = None) -> None:
        self._repository = repository
        self._public_repository = public_repository or repository

    def create_trip(self, user_id: UUID, profile: TravelProfile) -> Trip:
        return self._repository.create(
            Trip(
                user_id=user_id,
                title=destination_trip_title(profile.destination),
                profile=profile,
            )
        )

    def get_trip(self, user_id: UUID, trip_id: UUID) -> Trip:
        trip = self._repository.get(user_id, trip_id)
        if trip is None:
            raise AppError("TRIP_NOT_FOUND", "Trip not found")
        return trip

    def list_trips(self, user_id: UUID) -> list[Trip]:
        return self._repository.list_for_user(user_id)

    def update_trip(
        self,
        user_id: UUID,
        trip_id: UUID,
        *,
        title: str | None = None,
        profile: TravelProfile | None = None,
        status: str | None = None,
        itinerary: Itinerary | None = None,
    ) -> Trip:
        trip = self.get_trip(user_id, trip_id)
        next_profile = (
            TravelProfile.model_validate(profile.model_dump(mode="json"))
            if profile is not None
            else trip.profile
        )
        next_itinerary = (
            Itinerary.model_validate(itinerary.model_dump(mode="json"))
            if itinerary is not None
            else trip.itinerary
        )
        next_status = status or trip.status
        if next_status not in {"collecting", "planned"}:
            raise AppError("TRIP_INVALID", "Invalid trip status")
        if (next_status == "planned") != (next_itinerary is not None):
            raise AppError("TRIP_INVALID", "Trip status and itinerary do not match")
        if title is not None:
            trip.title = validate_trip_title(title)
        trip.profile = next_profile
        trip.status = next_status
        trip.itinerary = next_itinerary
        updated = self._repository.update(user_id, trip_id, trip)
        if updated is None:
            raise AppError("TRIP_NOT_FOUND", "Trip not found")
        return updated

    def copy_trip(self, user_id: UUID, trip_id: UUID) -> Trip:
        source = self.get_trip(user_id, trip_id)
        profile = TravelProfile.model_validate(source.profile.model_dump(mode="json"))
        itinerary = (
            Itinerary.model_validate(source.itinerary.model_dump(mode="json"))
            if source.itinerary is not None
            else None
        )
        copied = Trip(
            user_id=user_id,
            title=copied_trip_title(source.title),
            profile=profile,
            status=source.status,
            itinerary=itinerary,
        )
        return self._repository.create(copied)

    def delete_trip(self, user_id: UUID, trip_id: UUID) -> None:
        if not self._repository.delete(user_id, trip_id):
            raise AppError("TRIP_NOT_FOUND", "Trip not found")

    def append_message(self, user_id: UUID, trip_id: UUID, *, role: str, content: str) -> None:
        self.get_trip(user_id, trip_id)
        if role not in {"user", "assistant"}:
            raise AppError("MESSAGE_INVALID", "Invalid message role")
        self._repository.append_message(ConversationMessage(user_id=user_id, trip_id=trip_id, role=role, content=content))

    def create_share_link(self, user_id: UUID, trip_id: UUID, expires_in_days: int = 30) -> str:
        self.get_trip(user_id, trip_id)
        if not 1 <= expires_in_days <= 365:
            raise AppError("SHARE_INVALID", "Share expiry must be between 1 and 365 days")
        token = secrets.token_urlsafe(32)
        self._repository.create_share_link(ShareLink(user_id=user_id, trip_id=trip_id, token_hash=self._token_hash(token), expires_at=datetime.now(UTC) + timedelta(days=expires_in_days)))
        return token

    def revoke_share_link(self, user_id: UUID, trip_id: UUID) -> None:
        self.get_trip(user_id, trip_id)
        self._repository.revoke_share_links(user_id, trip_id)

    def get_shared_trip(self, token: str) -> dict:
        trip = self._public_repository.get_shared_trip(self._token_hash(token))
        if trip is None:
            raise AppError("SHARE_NOT_FOUND", "Shared trip not found")
        try:
            profile = TravelProfile.model_validate(trip["profile"])
            itinerary = (
                Itinerary.model_validate(trip["itinerary"])
                if trip.get("itinerary") is not None
                else None
            )
            status = trip["status"]
            if status not in {"collecting", "planned"}:
                raise ValueError("invalid status")
            if (status == "planned") != (itinerary is not None):
                raise ValueError("inconsistent public trip")
            trip_id = str(UUID(str(trip["id"])))
            title = validate_trip_title(trip["title"])
            updated_at = trip.get("updated_at")
            if updated_at is not None and not isinstance(updated_at, str):
                raise ValueError("invalid timestamp")
        except (KeyError, TypeError, ValueError, ValidationError):
            raise AppError("SHARE_NOT_FOUND", "Shared trip not found") from None
        return {
            "id": trip_id,
            "title": title,
            "status": status,
            "profile": profile.model_dump(mode="json"),
            "itinerary": (
                itinerary.model_dump(mode="json") if itinerary is not None else None
            ),
            "updated_at": updated_at,
        }

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
