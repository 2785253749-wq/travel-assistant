from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from app.rag_v2.authoring import (
    AuthoringDocument,
    load_authoring_directory,
    load_authoring_file,
)
from app.rag_v2.models import (
    AttractionVersionMetadata,
    ChunkType,
    Destination,
    SemanticSection,
)


ATTRACTION_ID = "00000000-0000-4000-8000-000000000101"
OTHER_ATTRACTION_ID = "00000000-0000-4000-8000-000000000102"
REVIEWED_ON = "2026-09-01"


def _section(content: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "content": content,
        "source_label": "厦门市文化和旅游局",
        "source_url": "https://wlj.xm.gov.cn/",
        "source_type": "official",
        "reviewed_on": REVIEWED_ON,
    }
    values.update(overrides)
    return values


def _valid_payload(
    *,
    attraction_id: str = ATTRACTION_ID,
    registry_key: str = "xiamen.gulangyu",
    destination_name: str = "厦门市",
) -> dict[str, object]:
    return {
        "schema_version": "rag-v2-authoring-v1",
        "destination": {
            "destination_code": "350200",
            "destination_level": "prefecture_city",
            "destination_name": destination_name,
            "province_code": "350000",
            "province_name": "福建省",
        },
        "attractions": [
            {
                "registry_key": registry_key,
                "attraction_id": attraction_id,
                "canonical_name": "鼓浪屿",
                "aliases": ["鼓浪屿"],
                "category": "海岛景区",
                "tags": ["海岛", "步行"],
                "status": "included",
                "sections": {
                    "overview": _section("概览文本。"),
                    "highlights": _section("亮点文本。"),
                    "transport": _section("交通文本。"),
                    "visit_advice": _section("建议文本。"),
                    "seasonal": _section("季节文本。"),
                },
            }
        ],
    }


def _write_yaml(tmp_path: Path, payload: dict[str, object], name: str = "authoring.yaml") -> Path:
    path = tmp_path / name
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_load_authoring_file_maps_existing_stage10b_models(tmp_path: Path) -> None:
    document = load_authoring_file(_write_yaml(tmp_path, _valid_payload()))

    assert isinstance(document, AuthoringDocument)
    assert document.schema_version == "rag-v2-authoring-v1"
    assert isinstance(document.destination, Destination)
    assert len(document.attractions) == 1

    attraction = document.attractions[0]
    assert isinstance(attraction.metadata, AttractionVersionMetadata)
    assert attraction.metadata.attraction_id == UUID(ATTRACTION_ID)
    assert isinstance(attraction.sections, tuple)
    assert all(isinstance(section, SemanticSection) for section in attraction.sections)
    assert tuple(section.chunk_type for section in attraction.sections) == (
        ChunkType.overview,
        ChunkType.highlights,
        ChunkType.transport,
        ChunkType.visit_advice,
        ChunkType.seasonal,
    )


def test_parser_rejects_wrong_schema_version(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["schema_version"] = "rag-v2-authoring-v0"

    with pytest.raises(ValueError):
        load_authoring_file(_write_yaml(tmp_path, payload))


def test_parser_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["manifest_hash"] = "a" * 64
    sections = payload["attractions"][0]["sections"]  # type: ignore[index]
    sections["overview"]["content_hash"] = "b" * 64  # type: ignore[index]

    with pytest.raises(ValueError):
        load_authoring_file(_write_yaml(tmp_path, payload))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("destination_code", "35020"),
        ("destination_level", "city"),
    ],
)
def test_parser_rejects_invalid_destination_profile(
    tmp_path: Path, field: str, value: str
) -> None:
    payload = _valid_payload()
    payload["destination"][field] = value  # type: ignore[index]

    with pytest.raises(ValueError):
        load_authoring_file(_write_yaml(tmp_path, payload))


def test_parser_rejects_duplicate_registry_keys(tmp_path: Path) -> None:
    payload = _valid_payload()
    second = deepcopy(payload["attractions"][0])  # type: ignore[index]
    second["attraction_id"] = OTHER_ATTRACTION_ID
    payload["attractions"].append(second)  # type: ignore[index]

    with pytest.raises(ValueError):
        load_authoring_file(_write_yaml(tmp_path, payload))


def test_parser_requires_five_sections_for_included_attraction(tmp_path: Path) -> None:
    payload = _valid_payload()
    del payload["attractions"][0]["sections"]["seasonal"]  # type: ignore[index]

    with pytest.raises(ValueError):
        load_authoring_file(_write_yaml(tmp_path, payload))


def test_parser_allows_zero_sections_for_suppressed_attraction(tmp_path: Path) -> None:
    payload = _valid_payload()
    attraction = payload["attractions"][0]  # type: ignore[index]
    attraction["status"] = "suppressed"
    attraction["sections"] = {}

    document = load_authoring_file(_write_yaml(tmp_path, payload))

    assert document.attractions[0].metadata.status == "suppressed"
    assert document.attractions[0].sections == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_label", "   "),
        ("source_type", ""),
        ("reviewed_on", None),
        ("source_url", "http://wlj.xm.gov.cn/"),
        ("source_url", "https:///missing-host"),
        ("source_url", "https://user:pass@wlj.xm.gov.cn/"),
    ],
)
def test_parser_rejects_incomplete_https_provenance(
    tmp_path: Path, field: str, value: object
) -> None:
    payload = _valid_payload()
    payload["attractions"][0]["sections"]["overview"][field] = value  # type: ignore[index]

    with pytest.raises(ValueError):
        load_authoring_file(_write_yaml(tmp_path, payload))


def test_parser_rejects_duplicate_section_keys(tmp_path: Path) -> None:
    raw = """
schema_version: rag-v2-authoring-v1
destination:
  destination_code: '350200'
  destination_level: prefecture_city
  destination_name: 厦门市
  province_code: '350000'
  province_name: 福建省
attractions:
  - registry_key: xiamen.gulangyu
    attraction_id: 00000000-0000-4000-8000-000000000101
    canonical_name: 鼓浪屿
    aliases: [鼓浪屿]
    category: 海岛景区
    tags: [海岛, 步行]
    status: included
    sections:
      overview:
        content: 第一份概览。
        source_label: 厦门市文化和旅游局
        source_url: https://wlj.xm.gov.cn/
        source_type: official
        reviewed_on: '2026-09-01'
      overview:
        content: 第二份概览。
        source_label: 厦门市文化和旅游局
        source_url: https://wlj.xm.gov.cn/
        source_type: official
        reviewed_on: '2026-09-01'
"""
    path = tmp_path / "duplicate-sections.yaml"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError):
        load_authoring_file(path)


def test_load_authoring_directory_is_path_sorted(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        _valid_payload(
            attraction_id=ATTRACTION_ID,
            registry_key="xiamen.gulangyu",
            destination_name="厦门市",
        ),
        name="z-last.yaml",
    )
    _write_yaml(
        tmp_path,
        _valid_payload(
            attraction_id=OTHER_ATTRACTION_ID,
            registry_key="quanzhou.kaiyuan",
            destination_name="泉州市",
        ),
        name="a-first.yaml",
    )

    documents = load_authoring_directory(tmp_path)

    assert tuple(document.destination.destination_name for document in documents) == (
        "泉州市",
        "厦门市",
    )
