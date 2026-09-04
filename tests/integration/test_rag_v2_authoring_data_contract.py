from pathlib import Path

import yaml

from app.rag_v2.authoring import load_authoring_directory, load_authoring_file
from app.rag_v2.models import AttractionVersionStatus, ChunkType, DestinationLevel


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_DIR = PROJECT_ROOT / "app" / "rag_v2" / "content" / "production"
INCREMENTAL_PATH = (
    PROJECT_ROOT
    / "app"
    / "rag_v2"
    / "content"
    / "acceptance"
    / "incremental-v1.yaml"
)

PRODUCTION_SPECS = (
    (
        "xiamen.yaml",
        "350200",
        DestinationLevel.prefecture_city,
        "350000",
        "福建省",
        "xiamen.gulangyu",
        "00000000-0000-4000-8000-000000000101",
        "鼓浪屿",
    ),
    (
        "fuzhou.yaml",
        "350100",
        DestinationLevel.prefecture_city,
        "350000",
        "福建省",
        "fuzhou.sanfang-qixiang",
        "00000000-0000-4000-8000-000000000102",
        "三坊七巷",
    ),
    (
        "dali.yaml",
        "532900",
        DestinationLevel.autonomous_prefecture,
        "530000",
        "云南省",
        "dali.old-town",
        "00000000-0000-4000-8000-000000000103",
        "大理古城",
    ),
)

REQUIRED_SECTION_TYPES = (
    ChunkType.overview,
    ChunkType.highlights,
    ChunkType.transport,
    ChunkType.visit_advice,
    ChunkType.seasonal,
)
DERIVED_FIELDS = frozenset(
    {
        "chunk_key",
        "content_hash",
        "embedding_input_hash",
        "metadata_hash",
        "manifest_hash",
        "embedding",
    }
)


def _production_paths() -> tuple[Path, ...]:
    return tuple(PRODUCTION_DIR / spec[0] for spec in PRODUCTION_SPECS)


def _sorted_production_specs():
    return tuple(sorted(PRODUCTION_SPECS, key=lambda spec: spec[0]))


def _load_production_documents():
    paths = _production_paths()
    for path in paths:
        assert path.exists(), f"expected production authoring artifact does not exist: {path}"

    assert tuple(
        sorted(PRODUCTION_DIR.glob("*.yaml"), key=lambda path: path.name)
    ) == tuple(sorted(paths, key=lambda path: path.name))
    return load_authoring_directory(PRODUCTION_DIR)


def _assert_no_derived_fields(value: object) -> None:
    if isinstance(value, dict):
        assert not DERIVED_FIELDS.intersection(value)
        for child in value.values():
            _assert_no_derived_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_derived_fields(child)


def test_production_authoring_files_exist_and_have_exact_destinations() -> None:
    documents = _load_production_documents()

    assert tuple(
        (
            document.destination.destination_code,
            document.destination.destination_level,
            document.destination.province_code,
            document.destination.province_name,
        )
        for document in documents
    ) == tuple(
        (spec[1], spec[2], spec[3], spec[4])
        for spec in _sorted_production_specs()
    )


def test_each_production_file_has_five_included_sections() -> None:
    documents = _load_production_documents()

    for document in documents:
        assert len(document.attractions) == 1
        attraction = document.attractions[0]
        assert attraction.metadata.status is AttractionVersionStatus.included
        assert tuple(section.chunk_type for section in attraction.sections) == (
            *REQUIRED_SECTION_TYPES,
        )
        assert all(section.source_label.strip() for section in attraction.sections)
        assert all(section.source_type == "official" for section in attraction.sections)
        assert all(section.source_url.startswith("https://") for section in attraction.sections)
        assert all(section.reviewed_on.isoformat() == "2026-09-01" for section in attraction.sections)


def test_production_data_has_unique_registry_and_attraction_ids() -> None:
    documents = _load_production_documents()
    observed = tuple(
        (
            document.attractions[0].registry_key,
            str(document.attractions[0].metadata.attraction_id),
            document.attractions[0].metadata.canonical_name,
        )
        for document in documents
    )

    assert observed == tuple(
        (spec[5], spec[6], spec[7]) for spec in _sorted_production_specs()
    )
    assert len({registry_key for registry_key, _, _ in observed}) == 3
    assert len({attraction_id for _, attraction_id, _ in observed}) == 3


def test_incremental_fixture_contains_one_in_memory_patch() -> None:
    assert INCREMENTAL_PATH.exists(), (
        "expected incremental authoring fixture does not exist"
    )
    payload = yaml.safe_load(INCREMENTAL_PATH.read_text(encoding="utf-8"))

    assert payload == {
        "schema_version": "rag-v2-authoring-patch-v1",
        "target": {
            "destination_code": "350200",
            "registry_key": "xiamen.gulangyu",
            "section": "visit_advice",
        },
        "replacement_content": (
            "登岛前核对官方船班与码头安排，旺时段为候船和步行保留余量。"
        ),
        "expectations": {
            "changed_sections": ["visit_advice"],
            "unchanged_sections": [
                "overview",
                "highlights",
                "transport",
                "seasonal",
            ],
        },
    }
    assert len(payload["expectations"]["changed_sections"]) == 1


def test_authoring_data_does_not_hand_author_program_derived_fields() -> None:
    paths = (*_production_paths(), INCREMENTAL_PATH)
    for path in paths:
        assert path.exists(), f"expected authoring artifact does not exist: {path}"
        _assert_no_derived_fields(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
