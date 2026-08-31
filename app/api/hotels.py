from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.composition import get_hotel_service
from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
)
from app.hotels.service import HotelService
from app.providers.baidu_hotel import BaiduHotelProviderError


router = APIRouter(prefix="/api/hotels", tags=["hotels"])


def _raise_provider_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "HOTEL_PROVIDER_UNAVAILABLE",
            "message": "Hotel service is temporarily unavailable",
        },
    ) from None


def _raise_invalid_request() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "REQUEST_INVALID", "message": "Request validation failed"},
    )


def get_hotel_service_dependency() -> HotelService:
    """Resolve the composed service while keeping provider details out of HTTP responses."""
    try:
        return get_hotel_service()
    except BaiduHotelProviderError:
        _raise_provider_unavailable()


@router.get("/search", response_model=HotelSearchResult)
def search_hotels(
    request: Annotated[HotelSearchRequest, Query()],
    service: HotelService = Depends(get_hotel_service_dependency),
) -> HotelSearchResult:
    try:
        return service.search_city(request)
    except BaiduHotelProviderError:
        _raise_provider_unavailable()


@router.get("/nearby", response_model=HotelSearchResult)
def search_nearby_hotels(
    request: Annotated[HotelNearbySearchRequest, Query()],
    service: HotelService = Depends(get_hotel_service_dependency),
) -> HotelSearchResult:
    try:
        return service.search_nearby(request)
    except BaiduHotelProviderError:
        _raise_provider_unavailable()


@router.get("/{hotel_id}", response_model=HotelDetail)
def get_hotel_detail(
    hotel_id: str,
    service: HotelService = Depends(get_hotel_service_dependency),
) -> HotelDetail:
    try:
        detail = service.get_detail(hotel_id)
    except ValueError:
        _raise_invalid_request()
    except BaiduHotelProviderError:
        _raise_provider_unavailable()

    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "HOTEL_NOT_FOUND", "message": "Hotel not found"},
        )
    return detail
