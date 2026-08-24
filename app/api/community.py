from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.auth import CurrentUser, OptionalCurrentUser
from app.community.models import CommunityPage, CommunityPost, CommunityPublishInput
from app.community.service import CommunityModule
from app.composition import get_community_module, get_optional_community_module
from app.core.errors import AppError


router = APIRouter(tags=["community"])


def _raise_http(error: AppError) -> None:
    status_code = {
        "COMMUNITY_POST_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "COMMUNITY_POST_EXISTS": status.HTTP_409_CONFLICT,
        "COMMUNITY_TRIP_NOT_PUBLISHABLE": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "COMMUNITY_VALIDATION_FAILED": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "COMMUNITY_PUBLISH_FAILED": status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.get("/api/community/posts", response_model=CommunityPage)
def list_community_posts(
    user: OptionalCurrentUser,
    module: CommunityModule = Depends(get_optional_community_module),
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunityPage:
    try:
        return module.list_posts(
            cursor=cursor,
            limit=limit,
            viewer_id=None if user is None else user.id,
        )
    except AppError as error:
        _raise_http(error)


@router.get("/api/community/posts/{post_id}", response_model=CommunityPost)
def get_community_post(
    post_id: UUID,
    user: OptionalCurrentUser,
    module: CommunityModule = Depends(get_optional_community_module),
) -> CommunityPost:
    try:
        return module.get_post(post_id, viewer_id=None if user is None else user.id)
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/community/posts",
    response_model=CommunityPost,
    status_code=status.HTTP_201_CREATED,
)
def publish_community_post(
    request: CommunityPublishInput,
    user: CurrentUser,
    module: CommunityModule = Depends(get_community_module),
) -> CommunityPost:
    try:
        return module.publish(user.id, request.trip_id, request.summary)
    except AppError as error:
        _raise_http(error)


@router.delete("/api/community/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_community_post(
    post_id: UUID,
    user: CurrentUser,
    module: CommunityModule = Depends(get_community_module),
) -> Response:
    try:
        module.withdraw(user.id, post_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AppError as error:
        _raise_http(error)
