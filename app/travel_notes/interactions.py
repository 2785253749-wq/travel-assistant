from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from app.core.errors import AppError
from app.schemas import StrictSchema
from app.travel_notes.models import (
    ReviewTargetType,
    TravelNoteComment,
    TravelNoteCommentStatus,
)


class TravelNoteInteractionState(StrictSchema):
    note_id: UUID
    liked: bool
    bookmarked: bool
    like_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)


class TravelNoteCommentPage(StrictSchema):
    items: list[TravelNoteComment]
    next_cursor: str | None = None


class TravelNoteCommentInput(StrictSchema):
    body: str = Field(min_length=1, max_length=500)

    @field_validator("body", mode="before")
    @classmethod
    def _trim_body(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TravelNoteReportInput(StrictSchema):
    target_type: ReviewTargetType
    target_id: UUID
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _trim_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TravelNoteReportView(StrictSchema):
    id: UUID
    target_type: ReviewTargetType
    target_id: UUID
    status: str = Field(pattern=r"^(pending|dismissed|actioned)$")


@dataclass(frozen=True, slots=True)
class _StoredComment:
    id: UUID
    note_id: UUID
    author_id: UUID
    author_display_name: str
    body: str
    status: TravelNoteCommentStatus
    published_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _StoredReport:
    id: UUID
    reporter_id: UUID
    target_type: ReviewTargetType
    target_id: UUID
    status: str


class InteractionRepository(Protocol):
    def set_like(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState: ...
    def set_bookmark(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState: ...
    def viewer_state(self, user_id: UUID, note_id: UUID) -> TravelNoteInteractionState: ...
    def submit_comment(self, user_id: UUID, note_id: UUID, body: str) -> TravelNoteComment: ...
    def list_public_comments(
        self, note_id: UUID, cursor: str | None, limit: int, viewer_id: UUID | None = None
    ) -> TravelNoteCommentPage: ...
    def submit_report(
        self,
        reporter_id: UUID,
        note_id: UUID,
        target_type: ReviewTargetType,
        target_id: UUID,
        reason: str,
    ) -> TravelNoteReportView: ...


class InMemoryInteractionRepository:
    def __init__(
        self,
        *,
        approved_note_ids: set[UUID] | None = None,
        approved_comment_ids: set[UUID] | None = None,
        comment_note_ids: dict[UUID, UUID] | None = None,
    ) -> None:
        self._approved_note_ids = set(approved_note_ids or set())
        self._approved_comment_ids = set(approved_comment_ids or set())
        self._comment_note_ids = dict(comment_note_ids or {})
        self._likes: set[tuple[UUID, UUID]] = set()
        self._bookmarks: set[tuple[UUID, UUID]] = set()
        self._comments: dict[UUID, _StoredComment] = {}
        self._reports: dict[tuple[UUID, ReviewTargetType, UUID], _StoredReport] = {}

    def set_like(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState:
        self._require_note(note_id)
        key = (user_id, note_id)
        if enabled:
            self._likes.add(key)
        else:
            self._likes.discard(key)
        return self.viewer_state(user_id, note_id)

    def set_bookmark(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState:
        self._require_note(note_id)
        key = (user_id, note_id)
        if enabled:
            self._bookmarks.add(key)
        else:
            self._bookmarks.discard(key)
        return self.viewer_state(user_id, note_id)

    def viewer_state(self, user_id: UUID, note_id: UUID) -> TravelNoteInteractionState:
        self._require_note(note_id)
        return TravelNoteInteractionState(
            note_id=note_id,
            liked=(user_id, note_id) in self._likes,
            bookmarked=(user_id, note_id) in self._bookmarks,
            like_count=sum(1 for _, candidate in self._likes if candidate == note_id),
            comment_count=sum(
                1
                for comment in self._comments.values()
                if comment.note_id == note_id and comment.status == "approved"
            ),
        )

    def submit_comment(self, user_id: UUID, note_id: UUID, body: str) -> TravelNoteComment:
        self._require_note(note_id)
        now = datetime.now(UTC)
        comment = _StoredComment(
            id=uuid4(),
            note_id=note_id,
            author_id=user_id,
            author_display_name="Voyage 旅行者",
            body=body,
            status="pending_review",
            published_at=None,
            created_at=now,
        )
        self._comments[comment.id] = comment
        self._comment_note_ids[comment.id] = note_id
        return _comment_view(comment)

    def list_public_comments(
        self, note_id: UUID, cursor: str | None, limit: int, viewer_id: UUID | None = None
    ) -> TravelNoteCommentPage:
        del cursor
        self._require_note(note_id)
        comments = [
            comment
            for comment in self._comments.values()
            if comment.note_id == note_id
            and (
                comment.status == "approved"
                or (comment.status == "pending_review" and viewer_id is not None and comment.author_id == viewer_id)
            )
        ]
        comments.sort(key=lambda comment: (comment.published_at or comment.created_at, str(comment.id)), reverse=True)
        return TravelNoteCommentPage(
            items=[_comment_view(comment) for comment in comments[:limit]],
            next_cursor=None,
        )

    def submit_report(
        self,
        reporter_id: UUID,
        note_id: UUID,
        target_type: ReviewTargetType,
        target_id: UUID,
        reason: str,
    ) -> TravelNoteReportView:
        self._require_note(note_id)
        if target_type == "note":
            if target_id != note_id:
                raise _not_found()
        elif self._comment_note_ids.get(target_id) != note_id:
            raise _not_found()
        key = (reporter_id, target_type, target_id)
        report = self._reports.get(key)
        if report is None:
            report = _StoredReport(
                id=uuid4(),
                reporter_id=reporter_id,
                target_type=target_type,
                target_id=target_id,
                status="pending",
            )
            self._reports[key] = report
        del reason
        return TravelNoteReportView(
            id=report.id,
            target_type=report.target_type,
            target_id=report.target_id,
            status=report.status,
        )

    def _require_note(self, note_id: UUID) -> None:
        if note_id not in self._approved_note_ids:
            raise _not_found()


class SupabaseInteractionRepository:
    def __init__(self, client, *, public_client=None) -> None:
        self._client = client
        self._public_client = public_client or client

    def set_like(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState:
        del user_id
        return self._state_rpc("set_travel_note_like_internal", note_id, enabled)

    def set_bookmark(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState:
        del user_id
        return self._state_rpc("set_travel_note_bookmark_internal", note_id, enabled)

    def viewer_state(self, user_id: UUID, note_id: UUID) -> TravelNoteInteractionState:
        del user_id
        response = self._client.rpc(
            "get_travel_note_interaction_state_internal", {"p_note_id": str(note_id)}
        ).execute()
        return _state_from_row(_first_row(response.data))

    def submit_comment(self, user_id: UUID, note_id: UUID, body: str) -> TravelNoteComment:
        del user_id
        response = self._client.rpc(
            "create_travel_note_comment_internal",
            {"p_note_id": str(note_id), "p_body": body},
        ).execute()
        return _comment_from_row(_first_row(response.data))

    def list_public_comments(
        self, note_id: UUID, cursor: str | None, limit: int, viewer_id: UUID | None = None
    ) -> TravelNoteCommentPage:
        response = self._public_client.rpc(
            "list_public_travel_note_comments_internal",
            {
                "p_note_id": str(note_id),
                "p_page_size": limit,
                "p_cursor": cursor,
                "p_viewer_id": str(viewer_id) if viewer_id else None,
            },
        ).execute()
        row = _first_page(response.data)
        return TravelNoteCommentPage(
            items=[_comment_from_row(item) for item in row.get("items", [])],
            next_cursor=row.get("next_cursor"),
        )

    def submit_report(
        self,
        reporter_id: UUID,
        note_id: UUID,
        target_type: ReviewTargetType,
        target_id: UUID,
        reason: str,
    ) -> TravelNoteReportView:
        del reporter_id
        response = self._client.rpc(
            "create_travel_note_report_internal",
            {
                "p_note_id": str(note_id),
                "p_target_type": target_type,
                "p_target_id": str(target_id),
                "p_reason": reason,
            },
        ).execute()
        return _report_from_row(_first_row(response.data))

    def _state_rpc(self, name: str, note_id: UUID, enabled: bool) -> TravelNoteInteractionState:
        response = self._client.rpc(
            name, {"p_note_id": str(note_id), "p_enabled": enabled}
        ).execute()
        return _state_from_row(_first_row(response.data))


class TravelNoteInteractionModule:
    def __init__(
        self,
        repository: InteractionRepository,
        public_repository: InteractionRepository | None = None,
    ) -> None:
        self._repository = repository
        self._public_repository = public_repository or repository

    def set_like(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState:
        return self._repository.set_like(user_id, note_id, enabled)

    def set_bookmark(self, user_id: UUID, note_id: UUID, enabled: bool) -> TravelNoteInteractionState:
        return self._repository.set_bookmark(user_id, note_id, enabled)

    def viewer_state(self, user_id: UUID, note_id: UUID) -> TravelNoteInteractionState:
        return self._repository.viewer_state(user_id, note_id)

    def submit_comment(self, user_id: UUID, note_id: UUID, body: str) -> TravelNoteComment:
        normalized = body.strip()
        if not 1 <= len(normalized) <= 500:
            raise _validation_failed()
        return self._repository.submit_comment(user_id, note_id, normalized)

    def list_public_comments(
        self, note_id: UUID, cursor: str | None, limit: int, viewer_id: UUID | None = None
    ) -> TravelNoteCommentPage:
        if not 1 <= limit <= 50:
            raise _validation_failed()
        return self._public_repository.list_public_comments(note_id, cursor, limit, viewer_id)

    def submit_report(
        self,
        reporter_id: UUID,
        note_id: UUID,
        *,
        target_type: ReviewTargetType,
        target_id: UUID,
        reason: str,
    ) -> TravelNoteReportView:
        normalized = reason.strip()
        if not 1 <= len(normalized) <= 500:
            raise _validation_failed()
        return self._repository.submit_report(
            reporter_id, note_id, target_type, target_id, normalized
        )


def create_user_scoped_interaction_repository(
    url: str,
    anon_key: str,
    access_token: str,
    *,
    public_client=None,
) -> SupabaseInteractionRepository:
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(access_token)
    return SupabaseInteractionRepository(client, public_client=public_client)


def create_public_interaction_repository(
    url: str, service_key: str, *, client=None
) -> SupabaseInteractionRepository:
    from app.travel_notes.supabase_repositories import create_internal_supabase_client

    return SupabaseInteractionRepository(
        client or create_internal_supabase_client(url, service_key)
    )


def _comment_view(comment: _StoredComment) -> TravelNoteComment:
    return TravelNoteComment(
        id=comment.id,
        note_id=comment.note_id,
        author_display_name=comment.author_display_name,
        body=comment.body,
        status=comment.status,
        published_at=comment.published_at,
    )


def _comment_from_row(row: dict[str, object]) -> TravelNoteComment:
    return TravelNoteComment.model_validate(row)


def _state_from_row(row: dict[str, object]) -> TravelNoteInteractionState:
    return TravelNoteInteractionState.model_validate(row)


def _report_from_row(row: dict[str, object]) -> TravelNoteReportView:
    return TravelNoteReportView.model_validate(row)


def _first_row(data: object) -> dict[str, object]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    raise RuntimeError("interaction RPC returned no row")


def _first_page(data: object) -> dict[str, object]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    raise RuntimeError("interaction RPC returned no page")


def _not_found() -> AppError:
    return AppError("TRAVEL_NOTE_NOT_FOUND", "Travel note not found")


def _validation_failed() -> AppError:
    return AppError(
        "TRAVEL_NOTE_VALIDATION_FAILED",
        "Travel note request validation failed",
    )
