from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.community.models import (
    CommunityPage,
    CommunityPost,
    decode_community_cursor,
    encode_community_cursor,
)
from app.community.repositories import (
    InMemoryCommunityRepository,
    SupabaseCommunityRepository,
    SupabasePublicCommunityRepository,
)
from app.core.errors import AppError


class CommunityRepository(Protocol):
    def publish(self, user_id: UUID, trip_id: UUID, summary: str) -> CommunityPost: ...
    def withdraw(self, user_id: UUID, post_id: UUID) -> bool: ...
    def list_owned_post_ids(self, user_id: UUID, post_ids: list[UUID]) -> set[UUID]: ...


class PublicCommunityRepository(Protocol):
    def list_posts(
        self, cursor: tuple[object, UUID] | None, limit: int
    ) -> list[CommunityPost]: ...
    def get_post(self, post_id: UUID) -> CommunityPost | None: ...


class CommunityModule:
    def __init__(
        self,
        repository: CommunityRepository,
        public_repository: PublicCommunityRepository | None = None,
    ) -> None:
        self._repository = repository
        self._public_repository = public_repository or repository

    def list_posts(
        self, cursor: str | None, limit: int, viewer_id: UUID | None = None
    ) -> CommunityPage:
        if not 1 <= limit <= 50:
            raise AppError(
                "COMMUNITY_VALIDATION_FAILED", "Community request validation failed"
            )
        try:
            decoded_cursor = decode_community_cursor(cursor) if cursor is not None else None
        except ValueError as exc:
            raise AppError(
                "COMMUNITY_VALIDATION_FAILED", "Community request validation failed"
            ) from exc

        posts = self._public_repository.list_posts(decoded_cursor, limit + 1)
        visible = posts[:limit]
        next_cursor = None
        if len(posts) > limit and visible:
            last_visible = visible[-1]
            next_cursor = encode_community_cursor(last_visible.created_at, last_visible.id)
        return CommunityPage(
            items=self._decorate_posts(visible, viewer_id),
            next_cursor=next_cursor,
        )

    def get_post(self, post_id: UUID, viewer_id: UUID | None = None) -> CommunityPost:
        post = self._public_repository.get_post(post_id)
        if post is None:
            raise AppError("COMMUNITY_POST_NOT_FOUND", "Community post not found")
        return self._decorate_posts([post], viewer_id)[0]

    def publish(self, user_id: UUID, trip_id: UUID, summary: str) -> CommunityPost:
        post = self._repository.publish(user_id, trip_id, summary)
        return post.model_copy(update={"can_delete": True})

    def withdraw(self, user_id: UUID, post_id: UUID) -> None:
        if not self._repository.withdraw(user_id, post_id):
            raise AppError("COMMUNITY_POST_NOT_FOUND", "Community post not found")

    def _decorate_posts(
        self, posts: list[CommunityPost], viewer_id: UUID | None
    ) -> list[CommunityPost]:
        if viewer_id is None or not posts:
            return [post.model_copy(update={"can_delete": False}) for post in posts]
        owned_ids = self._repository.list_owned_post_ids(
            viewer_id, [post.id for post in posts]
        )
        return [
            post.model_copy(update={"can_delete": post.id in owned_ids}) for post in posts
        ]


__all__ = [
    "CommunityModule",
    "InMemoryCommunityRepository",
    "SupabaseCommunityRepository",
    "SupabasePublicCommunityRepository",
]
