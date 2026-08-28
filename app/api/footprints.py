from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.auth import CurrentUser
from app.composition import get_footprint_module
from app.core.errors import AppError
from app.footprints.models import FootprintCreate, FootprintUpdate, FootprintView
from app.footprints.service import FootprintModule


router = APIRouter(tags=["footprints"])


def _raise_http(error: AppError) -> None:
    responses = {
        "FOOTPRINT_VALIDATION_FAILED": (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Footprint request validation failed",
        ),
        "FOOTPRINT_CITY_NOT_FOUND": (
            status.HTTP_404_NOT_FOUND,
            "Footprint city not found",
        ),
        "FOOTPRINT_NOT_FOUND": (
            status.HTTP_404_NOT_FOUND,
            "Footprint not found",
        ),
        "FOOTPRINT_UNAVAILABLE": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Footprint service unavailable",
        ),
    }
    response = responses.get(error.code)
    if response is None:
        raise error
    response_status, message = response
    raise HTTPException(
        status_code=response_status,
        detail={"code": error.code, "message": message},
    ) from error


@router.get("/api/footprints", response_model=list[FootprintView])
def list_footprints(
    user: CurrentUser, module: FootprintModule = Depends(get_footprint_module)
) -> list[FootprintView]:
    try:
        return module.list(user.id)
    except AppError as error:
        _raise_http(error)


@router.post(
    "/api/footprints",
    response_model=FootprintView,
    status_code=status.HTTP_201_CREATED,
)
def add_footprint(
    request: FootprintCreate,
    user: CurrentUser,
    module: FootprintModule = Depends(get_footprint_module),
) -> FootprintView:
    try:
        return module.add(user.id, request)
    except AppError as error:
        _raise_http(error)


@router.patch("/api/footprints/{footprint_id}", response_model=FootprintView)
def update_footprint(
    footprint_id: UUID,
    request: FootprintUpdate,
    user: CurrentUser,
    module: FootprintModule = Depends(get_footprint_module),
) -> FootprintView:
    try:
        return module.update(user.id, footprint_id, request)
    except AppError as error:
        _raise_http(error)


@router.delete("/api/footprints/{footprint_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_footprint(
    footprint_id: UUID,
    user: CurrentUser,
    module: FootprintModule = Depends(get_footprint_module),
) -> Response:
    try:
        module.remove(user.id, footprint_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AppError as error:
        _raise_http(error)
