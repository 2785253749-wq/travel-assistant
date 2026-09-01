from datetime import UTC, datetime
from importlib import import_module
import inspect
from uuid import UUID

import pytest

from app.rag_v2.models import AttractionVersionMetadata, Destination, StableAttraction


ATTRACTION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TARGET_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
DESCENDANT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CREATED_AT = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
RETIRED_AT = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)


def rag_identity():
    """Resolve the future identity module only when a RED test executes."""
    return import_module("app.rag_v2.identity")


def destination():
    return Destination(
        destination_code="350200",
        destination_level="prefecture_city",
        destination_name="厦门",
        province_code="350000",
        province_name="福建",
        district_name="思明区",
        latitude=24.4798,
        longitude=118.0894,
    )


def metadata(**overrides):
    values = {
        "attraction_id": ATTRACTION_ID,
        "canonical_name": "鼓浪屿",
        "aliases": ("鼓浪嶼", "Gulangyu"),
        "destination": destination(),
        "category": "历史文化",
        "tags": ("海岛", "人文"),
        "status": "included",
    }
    values.update(overrides)
    return AttractionVersionMetadata(**values)


def stable_attraction(**overrides):
    values = {
        "attraction_id": ATTRACTION_ID,
        "lifecycle_status": "active",
        "created_at": CREATED_AT,
        "retired_at": None,
        "merged_into_attraction_id": None,
    }
    values.update(overrides)
    return StableAttraction(**values)


class InMemoryIdentitySource:
    """Test-only registry fake implementing the approved identity seam."""

    def __init__(self, initial=None):
        self._identities = dict(initial or {})

    def resolve(self, registry_key):
        return self._identities.get(registry_key)

    def allocate(self, registry_key):
        if registry_key in self._identities:
            raise ValueError("registry key already has an allocated identity")
        identity = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
        self._identities[registry_key] = identity
        return identity


def test_identity_module_exposes_the_approved_public_api():
    identity = rag_identity()

    assert hasattr(identity, "AttractionIdentitySource")
    assert hasattr(identity, "rename_attraction")
    assert hasattr(identity, "merge_attraction")
    assert hasattr(identity, "retire_attraction")
    assert hasattr(identity, "handle_corpus_absence")

    assert tuple(inspect.signature(identity.AttractionIdentitySource.resolve).parameters) == (
        "self",
        "registry_key",
    )
    assert tuple(inspect.signature(identity.AttractionIdentitySource.allocate).parameters) == (
        "self",
        "registry_key",
    )
    assert tuple(inspect.signature(identity.rename_attraction).parameters) == (
        "metadata",
        "canonical_name",
    )
    assert tuple(inspect.signature(identity.merge_attraction).parameters) == (
        "source",
        "target",
        "known_descendants",
    )
    assert tuple(inspect.signature(identity.retire_attraction).parameters) == (
        "attraction",
        "retired_at",
    )
    assert tuple(inspect.signature(identity.handle_corpus_absence).parameters) == (
        "attraction",
        "known_in_source",
    )


def test_existing_registry_key_resolves_to_the_same_uuid():
    registry_key = "cn|350200|gulangyu"
    source = InMemoryIdentitySource({registry_key: ATTRACTION_ID})

    assert source.resolve(registry_key) == ATTRACTION_ID
    assert source.resolve(registry_key) == source.resolve(registry_key)


def test_new_registry_key_requires_explicit_allocation_before_resolution():
    source = InMemoryIdentitySource()
    registry_key = "cn|350200|sunlight-rock"

    assert source.resolve(registry_key) is None
    allocated = source.allocate(registry_key)

    assert allocated == UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    assert source.resolve(registry_key) == allocated


def test_rename_changes_versioned_name_and_preserves_stable_attraction_id():
    identity = rag_identity()
    original = metadata()

    renamed = identity.rename_attraction(original, canonical_name="鼓浪屿风景名胜区")

    assert renamed.canonical_name == "鼓浪屿风景名胜区"
    assert renamed.attraction_id == original.attraction_id == ATTRACTION_ID
    assert renamed.destination == original.destination
    assert original.canonical_name == "鼓浪屿"


def test_merge_records_target_on_source_and_preserves_source_identity():
    identity = rag_identity()
    source = stable_attraction()
    target = stable_attraction(attraction_id=TARGET_ID)

    merged = identity.merge_attraction(source, target, known_descendants=())

    assert merged.attraction_id == ATTRACTION_ID
    assert merged.lifecycle_status.value == "merged"
    assert merged.merged_into_attraction_id == TARGET_ID
    assert merged.retired_at is None
    assert source.lifecycle_status.value == "active"
    assert source.merged_into_attraction_id is None


def test_merge_rejects_missing_target():
    identity = rag_identity()

    with pytest.raises(ValueError):
        identity.merge_attraction(stable_attraction(), None, known_descendants=())


def test_merge_rejects_self_merge():
    identity = rag_identity()
    source = stable_attraction()

    with pytest.raises(ValueError):
        identity.merge_attraction(source, source, known_descendants=())


def test_merge_rejects_target_that_is_a_known_source_descendant():
    identity = rag_identity()
    source = stable_attraction()
    target = stable_attraction(attraction_id=TARGET_ID)

    with pytest.raises(ValueError):
        identity.merge_attraction(
            source,
            target,
            known_descendants=(TARGET_ID, DESCENDANT_ID),
        )


def test_retirement_is_explicit_and_records_retired_at():
    identity = rag_identity()
    source = stable_attraction()

    retired = identity.retire_attraction(source, retired_at=RETIRED_AT)

    assert retired.attraction_id == ATTRACTION_ID
    assert retired.lifecycle_status.value == "retired"
    assert retired.retired_at == RETIRED_AT
    assert retired.merged_into_attraction_id is None
    assert source.lifecycle_status.value == "active"
    assert source.retired_at is None


def test_corpus_absence_does_not_retire_stable_attraction():
    identity = rag_identity()
    source = stable_attraction()

    result = identity.handle_corpus_absence(source, known_in_source=False)

    assert result.attraction_id == ATTRACTION_ID
    assert result.lifecycle_status.value == "active"
    assert result.retired_at is None
    assert result.merged_into_attraction_id is None
    assert source.lifecycle_status.value == "active"
