from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.errors import AppError
from app.travel_notes.moderation import (
    decode_moderation_cursor,
    encode_moderation_cursor,
    ModerationComment,
    ModerationCommentPage,
    ModerationDecision,
    ModerationHideResult,
    ModerationImage,
    ModerationNote,
    ModerationNotePage,
    ModerationReport,
    ModerationReportPage,
    ReportResolution,
    SignedMediaGateway,
)


class SupabaseModerationRepository:
    def __init__(self, client, *, media_gateway: SignedMediaGateway, internal_client) -> None:
        self._client = client
        self._internal_client = internal_client
        self._media_gateway = media_gateway

    def is_admin(self, user_id: UUID) -> bool:
        del user_id
        try:
            response = self._client.rpc("is_community_admin", {}).execute()
            data = response.data
            if isinstance(data, bool):
                return data
            if isinstance(data, list) and data:
                return bool(data[0])
            if isinstance(data, dict):
                return bool(next(iter(data.values()), False))
            return False
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def list_notes(self, cursor: str | None, limit: int) -> ModerationNotePage:
        try:
            cursor_time, cursor_id = _decode_cursor(cursor)
            response = self._client.rpc(
                "list_pending_travel_notes_for_moderation",
                {"p_cursor_time": cursor_time, "p_cursor_id": cursor_id, "p_page_size": limit + 1},
            ).execute()
            items = [self._note_from_row(row) for row in _rows(response.data)]
            visible, next_cursor = _page(items, cursor, limit, lambda item: item.submitted_at)
            return ModerationNotePage(items=visible, next_cursor=next_cursor)
        except AppError:
            raise
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def list_comments(self, cursor: str | None, limit: int) -> ModerationCommentPage:
        try:
            cursor_time, cursor_id = _decode_cursor(cursor)
            response = self._client.rpc(
                "list_pending_travel_note_comments_for_moderation",
                {"p_cursor_time": cursor_time, "p_cursor_id": cursor_id, "p_page_size": limit + 1},
            ).execute()
            items = [ModerationComment.model_validate(row) for row in _rows(response.data)]
            visible, next_cursor = _page(items, cursor, limit, lambda item: item.created_at)
            return ModerationCommentPage(items=visible, next_cursor=next_cursor)
        except AppError:
            raise
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def list_reports(self, cursor: str | None, limit: int) -> ModerationReportPage:
        try:
            cursor_time, cursor_id = _decode_cursor(cursor)
            response = self._client.rpc(
                "list_pending_travel_note_reports_for_moderation",
                {"p_cursor_time": cursor_time, "p_cursor_id": cursor_id, "p_page_size": limit + 1},
            ).execute()
            items = [ModerationReport.model_validate(row) for row in _rows(response.data)]
            visible, next_cursor = _page(items, cursor, limit, lambda item: item.created_at)
            return ModerationReportPage(items=visible, next_cursor=next_cursor)
        except AppError:
            raise
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def review_note(
        self, moderator_id: UUID, note_id: UUID, decision: ModerationDecision, reason: str | None
    ) -> ModerationNote:
        del moderator_id
        try:
            self._client.rpc(
                "review_travel_note",
                {"p_note_id": str(note_id), "decision": decision, "reason": reason},
            ).execute()
            response = self._client.rpc(
                "get_travel_note_moderation_item", {"p_note_id": str(note_id)}
            ).execute()
            rows = _rows(response.data)
            if not rows:
                raise _not_found()
            return self._note_from_row(rows[0])
        except AppError:
            raise
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def review_comment(
        self, moderator_id: UUID, comment_id: UUID, decision: ModerationDecision, reason: str | None
    ) -> ModerationComment:
        del moderator_id
        try:
            self._client.rpc(
                "review_travel_note_comment",
                {"p_comment_id": str(comment_id), "decision": decision, "reason": reason},
            ).execute()
            response = self._client.rpc(
                "get_travel_note_comment_moderation_item", {"p_comment_id": str(comment_id)}
            ).execute()
            rows = _rows(response.data)
            if not rows:
                raise _not_found()
            return ModerationComment.model_validate(rows[0])
        except AppError:
            raise
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def resolve_report(
        self,
        moderator_id: UUID,
        report_id: UUID,
        decision: ReportResolution,
        resolution_note: str | None,
    ) -> ModerationReport:
        del moderator_id
        try:
            response = self._client.rpc(
                "resolve_travel_note_report",
                {
                    "p_report_id": str(report_id),
                    "p_decision": decision,
                    "p_resolution_note": resolution_note,
                },
            ).execute()
            rows = _rows(response.data)
            if not rows:
                raise _not_found()
            return ModerationReport.model_validate(rows[0])
        except AppError:
            raise
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def hide_content(
        self, moderator_id: UUID, target_type: str, target_id: UUID
    ) -> ModerationHideResult:
        del moderator_id
        try:
            response = self._client.rpc(
                "hide_travel_note_moderation_target",
                {"p_target_type": target_type, "p_target_id": str(target_id)},
            ).execute()
            rows = _rows(response.data)
            if not rows:
                raise _not_found()
            return ModerationHideResult.model_validate(rows[0])
        except AppError:
            raise
        except Exception as exc:
            raise _map_database_error(exc) from exc

    def _load_image_paths(self, image_ids: list[UUID]) -> list[str]:
        if not image_ids:
            return []
        response = (
            self._internal_client.table("travel_note_images")
            .select("id, storage_path")
            .in_("id", [str(image_id) for image_id in image_ids])
            .execute()
        )
        rows = _rows(response.data)
        paths_by_id = {UUID(str(row["id"])): str(row["storage_path"]) for row in rows}
        return [paths_by_id[image_id] for image_id in image_ids if image_id in paths_by_id]

    def _note_from_row(self, row: dict[str, Any]) -> ModerationNote:
        manifest = row.get("image_manifest") or []
        if not isinstance(manifest, list):
            raise RuntimeError("moderation image manifest is invalid")
        image_ids = [
            UUID(str(item["id"]))
            for item in manifest
            if isinstance(item, dict) and item.get("id")
        ]
        paths = self._load_image_paths(image_ids)
        signed_urls = self._media_gateway.sign_paths(paths)
        images = []
        for item, image_url in zip(manifest, signed_urls, strict=False):
            if not isinstance(item, dict):
                continue
            images.append(
                ModerationImage(
                    id=UUID(str(item["id"])),
                    image_url=image_url,
                    sort_order=int(item.get("sort_order", 0)),
                    width=int(item.get("width", 1)),
                    height=int(item.get("height", 1)),
                )
            )
        return ModerationNote.model_validate({**row, "images": images})


def create_user_scoped_moderation_repository(
    url: str,
    anon_key: str,
    access_token: str,
    *,
    media_gateway: SignedMediaGateway,
    internal_client=None,
) -> SupabaseModerationRepository:
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(access_token)
    return SupabaseModerationRepository(
        client, media_gateway=media_gateway, internal_client=internal_client or client
    )


def _decode_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if cursor is None:
        return None, None
    try:
        occurred_at, item_id = decode_moderation_cursor(cursor)
    except ValueError as exc:
        raise AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation request is invalid") from exc
    return occurred_at.isoformat(), str(item_id)


def _page(items, cursor: str | None, limit: int, timestamp):
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


def _rows(data: object) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and all(isinstance(row, dict) for row in data):
        return data
    raise RuntimeError("moderation RPC returned an invalid row set")


def _not_found() -> AppError:
    return AppError("COMMUNITY_MODERATION_NOT_FOUND", "Moderation target was not found")


def _map_database_error(error: Exception) -> AppError:
    if isinstance(error, AppError):
        return error
    code = str(getattr(error, "code", "") or "").upper()
    message = str(error).lower()
    if code == "P0002" or "not found" in message:
        return _not_found()
    if "administrator" in message or ("admin" in message and "required" in message):
        return AppError("COMMUNITY_ADMIN_REQUIRED", "Community administrator access is required")
    if code in {"P0001", "23514", "22P02"}:
        if "invalid state" in message or "pending" in message:
            return AppError("COMMUNITY_MODERATION_INVALID_STATE", "Moderation target is no longer pending")
        return AppError("COMMUNITY_MODERATION_VALIDATION_FAILED", "Moderation request is invalid")
    return AppError("COMMUNITY_MODERATION_UNAVAILABLE", "Moderation service is unavailable")
