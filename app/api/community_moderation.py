from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import CurrentUser
from app.composition import get_community_moderation_module
from app.core.errors import AppError
from app.travel_notes.moderation import (
    ModerationComment,
    ModerationHideResult,
    ModerationNote,
    ModerationQueuePage,
    ModerationReport,
    ModerationReviewRequest,
    ReportResolutionInput,
    TravelNoteModerationModule,
)


router = APIRouter(tags=["community-moderation"])


def _raise_http(error: AppError) -> None:
    status_code = {
        "COMMUNITY_ADMIN_REQUIRED": status.HTTP_403_FORBIDDEN,
        "COMMUNITY_MODERATION_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "COMMUNITY_MODERATION_INVALID_STATE": status.HTTP_409_CONFLICT,
        "COMMUNITY_MODERATION_VALIDATION_FAILED": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "COMMUNITY_MODERATION_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
    messages = {
        "COMMUNITY_ADMIN_REQUIRED": "Community administrator access is required",
        "COMMUNITY_MODERATION_NOT_FOUND": "Moderation target was not found",
        "COMMUNITY_MODERATION_INVALID_STATE": "Moderation target is no longer pending",
        "COMMUNITY_MODERATION_VALIDATION_FAILED": "Moderation request is invalid",
        "COMMUNITY_MODERATION_UNAVAILABLE": "Moderation service is unavailable",
    }
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": messages.get(error.code, "Service unavailable")},
    ) from error


@router.get("/api/admin/community/review-queue", response_model=ModerationQueuePage)
def list_review_queue(
    target_type: Annotated[Literal["note", "comment", "report"], Query()],
    user: CurrentUser,
    module: TravelNoteModerationModule = Depends(get_community_moderation_module),
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ModerationQueuePage:
    try:
        return module.list_queue(user.id, target_type, cursor=cursor, limit=limit)
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/admin/community/reviews/{target_type}/{target_id}/{decision}",
    response_model=ModerationNote | ModerationComment,
)
def review_content(
    target_type: Literal["note", "comment"],
    target_id: UUID,
    decision: Literal["approve", "reject"],
    request: ModerationReviewRequest,
    user: CurrentUser,
    module: TravelNoteModerationModule = Depends(get_community_moderation_module),
) -> ModerationNote | ModerationComment:
    try:
        moderation_decision = "approved" if decision == "approve" else "rejected"
        if target_type == "note":
            return module.review_note(
                user.id, target_id, decision=moderation_decision, reason=request.reason
            )
        return module.review_comment(
            user.id, target_id, decision=moderation_decision, reason=request.reason
        )
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/admin/community/hide/{target_type}/{target_id}",
    response_model=ModerationHideResult,
)
def hide_moderation_content(
    target_type: Literal["note", "comment"],
    target_id: UUID,
    user: CurrentUser,
    module: TravelNoteModerationModule = Depends(get_community_moderation_module),
) -> ModerationHideResult:
    try:
        return module.hide_content(user.id, target_type, target_id)
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/admin/community/reports/{report_id}/resolve",
    response_model=ModerationReport,
)
def resolve_moderation_report(
    report_id: UUID,
    request: ReportResolutionInput,
    user: CurrentUser,
    module: TravelNoteModerationModule = Depends(get_community_moderation_module),
) -> ModerationReport:
    try:
        return module.resolve_report(
            user.id,
            report_id,
            decision=request.decision,
            resolution_note=request.resolution_note,
        )
    except AppError as error:
        _raise_http(error)
