from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.auth import CurrentUser, OptionalCurrentUser
from app.composition import get_optional_travel_note_module, get_travel_note_module
from app.core.errors import AppError
from app.travel_notes.models import (
    TravelNoteDetail,
    TravelNoteCreatorPage,
    TravelNoteDraftInput,
    TravelNoteOwnerView,
    TravelNotePage,
)
from app.travel_notes.service import TravelNoteModule


TravelNoteMineStatus = Literal["draft", "pending_review", "approved", "rejected"]
_VALID_OWNER_STATUSES = {"draft", "pending_review", "approved", "rejected"}

router = APIRouter(tags=["travel-notes"])


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


def _raise_validation_failed() -> None:
    _raise_http(
        AppError(
            "TRAVEL_NOTE_VALIDATION_FAILED",
            "Travel note request validation failed",
        )
    )


@router.get("/api/community/notes", response_model=TravelNotePage)
def list_travel_notes(
    user: OptionalCurrentUser,
    module: TravelNoteModule = Depends(get_optional_travel_note_module),
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    category: str | None = None,
    q: str | None = None,
) -> TravelNotePage:
    del user
    try:
        return module.list_public(
            cursor=cursor,
            limit=limit,
            category=category,
            search_query=q,
        )
    except AppError as error:
        _raise_http(error)

@router.get("/api/community/creators/{creator_slug}", response_model=TravelNoteCreatorPage)
def get_creator_page(
    creator_slug: str,
    user: OptionalCurrentUser,
    module: TravelNoteModule = Depends(get_optional_travel_note_module),
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> TravelNoteCreatorPage:
    del user
    try:
        return module.get_creator_page(creator_slug, cursor=cursor, limit=limit)
    except AppError as error:
        _raise_http(error)



@router.get("/api/community/notes/{note_id}", response_model=TravelNoteDetail)
def get_travel_note(
    note_id: UUID,
    user: OptionalCurrentUser,
    module: TravelNoteModule = Depends(get_optional_travel_note_module),
) -> TravelNoteDetail:
    del user
    try:
        return module.get_public(note_id)
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/community/notes",
    response_model=TravelNoteOwnerView,
    status_code=status.HTTP_201_CREATED,
)
def create_travel_note(
    request: TravelNoteDraftInput,
    user: CurrentUser,
    module: TravelNoteModule = Depends(get_travel_note_module),
) -> TravelNoteOwnerView:
    try:
        return module.create_draft(user.id, request)
    except AppError as error:
        _raise_http(error)


@router.put("/api/community/notes/{note_id}", response_model=TravelNoteOwnerView)
def update_travel_note(
    note_id: UUID,
    request: TravelNoteDraftInput,
    user: CurrentUser,
    module: TravelNoteModule = Depends(get_travel_note_module),
) -> TravelNoteOwnerView:
    try:
        return module.replace_draft(user.id, note_id, request)
    except AppError as error:
        _raise_http(error)


@router.post("/api/community/notes/{note_id}/submit", response_model=TravelNoteOwnerView)
def submit_travel_note(
    note_id: UUID,
    user: CurrentUser,
    module: TravelNoteModule = Depends(get_travel_note_module),
) -> TravelNoteOwnerView:
    try:
        return module.submit(user.id, note_id)
    except AppError as error:
        _raise_http(error)


@router.delete("/api/community/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_travel_note(
    note_id: UUID,
    user: CurrentUser,
    module: TravelNoteModule = Depends(get_travel_note_module),
) -> Response:
    try:
        module.soft_delete(user.id, note_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AppError as error:
        _raise_http(error)


@router.get("/api/me/travel-notes")
def list_my_travel_notes(
    user: CurrentUser,
    module: TravelNoteModule = Depends(get_travel_note_module),
    status: TravelNoteMineStatus | str | None = None,
) -> dict[str, list[dict[str, object]]]:
    if status is not None and status not in _VALID_OWNER_STATUSES:
        _raise_validation_failed()
    try:
        items = module.list_mine(user.id)
    except AppError as error:
        _raise_http(error)
    if status is not None:
        items = [item for item in items if item.status == status]
    return {"items": [item.model_dump(mode="json") for item in items]}
