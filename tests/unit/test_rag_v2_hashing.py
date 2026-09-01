from hashlib import sha256
from importlib import import_module
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.rag_v2.models import AttractionVersionMetadata, Destination


ATTRACTION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_ATTRACTION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def rag_hashing():
    """Resolve the future hashing module only when a RED test executes."""
    return import_module("app.rag_v2.hashing")


def destination(**overrides):
    values = {
        "destination_code": "350200",
        "destination_level": "prefecture_city",
        "destination_name": "厦门",
        "province_code": "350000",
        "province_name": "福建",
        "district_name": "思明区",
        "latitude": 24.445676,
        "longitude": 118.065315,
    }
    values.update(overrides)
    return Destination(**values)


def metadata(**overrides):
    values = {
        "attraction_id": ATTRACTION_ID,
        "canonical_name": "鼓浪屿",
        "aliases": ("鼓浪屿", "Ｇｕｌａｎｇｙｕ", "Gulangyu", "Gulangyu"),
        "destination": destination(),
        "category": "历史文化",
        "tags": ("海岛", "人文", "海岛"),
        "status": "included",
    }
    values.update(overrides)
    return AttractionVersionMetadata(**values)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  ＡＢＣ　１２３  ", "ABC 123"),
        ("  保留，。！？  ", "保留,。!?"),
        ("\t第一行\t", "第一行"),
    ],
)
def test_normalize_text_applies_nfkc_and_preserves_semantic_text(value, expected):
    assert rag_hashing().normalize_text(value) == expected


def test_normalize_content_normalizes_line_endings_whitespace_and_blank_lines():
    value = "  第一\t\t行  \r\n\r\n 第二  \r第三\r\n\n 第四  "

    assert rag_hashing().normalize_content(value) == "第一 行\n第二\n第三\n第四"


@pytest.mark.parametrize(
    "value",
    [
        "\r\n第一\r第二\n第三\r\n",
        "  多   个\t空格  \n\n下一行  ",
        "ＡＢＣ\r\n\r\n保留标点！",
    ],
)
def test_normalization_is_deterministic(value):
    hashing = rag_hashing()

    assert hashing.normalize_text(value) == hashing.normalize_text(value)
    assert hashing.normalize_content(value) == hashing.normalize_content(value)


def test_content_hash_is_sha256_of_normalized_utf8_content():
    value = "  ＡＢＣ\r\n\t旅行。  "
    normalized = "ABC\n旅行。"
    expected = sha256(normalized.encode("utf-8")).hexdigest()

    assert rag_hashing().content_hash(value) == expected
    assert len(expected) == 64
    assert expected == expected.lower()


def test_content_hash_is_invariant_for_normalization_equivalent_inputs():
    hashing = rag_hashing()

    assert hashing.content_hash("ＡＢＣ\r\n旅行。") == hashing.content_hash("ABC\n旅行。")


def test_content_hash_changes_for_meaningful_content_change():
    hashing = rag_hashing()

    assert hashing.content_hash("鼓浪屿位于厦门。") != hashing.content_hash("鼓浪屿位于福州。")


def test_canonical_metadata_json_uses_fixed_compact_unicode_field_order():
    expected = (
        '{"canonical_name":"鼓浪屿","aliases":["Gulangyu","鼓浪屿"],'
        '"destination_code":"350200","destination_level":"prefecture_city",'
        '"destination_name":"厦门","province_code":"350000","province_name":"福建",'
        '"district_name":"思明区","category":"历史文化","tags":["人文","海岛"],'
        '"latitude":"24.4456760","longitude":"118.0653150","status":"included"}'
    )

    assert rag_hashing().canonical_metadata_json(metadata()) == expected


def test_aliases_are_normalized_deduplicated_and_sorted_before_canonicalization():
    hashing = rag_hashing()
    first = metadata(aliases=("鼓浪屿", "Gulangyu", " 鼓浪屿 ", "Ｇｕｌａｎｇｙｕ"))
    second = metadata(aliases=("Gulangyu", "鼓浪屿"))

    assert hashing.canonical_metadata_json(first) == hashing.canonical_metadata_json(second)
    assert hashing.metadata_hash(first) == hashing.metadata_hash(second)


def test_tags_are_normalized_deduplicated_and_sorted_before_canonicalization():
    hashing = rag_hashing()
    first = metadata(tags=(" 海岛 ", "人文", "海岛"))
    second = metadata(tags=("人文", "海岛"))

    assert hashing.canonical_metadata_json(first) == hashing.canonical_metadata_json(second)
    assert hashing.metadata_hash(first) == hashing.metadata_hash(second)


def test_normalized_empty_alias_and_tag_items_remain_in_the_canonical_collection():
    canonical = rag_hashing().canonical_metadata_json(
        metadata(aliases=("", " 鼓浪屿 "), tags=("", " 海岛 "))
    )

    assert '"aliases":["","鼓浪屿"]' in canonical
    assert '"tags":["","海岛"]' in canonical


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected_latitude", "expected_longitude"),
    [
        (24.445676, 118.065315, '"latitude":"24.4456760"', '"longitude":"118.0653150"'),
        (24, 118, '"latitude":"24.0000000"', '"longitude":"118.0000000"'),
        (24.4, 118.0, '"latitude":"24.4000000"', '"longitude":"118.0000000"'),
    ],
)
def test_coordinates_use_fixed_seven_place_decimal_strings(
    latitude, longitude, expected_latitude, expected_longitude
):
    canonical = rag_hashing().canonical_metadata_json(
        metadata(destination=destination(latitude=latitude, longitude=longitude))
    )

    assert expected_latitude in canonical
    assert expected_longitude in canonical


def test_missing_optional_metadata_is_serialized_as_json_null():
    canonical = rag_hashing().canonical_metadata_json(
        metadata(destination=destination(district_name=None, latitude=None, longitude=None), category=None)
    )

    assert '"district_name":null' in canonical
    assert '"category":null' in canonical
    assert '"latitude":null' in canonical
    assert '"longitude":null' in canonical
    assert '"district_name":""' not in canonical


def test_enum_fields_are_serialized_by_value():
    canonical = rag_hashing().canonical_metadata_json(metadata())

    assert '"destination_level":"prefecture_city"' in canonical
    assert '"status":"included"' in canonical
    assert "DestinationLevel." not in canonical
    assert "AttractionVersionStatus." not in canonical


def test_attraction_id_is_excluded_from_metadata_canonicalization_and_hash():
    hashing = rag_hashing()
    first = metadata(attraction_id=ATTRACTION_ID)
    second = metadata(attraction_id=OTHER_ATTRACTION_ID)

    assert hashing.canonical_metadata_json(first) == hashing.canonical_metadata_json(second)
    assert hashing.metadata_hash(first) == hashing.metadata_hash(second)
    assert str(ATTRACTION_ID) not in hashing.canonical_metadata_json(first)


def test_metadata_hash_is_sha256_of_canonical_metadata_json():
    hashing = rag_hashing()
    canonical = hashing.canonical_metadata_json(metadata())
    expected = sha256(canonical.encode("utf-8")).hexdigest()

    assert hashing.metadata_hash(metadata()) == expected
    assert len(expected) == 64
    assert expected == expected.lower()


def test_normalization_equivalent_metadata_has_the_same_hash():
    hashing = rag_hashing()
    first = metadata(canonical_name=" 鼓浪屿 ", category=" 历史文化 ")
    second = metadata(canonical_name="鼓浪屿", category="历史文化")

    assert hashing.metadata_hash(first) == hashing.metadata_hash(second)


@pytest.mark.parametrize(
    "changed_metadata",
    [
        metadata(canonical_name="日光岩"),
        metadata(aliases=(" 鼓浪屿 ", "厦门岛")),
        metadata(destination=destination(destination_code="350100")),
        metadata(destination=destination(destination_level="province")),
        metadata(destination=destination(destination_name="福州")),
        metadata(destination=destination(province_code="350100")),
        metadata(destination=destination(province_name="浙江")),
        metadata(destination=destination(district_name="湖里区")),
        metadata(category="自然"),
        metadata(tags=("山岳",)),
        metadata(destination=destination(latitude=24.445677)),
        metadata(destination=destination(longitude=118.065316)),
        metadata(status="suppressed"),
    ],
)
def test_metadata_hash_changes_for_every_meaningful_metadata_change(changed_metadata):
    hashing = rag_hashing()

    assert hashing.metadata_hash(changed_metadata) != hashing.metadata_hash(metadata())


def test_metadata_hash_does_not_accept_or_include_provenance_fields():
    with pytest.raises(ValidationError):
        metadata(source_label="厦门市文化和旅游局")


def test_hashing_does_not_mutate_the_input_model():
    hashing = rag_hashing()
    value = metadata()
    before = value.model_dump()

    hashing.canonical_metadata_json(value)
    hashing.metadata_hash(value)

    assert value.model_dump() == before
