from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import CurrentUser, OptionalCurrentUser
from app.composition import (
    get_optional_travel_note_interaction_module,
    get_travel_note_interaction_module,
)
from app.core.errors import AppError
from app.travel_notes.interactions import (
    TravelNoteCommentInput,
    TravelNoteCommentPage,
    TravelNoteInteractionModule,
    TravelNoteInteractionState,
    TravelNoteReportInput,
    TravelNoteReportView,
)
from app.travel_notes.models import TravelNoteComment


router = APIRouter(tags=["community-interactions"])


def _raise_http(error: AppError) -> None:
    status_code = {
        "TRAVEL_NOTE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "TRAVEL_NOTE_INVALID_STATE": status.HTTP_409_CONFLICT,
        "TRAVEL_NOTE_VALIDATION_FAILED": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "TRAVEL_NOTE_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
    messages = {
        "TRAVEL_NOTE_NOT_FOUND": "Travel note not found",
        "TRAVEL_NOTE_INVALID_STATE": "Travel note state is invalid",
        "TRAVEL_NOTE_VALIDATION_FAILED": "Travel note request validation failed",
        "TRAVEL_NOTE_UNAVAILABLE": "Travel note service is unavailable",
    }
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": messages.get(error.code, "Service unavailable")},
    ) from error


@router.put(
    "/api/community/notes/{note_id}/like",
    response_model=TravelNoteInteractionState,
)
def like_travel_note(
    note_id: UUID,
    user: CurrentUser,
    module: TravelNoteInteractionModule = Depends(get_travel_note_interaction_module),
) -> TravelNoteInteractionState:
    try:
        return module.set_like(user.id, note_id, True)
    except AppError as error:
        _raise_http(error)


@router.delete(
    "/api/community/notes/{note_id}/like",
    response_model=TravelNoteInteractionState,
)
def unlike_travel_note(
    note_id: UUID,
    user: CurrentUser,
    module: TravelNoteInteractionModule = Depends(get_travel_note_interaction_module),
) -> TravelNoteInteractionState:
    try:
        return module.set_like(user.id, note_id, False)
    except AppError as error:
        _raise_http(error)


@router.put(
    "/api/community/notes/{note_id}/bookmark",
    response_model=TravelNoteInteractionState,
)
def bookmark_travel_note(
    note_id: UUID,
    user: CurrentUser,
    module: TravelNoteInteractionModule = Depends(get_travel_note_interaction_module),
) -> TravelNoteInteractionState:
    try:
        return module.set_bookmark(user.id, note_id, True)
    except AppError as error:
        _raise_http(error)


@router.delete(
    "/api/community/notes/{note_id}/bookmark",
    response_model=TravelNoteInteractionState,
)
def unbookmark_travel_note(
    note_id: UUID,
    user: CurrentUser,
    module: TravelNoteInteractionModule = Depends(get_travel_note_interaction_module),
) -> TravelNoteInteractionState:
    try:
        return module.set_bookmark(user.id, note_id, False)
    except AppError as error:
        _raise_http(error)


@router.get(
    "/api/community/notes/{note_id}/comments",
    response_model=TravelNoteCommentPage,
)
def list_travel_note_comments(
    note_id: UUID,
    user: OptionalCurrentUser,
    module: TravelNoteInteractionModule = Depends(get_optional_travel_note_interaction_module),
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> TravelNoteCommentPage:
    try:
        return module.list_public_comments(
            note_id, cursor, limit, viewer_id=user.id if user else None
        )
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/community/notes/{note_id}/comments",
    response_model=TravelNoteComment,
    status_code=status.HTTP_201_CREATED,
)
def create_travel_note_comment(
    note_id: UUID,
    request: TravelNoteCommentInput,
    user: CurrentUser,
    module: TravelNoteInteractionModule = Depends(get_travel_note_interaction_module),
) -> TravelNoteComment:
    try:
        return module.submit_comment(user.id, note_id, request.body)
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/community/notes/{note_id}/reports",
    response_model=TravelNoteReportView,
    status_code=status.HTTP_201_CREATED,
)
def create_travel_note_report(
    note_id: UUID,
    request: TravelNoteReportInput,
    user: CurrentUser,
    module: TravelNoteInteractionModule = Depends(get_travel_note_interaction_module),
) -> TravelNoteReportView:
    try:
        return module.submit_report(
            user.id,
            note_id,
            target_type=request.target_type,
            target_id=request.target_id,
            reason=request.reason,
        )
    except AppError as error:
        _raise_http(error)
