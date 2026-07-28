import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

from app.core.errors import AppError
from app.api.auth import AuthenticatedUser, CurrentUser
from app.core.config import get_settings
from app.infrastructure.repositories import (
    InMemoryTripRepository,
    create_public_share_repository,
    create_user_scoped_supabase_repository,
)
from app.schemas import TravelProfile
from app.trips.models import ConversationMessage, ShareLink, Trip
from app.trips.repository import PublicShareRepository, TripRepository


class TripService:
    def __init__(self, repository: TripRepository, public_repository: PublicShareRepository | None = None) -> None:
        self._repository = repository
        self._public_repository = public_repository or repository

    def create_trip(self, user_id: UUID, profile: TravelProfile) -> Trip:
        destination = profile.destination or "New"
        return self._repository.create(Trip(user_id=user_id, title=f"{destination} trip", profile=profile))

    def get_trip(self, user_id: UUID, trip_id: UUID) -> Trip:
        trip = self._repository.get(user_id, trip_id)
        if trip is None:
            raise AppError("TRIP_NOT_FOUND", "Trip not found")
        return trip

    def list_trips(self, user_id: UUID) -> list[Trip]:
        return self._repository.list_for_user(user_id)

    def update_trip(self, user_id: UUID, trip_id: UUID, *, title: str | None = None, profile: TravelProfile | None = None, status: str | None = None, itinerary: dict | None = None) -> Trip:
        trip = self.get_trip(user_id, trip_id)
        if title is not None:
            trip.title = title
        if profile is not None:
            trip.profile = profile
        if status is not None:
            if status not in {"collecting", "planned"}:
                raise AppError("TRIP_INVALID", "Invalid trip status")
            trip.status = status
        if itinerary is not None:
            trip.itinerary = itinerary
        updated = self._repository.update(user_id, trip_id, trip)
        if updated is None:
            raise AppError("TRIP_NOT_FOUND", "Trip not found")
        return updated

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
        return trip

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _uses_supabase() -> bool:
    settings = get_settings()
    return settings.app_env == "production" or (
        settings.supabase_url is not None and settings.supabase_anon_key is not None
    )


@lru_cache(maxsize=1)
def get_development_repository() -> InMemoryTripRepository:
    """Share one credential-free store across local private and public dependencies."""
    return InMemoryTripRepository()


def get_trip_service(user: CurrentUser) -> TripService:
    if _uses_supabase():
        if not user.access_token:
            raise RuntimeError("A verified bearer token is required for Supabase trip access")
        return TripService(create_user_scoped_supabase_repository(user.access_token))
    return TripService(get_development_repository())


def get_public_trip_service() -> TripService:
    if _uses_supabase():
        return TripService(InMemoryTripRepository(), create_public_share_repository())
    return TripService(get_development_repository())
