from datetime import UTC, date, datetime
from importlib import import_module
from uuid import UUID

import pytest
from pydantic import ValidationError


ATTRACTION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
MERGED_INTO_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
CREATED_AT = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
RETIRED_AT = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)
REVIEWED_ON = date(2026, 8, 31)


def rag_models():
    """Resolve the feature module at test time so RED reports the missing module."""
    return import_module("app.rag_v2.models")


def destination(**overrides):
    values = {
        "destination_code": "350200",
        "destination_level": "prefecture_city",
        "destination_name": "厦门",
        "province_code": "350000",
        "province_name": "福建",
        "district_name": "思明区",
        "latitude": 24.4798,
        "longitude": 118.0894,
    }
    values.update(overrides)
    return rag_models().Destination(**values)


def source_provenance(**overrides):
    values = {
        "source_type": "official",
        "source_label": "厦门市文化和旅游局",
        "source_url": "https://example.gov.cn/xiamen",
        "reviewed_on": REVIEWED_ON,
    }
    values.update(overrides)
    return rag_models().SourceProvenance(**values)


def stable_attraction(**overrides):
    values = {
        "attraction_id": ATTRACTION_ID,
        "lifecycle_status": "active",
        "created_at": CREATED_AT,
        "retired_at": None,
        "merged_into_attraction_id": None,
    }
    values.update(overrides)
    return rag_models().StableAttraction(**values)


def attraction_metadata(**overrides):
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
    return rag_models().AttractionVersionMetadata(**values)


def semantic_section(**overrides):
    values = {
        "attraction_id": ATTRACTION_ID,
        "chunk_type": "overview",
        "content": "鼓浪屿位于厦门。",
        "source_label": "厦门市文化和旅游局",
        "source_url": "https://example.gov.cn/xiamen/gulangyu",
        "source_type": "official",
        "reviewed_on": REVIEWED_ON,
    }
    values.update(overrides)
    return rag_models().SemanticSection(**values)


@pytest.mark.parametrize(
    ("enum_name", "expected_values"),
    [
        (
            "DestinationLevel",
            ("province", "prefecture_city", "autonomous_prefecture", "county_city"),
        ),
        ("ChunkType", ("overview", "highlights", "transport", "visit_advice", "seasonal")),
        ("AttractionLifecycleStatus", ("active", "retired", "merged")),
        ("AttractionVersionStatus", ("included", "suppressed")),
        ("ChunkStatus", ("pending", "embedded", "failed", "excluded")),
    ],
)
def test_rag_v2_enums_expose_only_approved_values(enum_name, expected_values):
    enum_type = getattr(rag_models(), enum_name)

    assert tuple(member.value for member in enum_type) == expected_values


@pytest.mark.parametrize(
    ("enum_name", "value"),
    [
        ("DestinationLevel", "city"),
        ("DestinationLevel", "tourism_region"),
        ("ChunkType", "description"),
        ("AttractionLifecycleStatus", "deleted"),
        ("AttractionVersionStatus", "active"),
        ("ChunkStatus", "ready"),
    ],
)
def test_rag_v2_enums_reject_unknown_values(enum_name, value):
    enum_type = getattr(rag_models(), enum_name)

    with pytest.raises(ValueError):
        enum_type(value)


@pytest.mark.parametrize(
    ("destination_code", "destination_level", "destination_name"),
    [
        ("350200", "prefecture_city", "厦门"),
        ("350100", "prefecture_city", "福州"),
        ("532900", "autonomous_prefecture", "大理"),
        ("350000", "province", "福建"),
        ("530000", "province", "云南"),
    ],
)
def test_destination_accepts_approved_administrative_examples(
    destination_code, destination_level, destination_name
):
    model = destination(
        destination_code=destination_code,
        destination_level=destination_level,
        destination_name=destination_name,
    )

    assert model.destination_code == destination_code
    assert model.destination_level.value == destination_level
    assert model.destination_name == destination_name


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("destination_code", 350200),
        ("province_code", 350000),
    ],
)
def test_destination_rejects_integer_administrative_codes(field, value):
    with pytest.raises(ValidationError):
        destination(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("destination_code", "35020"),
        ("destination_code", "3502000"),
        ("destination_code", "ABC200"),
        ("province_code", "35020"),
        ("province_code", "3502000"),
        ("province_code", "ABC000"),
    ],
)
def test_destination_rejects_malformed_administrative_codes(field, value):
    with pytest.raises(ValidationError):
        destination(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latitude", -90),
        ("latitude", 90),
        ("longitude", -180),
        ("longitude", 180),
        ("latitude", None),
        ("longitude", None),
    ],
)
def test_destination_accepts_coordinate_boundaries_and_missing_coordinates(field, value):
    model = destination(**{field: value})

    assert getattr(model, field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latitude", -90.0001),
        ("latitude", 90.0001),
        ("longitude", -180.0001),
        ("longitude", 180.0001),
    ],
)
def test_destination_rejects_coordinates_outside_domain_bounds(field, value):
    with pytest.raises(ValidationError):
        destination(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("level", "prefecture_city"),
        ("name", "厦门"),
    ],
)
def test_destination_rejects_shorthand_fields(field, value):
    with pytest.raises(ValidationError):
        destination(**{field: value})


def test_embedding_profile_accepts_approved_document_profile():
    profile = rag_models().EmbeddingProfile(
        model="jina-embeddings-v3",
        task="retrieval.passage",
        dimensions=1024,
        input_schema_version="rag-v2-embedding-input-v1",
    )

    assert profile.model == "jina-embeddings-v3"
    assert profile.task.value == "retrieval.passage"
    assert profile.dimensions == 1024


@pytest.mark.parametrize("task", ["embedding", "retrieval.document", ""])
def test_embedding_profile_rejects_invalid_task(task):
    with pytest.raises(ValidationError):
        rag_models().EmbeddingProfile(
            model="jina-embeddings-v3",
            task=task,
            dimensions=1024,
            input_schema_version="rag-v2-embedding-input-v1",
        )


@pytest.mark.parametrize("dimensions", [0, -1])
def test_embedding_profile_rejects_non_positive_dimensions(dimensions):
    with pytest.raises(ValidationError):
        rag_models().EmbeddingProfile(
            model="jina-embeddings-v3",
            task="retrieval.passage",
            dimensions=dimensions,
            input_schema_version="rag-v2-embedding-input-v1",
        )


def test_source_provenance_accepts_required_source_fields():
    model = source_provenance()

    assert model.source_type == "official"
    assert model.source_label == "厦门市文化和旅游局"
    assert model.source_url == "https://example.gov.cn/xiamen"
    assert model.reviewed_on == REVIEWED_ON


@pytest.mark.parametrize("missing_field", ["source_type", "source_label", "source_url", "reviewed_on"])
def test_source_provenance_requires_all_source_fields(missing_field):
    values = {
        "source_type": "official",
        "source_label": "厦门市文化和旅游局",
        "source_url": "https://example.gov.cn/xiamen",
        "reviewed_on": REVIEWED_ON,
    }
    values.pop(missing_field)

    with pytest.raises(ValidationError):
        rag_models().SourceProvenance(**values)


def test_source_provenance_rejects_extra_fields():
    with pytest.raises(ValidationError):
        source_provenance(fetched_at=CREATED_AT)


def test_stable_attraction_accepts_active_lifecycle_invariant():
    model = stable_attraction()

    assert model.lifecycle_status.value == "active"
    assert model.retired_at is None
    assert model.merged_into_attraction_id is None


def test_stable_attraction_accepts_retired_lifecycle_invariant():
    model = stable_attraction(
        lifecycle_status="retired",
        retired_at=RETIRED_AT,
    )

    assert model.lifecycle_status.value == "retired"
    assert model.retired_at == RETIRED_AT
    assert model.merged_into_attraction_id is None


def test_stable_attraction_accepts_merged_lifecycle_invariant():
    model = stable_attraction(
        lifecycle_status="merged",
        merged_into_attraction_id=MERGED_INTO_ID,
    )

    assert model.lifecycle_status.value == "merged"
    assert model.merged_into_attraction_id == MERGED_INTO_ID


@pytest.mark.parametrize(
    "overrides",
    [
        {"lifecycle_status": "active", "retired_at": RETIRED_AT},
        {"lifecycle_status": "active", "merged_into_attraction_id": MERGED_INTO_ID},
        {"lifecycle_status": "retired", "retired_at": None},
        {"lifecycle_status": "retired", "merged_into_attraction_id": MERGED_INTO_ID},
        {"lifecycle_status": "merged", "merged_into_attraction_id": None},
    ],
)
def test_stable_attraction_rejects_inconsistent_lifecycle_fields(overrides):
    with pytest.raises(ValidationError):
        stable_attraction(**overrides)


def test_stable_attraction_rejects_self_merge():
    with pytest.raises(ValidationError):
        stable_attraction(
            lifecycle_status="merged",
            merged_into_attraction_id=ATTRACTION_ID,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_name", "鼓浪屿"),
        ("destination_code", "350200"),
        (
            "destination",
            {
                "destination_code": "350200",
                "destination_level": "prefecture_city",
                "destination_name": "厦门",
                "province_code": "350000",
                "province_name": "福建",
                "district_name": "思明区",
                "latitude": 24.4798,
                "longitude": 118.0894,
            },
        ),
        ("aliases", ("别名",)),
        ("category", "自然"),
        ("tags", ("海岛",)),
        ("coordinates", (118.0894, 24.4798)),
        (
            "provenance",
            {
                "source_type": "official",
                "source_label": "厦门市文化和旅游局",
                "source_url": "https://example.gov.cn/xiamen",
                "reviewed_on": REVIEWED_ON,
            },
        ),
        ("metadata_hash", "a" * 64),
    ],
)
def test_stable_attraction_rejects_versioned_metadata_leakage(field, value):
    with pytest.raises(ValidationError):
        stable_attraction(**{field: value})


def test_attraction_version_metadata_accepts_raw_versioned_metadata():
    model = attraction_metadata()

    assert model.attraction_id == ATTRACTION_ID
    assert model.canonical_name == "鼓浪屿"
    assert model.aliases == ("鼓浪嶼", "Gulangyu")
    assert model.destination.destination_code == "350200"
    assert model.destination.destination_level.value == "prefecture_city"
    assert model.tags == ("海岛", "人文")
    assert model.status.value == "included"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metadata_hash", "a" * 64),
        (
            "provenance",
            {
                "source_type": "official",
                "source_label": "厦门市文化和旅游局",
                "source_url": "https://example.gov.cn/xiamen",
                "reviewed_on": REVIEWED_ON,
            },
        ),
        ("version_label", "2026-09-01"),
        ("corpus_version_id", "cccccccc-cccc-cccc-cccc-cccccccccccc"),
        ("created_at", CREATED_AT),
        ("retired_at", RETIRED_AT),
    ],
)
def test_attraction_version_metadata_rejects_operational_or_computed_leakage(field, value):
    with pytest.raises(ValidationError):
        attraction_metadata(**{field: value})


def test_semantic_section_accepts_chunk_source_input():
    model = semantic_section()

    assert model.attraction_id == ATTRACTION_ID
    assert model.chunk_type.value == "overview"
    assert model.content == "鼓浪屿位于厦门。"
    assert model.reviewed_on == REVIEWED_ON


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_name", "鼓浪屿"),
        ("canonical_attraction_name", "鼓浪屿"),
        (
            "destination",
            {
                "destination_code": "350200",
                "destination_level": "prefecture_city",
                "destination_name": "厦门",
                "province_code": "350000",
                "province_name": "福建",
                "district_name": "思明区",
                "latitude": 24.4798,
                "longitude": 118.0894,
            },
        ),
        ("destination_code", "350200"),
        ("destination_name", "厦门"),
        ("embedding_profile", {"model": "jina-embeddings-v3"}),
        ("actual_vector", [0.1, 0.2]),
    ],
)
def test_semantic_section_rejects_attraction_or_embedding_context_duplication(field, value):
    with pytest.raises(ValidationError):
        semantic_section(**{field: value})


def test_rag_v2_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        rag_models().RagV2Schema.model_validate({"unexpected": "value"})
