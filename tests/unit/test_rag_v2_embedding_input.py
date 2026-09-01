from hashlib import sha256
from importlib import import_module

import pytest
from pydantic import ValidationError


SCHEMA_VERSION = "rag-v2-embedding-input-v1"


def rag_models():
    """Resolve Task 3's future model only when a RED test executes."""
    return import_module("app.rag_v2.models")


def rag_hashing():
    """Resolve Task 3's future hashing APIs only when a RED test executes."""
    return import_module("app.rag_v2.hashing")


def build_input(**overrides):
    values = {
        "canonical_attraction_name": "鼓浪屿",
        "destination_name": "厦门",
        "destination_code": "350200",
        "destination_level": rag_models().DestinationLevel.prefecture_city,
        "chunk_type": rag_models().ChunkType.overview,
        "normalized_content": "鼓浪屿位于厦门岛西南侧。",
    }
    values.update(overrides)
    return rag_hashing().build_embedding_input(**values)


def test_embedding_input_has_exact_fields_and_fixed_schema_version():
    value = build_input()

    assert tuple(value.model_fields) == (
        "schema_version",
        "canonical_attraction_name",
        "destination_name",
        "destination_code",
        "destination_level",
        "chunk_type",
        "normalized_content",
    )
    assert value.schema_version == SCHEMA_VERSION


def test_build_embedding_input_returns_approved_semantic_values():
    value = build_input()

    assert value.canonical_attraction_name == "鼓浪屿"
    assert value.destination_name == "厦门"
    assert value.destination_code == "350200"
    assert value.destination_level.value == "prefecture_city"
    assert value.chunk_type.value == "overview"
    assert value.normalized_content == "鼓浪屿位于厦门岛西南侧。"


def test_build_embedding_input_normalizes_semantic_strings():
    value = build_input(
        canonical_attraction_name="  Ｇｕｌａｎｇｙｕ  ",
        destination_name=" 厦门\t",
        normalized_content="  鼓浪屿位于厦门。\r\n\r\n 第二句。  ",
    )

    assert value.canonical_attraction_name == "Gulangyu"
    assert value.destination_name == "厦门"
    assert value.normalized_content == "鼓浪屿位于厦门。\n第二句。"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"canonical_attraction_name": "ＡＢＣ"}, "ABC"),
        ({"destination_name": "  厦门  "}, "厦门"),
        ({"normalized_content": "第一句。\r\n\r\n\t第二句。"}, "第一句。\n第二句。"),
    ],
)
def test_normalization_equivalent_inputs_produce_same_canonical_semantic_value(overrides, expected):
    value = build_input(**overrides)
    field = next(iter(overrides))

    assert getattr(value, field) == expected


def test_equivalent_normalized_inputs_produce_same_text_and_hash():
    hashing = rag_hashing()
    first = build_input(
        canonical_attraction_name=" 鼓浪屿 ",
        destination_name="厦门\t",
        normalized_content="鼓浪屿位于厦门。\r\n\r\n第二句。",
    )
    second = build_input(
        canonical_attraction_name="鼓浪屿",
        destination_name="厦门",
        normalized_content="鼓浪屿位于厦门。\n第二句。",
    )

    assert first.model_dump() == second.model_dump()
    assert hashing.canonical_embedding_text(first) == hashing.canonical_embedding_text(second)
    assert hashing.embedding_input_hash(first) == hashing.embedding_input_hash(second)


def test_canonical_embedding_text_uses_fixed_compact_unicode_json():
    expected = (
        '{"schema_version":"rag-v2-embedding-input-v1",'
        '"canonical_attraction_name":"鼓浪屿","destination_name":"厦门",'
        '"destination_code":"350200","destination_level":"prefecture_city",'
        '"chunk_type":"overview","normalized_content":"鼓浪屿位于厦门岛西南侧。"}'
    )

    assert rag_hashing().canonical_embedding_text(build_input()) == expected


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("canonical_attraction_name", "日光岩"),
        ("destination_name", "福州"),
        ("destination_code", "350100"),
        ("destination_level", "province"),
        ("chunk_type", "highlights"),
        ("normalized_content", "鼓浪屿位于厦门岛东南侧。"),
    ],
)
def test_embedding_input_hash_changes_for_each_embedding_relevant_field(field, changed_value):
    hashing = rag_hashing()
    changed = build_input(**{field: changed_value})

    assert hashing.embedding_input_hash(changed) != hashing.embedding_input_hash(build_input())


@pytest.mark.parametrize(
    "excluded_field",
    [
        "attraction_id",
        "aliases",
        "province_code",
        "province_name",
        "district_name",
        "category",
        "tags",
        "latitude",
        "longitude",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
        "model",
        "task",
        "dimensions",
        "chunk_key",
        "vector",
    ],
)
def test_canonical_embedding_text_excludes_non_embedding_input_fields(excluded_field):
    canonical = rag_hashing().canonical_embedding_text(build_input())

    assert excluded_field not in canonical


def test_embedding_input_hash_is_sha256_of_exact_canonical_embedding_text():
    hashing = rag_hashing()
    value = build_input()
    canonical_text = hashing.canonical_embedding_text(value)
    expected = sha256(canonical_text.encode("utf-8")).hexdigest()

    assert hashing.embedding_input_hash(value) == expected
    assert len(expected) == 64
    assert expected == expected.lower()


def test_canonical_embedding_text_bytes_are_the_only_hash_input():
    hashing = rag_hashing()
    value = build_input()
    canonical_text = hashing.canonical_embedding_text(value)

    assert not canonical_text.endswith("\n")
    assert "  " not in canonical_text
    assert hashing.embedding_input_hash(value) == sha256(canonical_text.encode("utf-8")).hexdigest()


def test_embedding_input_calls_are_deterministic_and_pure():
    hashing = rag_hashing()
    value = build_input()
    before = value.model_dump()

    first_text = hashing.canonical_embedding_text(value)
    first_hash = hashing.embedding_input_hash(value)
    second_text = hashing.canonical_embedding_text(value)
    second_hash = hashing.embedding_input_hash(value)

    assert first_text == second_text
    assert first_hash == second_hash
    assert value.model_dump() == before


def test_embedding_input_rejects_noncanonical_schema_version():
    models = rag_models()
    with pytest.raises(ValidationError):
        models.EmbeddingInput(
            schema_version="rag-v2-embedding-input-v0",
            canonical_attraction_name="鼓浪屿",
            destination_name="厦门",
            destination_code="350200",
            destination_level="prefecture_city",
            chunk_type="overview",
            normalized_content="鼓浪屿位于厦门岛西南侧。",
        )


@pytest.mark.parametrize("destination_code", [350200, "35020", "3502000", "ABC200"])
def test_embedding_input_rejects_invalid_destination_codes(destination_code):
    models = rag_models()
    with pytest.raises(ValidationError):
        models.EmbeddingInput(
            schema_version=SCHEMA_VERSION,
            canonical_attraction_name="鼓浪屿",
            destination_name="厦门",
            destination_code=destination_code,
            destination_level="prefecture_city",
            chunk_type="overview",
            normalized_content="鼓浪屿位于厦门岛西南侧。",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("destination_level", "city"), ("chunk_type", "description")],
)
def test_embedding_input_rejects_invalid_enum_values(field, value):
    models = rag_models()
    values = {
        "schema_version": SCHEMA_VERSION,
        "canonical_attraction_name": "鼓浪屿",
        "destination_name": "厦门",
        "destination_code": "350200",
        "destination_level": "prefecture_city",
        "chunk_type": "overview",
        "normalized_content": "鼓浪屿位于厦门岛西南侧。",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        models.EmbeddingInput(**values)


def test_embedding_input_rejects_extra_fields():
    models = rag_models()
    values = build_input().model_dump()

    with pytest.raises(ValidationError):
        models.EmbeddingInput(**values, unexpected="value")
