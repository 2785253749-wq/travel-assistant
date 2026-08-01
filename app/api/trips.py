from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import CurrentUser
from app.core.errors import AppError
from app.schemas import TravelProfile
from app.trips.models import Trip
from app.trips.service import TripService, get_public_trip_service, get_trip_service


router = APIRouter(tags=["trips"])


class CreateTripRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    profile: TravelProfile = Field(default_factory=TravelProfile)


class UpdateTripRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = Field(default=None, min_length=1, max_length=100)
    profile: TravelProfile | None = None
    status: str | None = None
    itinerary: dict[str, Any] | None = None


class ShareRequest(BaseModel):
    expires_in_days: int = Field(default=30, ge=1, le=365)


class ResolveShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=512)


def _trip_response(trip: Trip) -> dict[str, Any]:
    return {
        "id": str(trip.id),
        "user_id": str(trip.user_id),
        "title": trip.title,
        "status": trip.status,
        "profile": trip.profile.model_dump(mode="json"),
        "itinerary": trip.itinerary,
        "created_at": trip.created_at.isoformat() if trip.created_at else None,
        "updated_at": trip.updated_at.isoformat() if trip.updated_at else None,
    }


def _raise_http(error: AppError) -> None:
    code = status.HTTP_404_NOT_FOUND if error.code in {"TRIP_NOT_FOUND", "SHARE_NOT_FOUND"} else status.HTTP_422_UNPROCESSABLE_ENTITY
    raise HTTPException(status_code=code, detail={"code": error.code, "message": error.message}) from error


@router.post("/api/trips", status_code=status.HTTP_201_CREATED)
def create_trip(request: CreateTripRequest, user: CurrentUser, service: TripService = Depends(get_trip_service)):
    return _trip_response(service.create_trip(user.id, request.profile))


@router.get("/api/trips")
def list_trips(user: CurrentUser, service: TripService = Depends(get_trip_service)):
    return [_trip_response(trip) for trip in service.list_trips(user.id)]


@router.get("/api/trips/{trip_id}")
def get_trip(trip_id: UUID, user: CurrentUser, service: TripService = Depends(get_trip_service)):
    try:
        return _trip_response(service.get_trip(user.id, trip_id))
    except AppError as error:
        _raise_http(error)


@router.patch("/api/trips/{trip_id}")
def update_trip(trip_id: UUID, request: UpdateTripRequest, user: CurrentUser, service: TripService = Depends(get_trip_service)):
    try:
        return _trip_response(service.update_trip(user.id, trip_id, **request.model_dump(exclude_none=True)))
    except AppError as error:
        _raise_http(error)


@router.delete("/api/trips/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trip(trip_id: UUID, user: CurrentUser, service: TripService = Depends(get_trip_service)) -> Response:
    try:
        service.delete_trip(user.id, trip_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AppError as error:
        _raise_http(error)


@router.post("/api/trips/{trip_id}/share", status_code=status.HTTP_201_CREATED)
def create_share_link(trip_id: UUID, user: CurrentUser, service: TripService = Depends(get_trip_service), request: ShareRequest | None = None):
    try:
        expires_in_days = request.expires_in_days if request is not None else 30
        return {"token": service.create_share_link(user.id, trip_id, expires_in_days)}
    except AppError as error:
        _raise_http(error)


@router.delete("/api/trips/{trip_id}/share", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share_link(trip_id: UUID, user: CurrentUser, service: TripService = Depends(get_trip_service)) -> Response:
    try:
        service.revoke_share_link(user.id, trip_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AppError as error:
        _raise_http(error)


@router.post("/api/shared/resolve")
def get_shared_trip(request: ResolveShareRequest, service: TripService = Depends(get_public_trip_service)):
    try:
        return service.get_shared_trip(request.token)
    except AppError as error:
        _raise_http(error)
