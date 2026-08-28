from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.footprints.models import (
    CityRecord,
    DistrictBoundary,
    DistrictBoundaryView,
    FootprintCreate,
    FootprintUpdate,
    FootprintView,
    StoredFootprint,
)


FOOTPRINT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("11111111-1111-1111-1111-111111111111")
TIMESTAMP = datetime(2026, 8, 28, 8, 30, tzinfo=UTC)


def city() -> CityRecord:
    return CityRecord(
        city_adcode="350200",
        city_name="厦门市",
        province_adcode="350000",
        province_name="福建省",
        center=(118.09, 24.48),
    )


def test_create_accepts_canonical_city_and_date():
    request = FootprintCreate(city_adcode="350200", visited_at=date(2026, 8, 28))

    assert request.city_adcode == "350200"


@pytest.mark.parametrize("adcode", ["", "35020", "3502000", "xiamen", "3502 00"])
def test_create_rejects_noncanonical_adcode(adcode):
    with pytest.raises(ValidationError):
        FootprintCreate(city_adcode=adcode, visited_at=date(2026, 8, 28))


def test_public_footprint_view_excludes_private_owner():
    view = FootprintView(
        id=FOOTPRINT_ID,
        city_adcode="350200",
        city_name="厦门市",
        province_adcode="350000",
        province_name="福建省",
        center=(118.09, 24.48),
        visited_at=date(2026, 8, 28),
        created_at=TIMESTAMP,
        updated_at=TIMESTAMP,
    )

    assert "user_id" not in view.model_dump()
    with pytest.raises(ValidationError):
        FootprintView.model_validate({**view.model_dump(), "user_id": str(USER_ID)})


def test_stored_footprint_keeps_private_owner_and_boundary_models_are_strict():
    stored = StoredFootprint(
        id=FOOTPRINT_ID,
        user_id=USER_ID,
        city_adcode="350200",
        city_name="厦门市",
        province_adcode="350000",
        province_name="福建省",
        center=(118.09, 24.48),
        visited_at=date(2026, 8, 28),
        created_at=TIMESTAMP,
        updated_at=TIMESTAMP,
    )
    boundary = DistrictBoundary(
        city=city(),
        rings=[[(118.09, 24.48), (118.1, 24.48), (118.09, 24.48)]],
        fetched_at=TIMESTAMP,
    )
    view = DistrictBoundaryView(city=city(), rings=boundary.rings, status="fresh")

    assert stored.user_id == USER_ID
    assert view.status == "fresh"
    with pytest.raises(ValidationError):
        FootprintUpdate(visited_at=date(2026, 8, 28), city_adcode="350200")
