from datetime import UTC, datetime
from uuid import UUID

from app.trips.models import ConversationMessage, ShareLink, Trip
from app.schemas import TravelProfile


class InMemoryTripRepository:
    """Deterministic repository for local use and tests; it enforces ownership too."""

    def __init__(self) -> None:
        self.trips: dict[UUID, Trip] = {}
        self.messages: list[ConversationMessage] = []
        self.share_links: list[ShareLink] = []
        self.last_share_link: ShareLink | None = None

    def create(self, trip: Trip) -> Trip:
        now = datetime.now(UTC)
        trip.created_at = trip.created_at or now
        trip.updated_at = now
        self.trips[trip.id] = trip
        return trip

    def get(self, user_id: UUID, trip_id: UUID) -> Trip | None:
        trip = self.trips.get(trip_id)
        return trip if trip is not None and trip.user_id == user_id else None

    def list_for_user(self, user_id: UUID) -> list[Trip]:
        return sorted(
            (trip for trip in self.trips.values() if trip.user_id == user_id),
            key=lambda trip: trip.updated_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    def update(self, user_id: UUID, trip_id: UUID, trip: Trip) -> Trip | None:
        if self.get(user_id, trip_id) is None or trip.user_id != user_id:
            return None
        trip.updated_at = datetime.now(UTC)
        self.trips[trip_id] = trip
        return trip

    def delete(self, user_id: UUID, trip_id: UUID) -> bool:
        if self.get(user_id, trip_id) is None:
            return False
        del self.trips[trip_id]
        return True

    def append_message(self, message: ConversationMessage) -> None:
        if self.get(message.user_id, message.trip_id) is None:
            return
        message.created_at = message.created_at or datetime.now(UTC)
        self.messages.append(message)

    def create_share_link(self, share_link: ShareLink) -> ShareLink:
        share_link.created_at = share_link.created_at or datetime.now(UTC)
        self.share_links.append(share_link)
        self.last_share_link = share_link
        return share_link

    def revoke_share_links(self, user_id: UUID, trip_id: UUID) -> None:
        now = datetime.now(UTC)
        for share_link in self.share_links:
            if share_link.user_id == user_id and share_link.trip_id == trip_id and share_link.revoked_at is None:
                share_link.revoked_at = now

    def get_share_link(self, token_hash: str) -> ShareLink | None:
        return next((link for link in self.share_links if link.token_hash == token_hash), None)


class SupabaseTripRepository:
    """Supabase adapter with owner filters on every private data operation.

    The injected client is deliberately left to composition code: production can use a
    user-scoped client for RLS, while tests can provide a no-network fake client.
    """

    def __init__(self, client) -> None:
        self._client = client

    def create(self, trip: Trip) -> Trip:
        return self._trip_from_row(self._one(self._client.table("trips").insert(self._trip_row(trip)).execute()))

    def get(self, user_id: UUID, trip_id: UUID) -> Trip | None:
        response = self._client.table("trips").select("*").eq("id", str(trip_id)).eq("user_id", str(user_id)).execute()
        return self._trip_from_row(response.data[0]) if response.data else None

    def list_for_user(self, user_id: UUID) -> list[Trip]:
        response = self._client.table("trips").select("*").eq("user_id", str(user_id)).order("updated_at", desc=True).execute()
        return [self._trip_from_row(row) for row in response.data]

    def update(self, user_id: UUID, trip_id: UUID, trip: Trip) -> Trip | None:
        response = self._client.table("trips").update(self._trip_row(trip, include_id=False)).eq("id", str(trip_id)).eq("user_id", str(user_id)).execute()
        return self._trip_from_row(response.data[0]) if response.data else None

    def delete(self, user_id: UUID, trip_id: UUID) -> bool:
        response = self._client.table("trips").delete().eq("id", str(trip_id)).eq("user_id", str(user_id)).execute()
        return bool(response.data)

    def append_message(self, message: ConversationMessage) -> None:
        self._client.table("conversation_messages").insert({"id": str(message.id), "user_id": str(message.user_id), "trip_id": str(message.trip_id), "role": message.role, "content": message.content}).execute()

    def create_share_link(self, share_link: ShareLink) -> ShareLink:
        row = self._one(self._client.table("share_links").insert({"id": str(share_link.id), "user_id": str(share_link.user_id), "trip_id": str(share_link.trip_id), "token_hash": share_link.token_hash, "expires_at": share_link.expires_at.isoformat()}).execute())
        return self._share_from_row(row)

    def revoke_share_links(self, user_id: UUID, trip_id: UUID) -> None:
        self._client.table("share_links").update({"revoked_at": datetime.now(UTC).isoformat()}).eq("user_id", str(user_id)).eq("trip_id", str(trip_id)).is_("revoked_at", "null").execute()

    def get_share_link(self, token_hash: str) -> ShareLink | None:
        response = self._client.table("share_links").select("*").eq("token_hash", token_hash).execute()
        return self._share_from_row(response.data[0]) if response.data else None

    @staticmethod
    def _one(response):
        return response.data[0]

    @staticmethod
    def _trip_row(trip: Trip, *, include_id: bool = True) -> dict:
        row = {"user_id": str(trip.user_id), "title": trip.title, "status": trip.status, "profile": trip.profile.model_dump(mode="json"), "itinerary": trip.itinerary}
        if include_id:
            row["id"] = str(trip.id)
        return row

    @staticmethod
    def _trip_from_row(row: dict) -> Trip:
        return Trip(id=UUID(row["id"]), user_id=UUID(row["user_id"]), title=row["title"], status=row["status"], profile=TravelProfile.model_validate(row["profile"]), itinerary=row.get("itinerary"), created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None, updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None)

    @staticmethod
    def _share_from_row(row: dict) -> ShareLink:
        return ShareLink(id=UUID(row["id"]), user_id=UUID(row["user_id"]), trip_id=UUID(row["trip_id"]), token_hash=row["token_hash"], expires_at=datetime.fromisoformat(row["expires_at"]), revoked_at=datetime.fromisoformat(row["revoked_at"]) if row.get("revoked_at") else None, created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None)
