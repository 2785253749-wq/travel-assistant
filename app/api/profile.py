from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import CurrentUser
from app.composition import get_profile_module
from app.core.errors import AppError
from app.profile.models import ProfileInput, UserProfile
from app.profile.service import ProfileModule


router = APIRouter(tags=["profile"])


def _raise_http(error: AppError) -> None:
    if error.code == "PROFILE_VALIDATION_FAILED":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "PROFILE_VALIDATION_FAILED",
                "message": "Profile request validation failed",
            },
        ) from error
    if error.code == "PROFILE_UNAVAILABLE":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROFILE_UNAVAILABLE",
                "message": "Profile service unavailable",
            },
        ) from error
    raise error


@router.get("/api/profile", response_model=UserProfile)
def get_profile(
    user: CurrentUser, module: ProfileModule = Depends(get_profile_module)
) -> UserProfile:
    try:
        return module.get_profile(user)
    except AppError as error:
        _raise_http(error)


@router.put("/api/profile", response_model=UserProfile)
def replace_profile(
    request: ProfileInput,
    user: CurrentUser,
    module: ProfileModule = Depends(get_profile_module),
) -> UserProfile:
    try:
        return module.replace_profile(user, request)
    except AppError as error:
        _raise_http(error)
