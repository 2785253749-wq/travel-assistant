from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from typing import Callable, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import Field, field_validator

from app.core.errors import AppError
from app.schemas import StrictSchema
from app.travel_notes.models import ReviewTargetType


ModerationDecision = Literal["approved", "rejected"]
ModerationQueueTarget = Literal["note", "comment", "report"]
ModerationHideTarget = Literal["note", "comment"]


TModerationItem = TypeVar("TModerationItem")


def encode_moderation_cursor(occurred_at: datetime, item_id: UUID) -> str:
    raw_cursor = f"{occurred_at.isoformat()}|{item_id}"
    return urlsafe_b64encode(raw_cursor.encode("utf-8")).decode("ascii").rstrip("=")


def decode_moderation_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
        occurred_at, item_id = decoded.split("|", maxsplit=1)
        return datetime.fromisoformat(occurred_at), UUID(item_id)
    except Exception as exc:
        raise ValueError("invalid moderation cursor") from exc


def paginate_moderation_items(
    items: list[TModerationItem],
    cursor: str | None,
    limit: int,
    timestamp: Callable[[TModerationItem], datetime],
) -> tuple[list[TModerationItem], str | None]:
    if cursor:
        try:
            cursor_time, cursor_id = decode_moderation_cursor(cursor)
        except ValueError as exc:
            raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation request is invalid") from exc
        items = [
            item
            for item in items
            if (timestamp(item), str(item.id)) > (cursor_time, str(cursor_id))
        ]
    visible = items[:limit]
    next_cursor = None
    if len(items) > limit and visible:
        next_cursor = encode_moderation_cursor(timestamp(visible[-1]), visible[-1].id)
    return visible, next_cursor
ReportResolution = Literal["dismissed", "actioned"]


class ModerationImage(StrictSchema):
    id: UUID
    image_url: str = Field(min_length=1, max_length=2048)
    sort_order: int = Field(ge=0, le=8)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)


class ModerationNote(StrictSchema):
    id: UUID
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=5000)
    location_name: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=20)
    status: Literal["pending_review", "approved", "rejected"]
    review_reason: str | None = Field(default=None, min_length=1, max_length=500)
    submitted_at: datetime
    author_display_name: str = Field(min_length=1, max_length=40)
    images: list[ModerationImage] = Field(max_length=9)


class ModerationComment(StrictSchema):
    id: UUID
    note_id: UUID
    author_display_name: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=500)
    status: Literal["pending_review", "approved", "rejected"]
    review_reason: str | None = Field(default=None, min_length=1, max_length=500)
    created_at: datetime


class ModerationReport(StrictSchema):
    id: UUID
    target_type: ReviewTargetType
    target_id: UUID
    reason: str = Field(min_length=1, max_length=500)
    status: Literal["pending", "dismissed", "actioned"]
    resolution_note: str | None = Field(default=None, min_length=1, max_length=500)
    created_at: datetime


class ModerationHideResult(StrictSchema):
    target_type: ModerationHideTarget
    target_id: UUID
    hidden: bool


class ModerationNotePage(StrictSchema):
    items: list[ModerationNote]
    next_cursor: str | None = None


class ModerationCommentPage(StrictSchema):
    items: list[ModerationComment]
    next_cursor: str | None = None


class ModerationReportPage(StrictSchema):
    items: list[ModerationReport]
    next_cursor: str | None = None


class ModerationQueuePage(StrictSchema):
    items: list[ModerationNote | ModerationComment | ModerationReport]
    next_cursor: str | None = None


class ModerationReviewRequest(StrictSchema):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _trim_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ModerationReviewInput(StrictSchema):
    decision: ModerationDecision
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _trim_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ReportResolutionInput(StrictSchema):
    decision: ReportResolution
    resolution_note: str | None = Field(default=None, max_length=500)

    @field_validator("resolution_note", mode="before")
    @classmethod
    def _trim_resolution_note(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ModerationRepository(Protocol):
    def is_admin(self, user_id: UUID) -> bool: ...

    def list_notes(self, cursor: str | None, limit: int) -> ModerationNotePage: ...

    def list_comments(self, cursor: str | None, limit: int) -> ModerationCommentPage: ...

    def list_reports(self, cursor: str | None, limit: int) -> ModerationReportPage: ...

    def review_note(
        self, moderator_id: UUID, note_id: UUID, decision: ModerationDecision, reason: str | None
    ) -> ModerationNote: ...

    def review_comment(
        self, moderator_id: UUID, comment_id: UUID, decision: ModerationDecision, reason: str | None
    ) -> ModerationComment: ...

    def resolve_report(
        self,
        moderator_id: UUID,
        report_id: UUID,
        decision: ReportResolution,
        resolution_note: str | None,
    ) -> ModerationReport: ...

    def hide_content(
        self, moderator_id: UUID, target_type: ModerationHideTarget, target_id: UUID
    ) -> ModerationHideResult: ...


class SignedMediaGateway(Protocol):
    def sign_paths(self, paths: list[str]) -> list[str]: ...


class InMemoryModerationRepository:
    def __init__(self, *, admin_user_ids: set[UUID] | None = None) -> None:
        self._admin_user_ids = set(admin_user_ids or set())
        self._notes: dict[UUID, ModerationNote] = {}
        self._comments: dict[UUID, ModerationComment] = {}
        self._reports: dict[UUID, ModerationReport] = {}
        self._hidden_targets: set[tuple[ModerationHideTarget, UUID]] = set()
        self.decisions: list[dict[str, object]] = []

    def add_note(self, note: ModerationNote) -> None:
        self._notes[note.id] = note

    def add_comment(self, comment: ModerationComment) -> None:
        self._comments[comment.id] = comment

    def add_report(self, report: ModerationReport) -> None:
        self._reports[report.id] = report

    def is_admin(self, user_id: UUID) -> bool:
        return user_id in self._admin_user_ids

    def is_hidden(self, target_type: ModerationHideTarget, target_id: UUID) -> bool:
        return (target_type, target_id) in self._hidden_targets

    def list_notes(self, cursor: str | None, limit: int) -> ModerationNotePage:
        items = [item for item in self._notes.values() if item.status == "pending_review"]
        items.sort(key=lambda item: (item.submitted_at, str(item.id)))
        visible, next_cursor = paginate_moderation_items(items, cursor, limit, lambda item: item.submitted_at)
        return ModerationNotePage(items=visible, next_cursor=next_cursor)

    def list_comments(self, cursor: str | None, limit: int) -> ModerationCommentPage:
        items = [item for item in self._comments.values() if item.status == "pending_review"]
        items.sort(key=lambda item: (item.created_at, str(item.id)))
        visible, next_cursor = paginate_moderation_items(items, cursor, limit, lambda item: item.created_at)
        return ModerationCommentPage(items=visible, next_cursor=next_cursor)

    def list_reports(self, cursor: str | None, limit: int) -> ModerationReportPage:
        items = [item for item in self._reports.values() if item.status == "pending"]
        items.sort(key=lambda item: (item.created_at, str(item.id)))
        visible, next_cursor = paginate_moderation_items(items, cursor, limit, lambda item: item.created_at)
        return ModerationReportPage(items=visible, next_cursor=next_cursor)

    def review_note(
        self, moderator_id: UUID, note_id: UUID, decision: ModerationDecision, reason: str | None
    ) -> ModerationNote:
        del moderator_id
        note = self._notes.get(note_id)
        if note is None:
            raise _not_found()
        if note.status != "pending_review":
            raise _invalid_state()
        normalized_reason = _review_reason(decision, reason)
        updated = note.model_copy(
            update={
                "status": decision,
                "review_reason": normalized_reason,
            }
        )
        self._notes[note_id] = updated
        self.decisions.append(
            {"target_type": "note", "target_id": note_id, "decision": decision, "reason": normalized_reason}
        )
        return updated

    def review_comment(
        self, moderator_id: UUID, comment_id: UUID, decision: ModerationDecision, reason: str | None
    ) -> ModerationComment:
        del moderator_id
        comment = self._comments.get(comment_id)
        if comment is None:
            raise _not_found()
        if comment.status != "pending_review":
            raise _invalid_state()
        normalized_reason = _review_reason(decision, reason)
        updated = comment.model_copy(
            update={"status": decision, "review_reason": normalized_reason}
        )
        self._comments[comment_id] = updated
        self.decisions.append(
            {"target_type": "comment", "target_id": comment_id, "decision": decision, "reason": normalized_reason}
        )
        return updated

    def resolve_report(
        self,
        moderator_id: UUID,
        report_id: UUID,
        decision: ReportResolution,
        resolution_note: str | None,
    ) -> ModerationReport:
        del moderator_id
        report = self._reports.get(report_id)
        if report is None:
            raise _not_found()
        if report.status != "pending":
            raise _invalid_state()
        normalized_note = _resolution_note(resolution_note)
        updated = report.model_copy(
            update={"status": decision, "resolution_note": normalized_note}
        )
        self._reports[report_id] = updated
        self.decisions.append(
            {"target_type": "report", "target_id": report_id, "decision": decision, "reason": normalized_note}
        )
        return updated

    def hide_content(
        self, moderator_id: UUID, target_type: ModerationHideTarget, target_id: UUID
    ) -> ModerationHideResult:
        del moderator_id
        if target_type == "note":
            if target_id not in self._notes:
                raise _not_found()
        elif target_type == "comment":
            if target_id not in self._comments:
                raise _not_found()
        else:
            raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation request is invalid")
        self._hidden_targets.add((target_type, target_id))
        self.decisions.append(
            {"target_type": target_type, "target_id": target_id, "decision": "hide_content", "reason": None}
        )
        return ModerationHideResult(target_type=target_type, target_id=target_id, hidden=True)


class TravelNoteModerationModule:
    def __init__(self, repository: ModerationRepository) -> None:
        self._repository = repository

    def list_notes(self, user_id: UUID, *, cursor: str | None = None, limit: int = 20) -> ModerationNotePage:
        self._require_admin(user_id)
        _validate_limit(limit)
        return self._repository.list_notes(cursor, limit)

    def list_comments(self, user_id: UUID, *, cursor: str | None = None, limit: int = 20) -> ModerationCommentPage:
        self._require_admin(user_id)
        _validate_limit(limit)
        return self._repository.list_comments(cursor, limit)

    def list_reports(self, user_id: UUID, *, cursor: str | None = None, limit: int = 20) -> ModerationReportPage:
        self._require_admin(user_id)
        _validate_limit(limit)
        return self._repository.list_reports(cursor, limit)

    def list_queue(
        self,
        user_id: UUID,
        target_type: ModerationQueueTarget,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ModerationQueuePage:
        self._require_admin(user_id)
        _validate_limit(limit)
        if target_type == "note":
            page = self._repository.list_notes(cursor, limit)
        elif target_type == "comment":
            page = self._repository.list_comments(cursor, limit)
        elif target_type == "report":
            page = self._repository.list_reports(cursor, limit)
        else:
            raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation request is invalid")
        return ModerationQueuePage(items=page.items, next_cursor=page.next_cursor)

    def review_note(
        self, user_id: UUID, note_id: UUID, *, decision: ModerationDecision, reason: str | None
    ) -> ModerationNote:
        self._require_admin(user_id)
        return self._repository.review_note(user_id, note_id, decision, _review_reason(decision, reason))

    def review_comment(
        self, user_id: UUID, comment_id: UUID, *, decision: ModerationDecision, reason: str | None
    ) -> ModerationComment:
        self._require_admin(user_id)
        return self._repository.review_comment(user_id, comment_id, decision, _review_reason(decision, reason))

    def resolve_report(
        self,
        user_id: UUID,
        report_id: UUID,
        *,
        decision: ReportResolution,
        resolution_note: str | None,
    ) -> ModerationReport:
        self._require_admin(user_id)
        return self._repository.resolve_report(
            user_id, report_id, decision, _resolution_note(resolution_note)
        )

    def hide_content(
        self, user_id: UUID, target_type: ModerationHideTarget, target_id: UUID
    ) -> ModerationHideResult:
        self._require_admin(user_id)
        if target_type not in ("note", "comment"):
            raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation request is invalid")
        return self._repository.hide_content(user_id, target_type, target_id)

    def _require_admin(self, user_id: UUID) -> None:
        if not self._repository.is_admin(user_id):
            raise AppError("COMMUNITY_ADMIN_REQUIRED", "Community administrator access is required")


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation request is invalid")


def _review_reason(decision: ModerationDecision, reason: str | None) -> str | None:
    normalized = reason.strip() if isinstance(reason, str) else None
    if decision == "rejected" and (normalized is None or not 1 <= len(normalized) <= 500):
        raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Rejection reason is required")
    if normalized is not None and len(normalized) > 500:
        raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation reason is too long")
    return normalized if decision == "rejected" else None


def _resolution_note(value: str | None) -> str | None:
    normalized = value.strip() if isinstance(value, str) else None
    if normalized is not None and not 1 <= len(normalized) <= 500:
        raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Resolution note is invalid")
    return normalized


def _not_found() -> AppError:
    return AppError("COMMUNITY_MODERATION_NOT_FOUND", "Moderation target was not found")


def _invalid_state() -> AppError:
    return AppError("COMMUNITY_MODERATION_INVALID_STATE", "Moderation target is no longer pending")
