from datetime import date
from importlib import import_module
import inspect
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.rag_v2.models import AttractionVersionMetadata, Destination, SemanticSection


ATTRACTION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_ATTRACTION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
REVIEWED_ON = date(2026, 8, 31)


def rag_models():
    """Resolve the future SemanticChunk model when a RED test executes."""
    return import_module("app.rag_v2.models")


def rag_chunking():
    """Resolve the future chunking module when a RED test executes."""
    return import_module("app.rag_v2.chunking")


def rag_hashing():
    return import_module("app.rag_v2.hashing")


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
    return Destination(**values)


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
    return AttractionVersionMetadata(**values)


def semantic_section(content, **overrides):
    values = {
        "attraction_id": ATTRACTION_ID,
        "chunk_type": "overview",
        "content": content,
        "source_label": "厦门市文化和旅游局",
        "source_url": "https://example.gov.cn/xiamen/gulangyu",
        "source_type": "official",
        "reviewed_on": REVIEWED_ON,
    }
    values.update(overrides)
    return SemanticSection(**values)


def strip_ws(value):
    return "".join(character for character in value if not character.isspace())


def test_semantic_chunk_has_exact_fields_and_strict_ordinal_contract():
    models = rag_models()

    assert tuple(models.SemanticChunk.model_fields) == (
        "chunk_key",
        "attraction_id",
        "chunk_type",
        "ordinal",
        "normalized_content",
        "content_hash",
        "embedding_input_hash",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
    )

    value = models.SemanticChunk(
        chunk_key="logical-key",
        attraction_id=ATTRACTION_ID,
        chunk_type="overview",
        ordinal=0,
        normalized_content="鼓浪屿位于厦门。",
        content_hash="content-hash",
        embedding_input_hash="embedding-hash",
        source_label="来源",
        source_url="https://example.gov.cn/source",
        source_type="official",
        reviewed_on=REVIEWED_ON,
    )

    assert value.attraction_id == ATTRACTION_ID
    assert value.ordinal == 0


@pytest.mark.parametrize("ordinal", [-1])
def test_semantic_chunk_rejects_negative_ordinal(ordinal):
    models = rag_models()

    with pytest.raises(ValidationError):
        models.SemanticChunk(
            chunk_key="logical-key",
            attraction_id=ATTRACTION_ID,
            chunk_type="overview",
            ordinal=ordinal,
            normalized_content="鼓浪屿位于厦门。",
            content_hash="content-hash",
            embedding_input_hash="embedding-hash",
            source_label="来源",
            source_url="https://example.gov.cn/source",
            source_type="official",
            reviewed_on=REVIEWED_ON,
        )


def test_semantic_chunk_rejects_extra_fields_without_hash_format_validation():
    models = rag_models()

    with pytest.raises(ValidationError):
        models.SemanticChunk(
            chunk_key="logical-key",
            attraction_id=ATTRACTION_ID,
            chunk_type="overview",
            ordinal=0,
            normalized_content="鼓浪屿位于厦门。",
            content_hash="not-a-sha256-value",
            embedding_input_hash="not-a-sha256-value",
            source_label="来源",
            source_url="https://example.gov.cn/source",
            source_type="official",
            reviewed_on=REVIEWED_ON,
            vector=[0.1, 0.2],
        )


def test_chunking_exposes_only_the_approved_task_6_api_shape():
    chunking = rag_chunking()

    assert chunking.CHUNK_KEY_SCHEMA_VERSION == "rag-v2-chunk-key-v1"
    assert chunking.DEFAULT_CHUNK_BUDGET == 4000
    assert hasattr(chunking, "chunk_key_for")
    assert hasattr(chunking, "SemanticChunker")

    key_signature = inspect.signature(chunking.chunk_key_for)
    assert tuple(key_signature.parameters) == ("attraction_id", "chunk_type", "ordinal")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in key_signature.parameters.values()
    )

    constructor_signature = inspect.signature(chunking.SemanticChunker.__init__)
    assert tuple(constructor_signature.parameters) == ("self", "max_code_points")
    assert constructor_signature.parameters["max_code_points"].default == 4000

    chunk_signature = inspect.signature(chunking.SemanticChunker.chunk)
    assert tuple(chunk_signature.parameters) == ("self", "section", "attraction")
    assert chunk_signature.parameters["attraction"].kind is inspect.Parameter.KEYWORD_ONLY


def test_chunk_key_uses_exact_stable_logical_string_format():
    chunking = rag_chunking()

    value = chunking.chunk_key_for(
        attraction_id=ATTRACTION_ID,
        chunk_type=rag_models().ChunkType.overview,
        ordinal=0,
    )

    assert value == "rag-v2-chunk-key-v1|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa|overview|0"


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("attraction_id", OTHER_ATTRACTION_ID),
        ("chunk_type", "highlights"),
        ("ordinal", 1),
    ],
)
def test_chunk_key_changes_for_each_logical_identity_component(field, changed_value):
    chunking = rag_chunking()
    values = {
        "attraction_id": ATTRACTION_ID,
        "chunk_type": rag_models().ChunkType.overview,
        "ordinal": 0,
    }
    changed = dict(values)
    changed[field] = (
        rag_models().ChunkType.highlights
        if field == "chunk_type"
        else changed_value
    )

    assert chunking.chunk_key_for(**changed) != chunking.chunk_key_for(**values)


def test_chunk_key_rejects_negative_ordinal():
    with pytest.raises(ValueError):
        rag_chunking().chunk_key_for(
            attraction_id=ATTRACTION_ID,
            chunk_type=rag_models().ChunkType.overview,
            ordinal=-1,
        )


@pytest.mark.parametrize("budget", [0, -1])
def test_semantic_chunker_rejects_non_positive_budget(budget):
    with pytest.raises(ValueError):
        rag_chunking().SemanticChunker(max_code_points=budget)


def test_within_budget_multi_paragraph_section_emits_exactly_one_chunk():
    chunker = rag_chunking().SemanticChunker(max_code_points=100)

    chunks = chunker.chunk(
        semantic_section("第一段。\n \t \n第二段。"),
        attraction=attraction_metadata(),
    )

    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert chunks[0].normalized_content == "第一段。\n第二段。"


def test_oversized_multi_paragraph_section_splits_at_paragraphs_first():
    chunker = rag_chunking().SemanticChunker(max_code_points=10)

    chunks = chunker.chunk(
        semantic_section("甲乙丙丁。\n\n戊己庚辛。\n\n壬癸子丑。"),
        attraction=attraction_metadata(),
    )

    assert [chunk.normalized_content for chunk in chunks] == [
        "甲乙丙丁。",
        "戊己庚辛。",
        "壬癸子丑。",
    ]
    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]
    assert all(len(chunk.normalized_content) <= 10 for chunk in chunks)


def test_whitespace_only_lines_are_paragraph_boundaries_before_normalization():
    chunker = rag_chunking().SemanticChunker(max_code_points=8)

    chunks = chunker.chunk(
        semantic_section("甲乙丙丁。\n \t \n戊己庚辛。"),
        attraction=attraction_metadata(),
    )

    assert [chunk.normalized_content for chunk in chunks] == ["甲乙丙丁。", "戊己庚辛。"]


def test_oversized_single_paragraph_uses_sentence_boundaries_before_lower_levels():
    chunker = rag_chunking().SemanticChunker(max_code_points=4)

    chunks = chunker.chunk(
        semantic_section("甲乙。丙丁！戊己？"),
        attraction=attraction_metadata(),
    )

    assert [chunk.normalized_content for chunk in chunks] == ["甲乙。", "丙丁!", "戊己?"]


def test_sentence_terminators_are_exact_and_remain_on_the_left_child():
    chunker = rag_chunking().SemanticChunker(max_code_points=3)

    chunks = chunker.chunk(
        semantic_section("甲。乙！丙？D.E!F?"),
        attraction=attraction_metadata(),
    )

    assert [chunk.normalized_content for chunk in chunks] == [
        "甲。",
        "乙!",
        "丙?",
        "D.",
        "E!",
        "F?",
    ]


def test_oversized_sentence_uses_clause_boundaries_before_whitespace():
    chunker = rag_chunking().SemanticChunker(max_code_points=3)

    chunks = chunker.chunk(
        semantic_section("甲，乙,丙；丁;戊：己:庚"),
        attraction=attraction_metadata(),
    )

    assert [chunk.normalized_content for chunk in chunks] == [
        "甲,",
        "乙,",
        "丙;",
        "丁;",
        "戊:",
        "己:",
        "庚",
    ]


def test_clause_delimiters_are_exact_and_remain_on_the_left_child():
    chunker = rag_chunking().SemanticChunker(max_code_points=2)

    chunks = chunker.chunk(
        semantic_section("甲，乙,丙；丁;戊：己:庚"),
        attraction=attraction_metadata(),
    )

    assert all(
        child.endswith(delimiter)
        for child, delimiter in zip(
            [chunk.normalized_content for chunk in chunks[:-1]],
            [",", ",", ";", ";", ":", ":"],
        )
    )


def test_oversized_clause_uses_whitespace_as_the_final_boundary():
    chunker = rag_chunking().SemanticChunker(max_code_points=3)

    chunks = chunker.chunk(
        semantic_section("甲乙 丙丁 戊己"),
        attraction=attraction_metadata(),
    )

    assert [chunk.normalized_content for chunk in chunks] == ["甲乙", "丙丁", "戊己"]
    assert all(" " not in chunk.normalized_content for chunk in chunks)


def test_unsplittable_atomic_token_overflow_raises_value_error():
    chunker = rag_chunking().SemanticChunker(max_code_points=3)

    with pytest.raises(ValueError):
        chunker.chunk(
            semantic_section("甲乙丙丁"),
            attraction=attraction_metadata(),
        )


@pytest.mark.parametrize("content", ["", "   ", "\n\n\t"])
def test_empty_normalized_content_raises_value_error(content):
    with pytest.raises(ValueError):
        rag_chunking().SemanticChunker(max_code_points=10).chunk(
            semantic_section(content),
            attraction=attraction_metadata(),
        )


def test_attraction_mismatch_raises_before_emitting_any_chunk():
    section = semantic_section("鼓浪屿位于厦门。")

    with pytest.raises(ValueError):
        rag_chunking().SemanticChunker().chunk(
            section,
            attraction=attraction_metadata(attraction_id=OTHER_ATTRACTION_ID),
        )


def test_chunks_propagate_one_attraction_and_all_section_provenance():
    section = semantic_section(
        "甲乙丙丁。\n\n戊己庚辛。",
        source_label="文化和旅游局",
        source_url="https://example.gov.cn/page",
        source_type="government",
        reviewed_on=date(2026, 9, 1),
    )
    chunks = rag_chunking().SemanticChunker(max_code_points=8).chunk(
        section,
        attraction=attraction_metadata(),
    )

    assert len(chunks) == 2
    for chunk in chunks:
        assert chunk.attraction_id == section.attraction_id == ATTRACTION_ID
        assert chunk.chunk_type.value == section.chunk_type.value == "overview"
        assert chunk.source_label == section.source_label
        assert chunk.source_url == section.source_url
        assert chunk.source_type == section.source_type
        assert chunk.reviewed_on == section.reviewed_on


def test_chunk_hashes_are_populated_through_existing_canonical_apis():
    hashing = rag_hashing()
    attraction = attraction_metadata()
    section = semantic_section("鼓浪屿位于厦门。")
    chunk = rag_chunking().SemanticChunker().chunk(section, attraction=attraction)[0]

    expected_input = hashing.build_embedding_input(
        canonical_attraction_name=attraction.canonical_name,
        destination_name=attraction.destination.destination_name,
        destination_code=attraction.destination.destination_code,
        destination_level=attraction.destination.destination_level,
        chunk_type=section.chunk_type,
        normalized_content=chunk.normalized_content,
    )

    assert chunk.content_hash == hashing.content_hash(chunk.normalized_content)
    assert chunk.embedding_input_hash == hashing.embedding_input_hash(expected_input)


def test_content_change_keeps_logical_key_but_changes_content_and_embedding_hashes():
    attraction = attraction_metadata()
    chunker = rag_chunking().SemanticChunker()
    first = chunker.chunk(semantic_section("鼓浪屿位于厦门。"), attraction=attraction)[0]
    second = chunker.chunk(semantic_section("鼓浪屿位于福州。"), attraction=attraction)[0]

    assert second.chunk_key == first.chunk_key
    assert second.content_hash != first.content_hash
    assert second.embedding_input_hash != first.embedding_input_hash


def test_provenance_only_change_keeps_logical_and_content_hashes():
    attraction = attraction_metadata()
    chunker = rag_chunking().SemanticChunker()
    first = chunker.chunk(semantic_section("鼓浪屿位于厦门。"), attraction=attraction)[0]
    second = chunker.chunk(
        semantic_section(
            "鼓浪屿位于厦门。",
            source_label="另一个来源",
            source_url="https://example.gov.cn/other",
            source_type="government",
            reviewed_on=date(2026, 9, 1),
        ),
        attraction=attraction,
    )[0]

    assert second.chunk_key == first.chunk_key
    assert second.content_hash == first.content_hash
    assert second.embedding_input_hash == first.embedding_input_hash
    assert second.source_label == "另一个来源"
    assert second.source_url == "https://example.gov.cn/other"
    assert second.source_type == "government"
    assert second.reviewed_on == date(2026, 9, 1)


def test_content_preservation_uses_non_whitespace_code_point_invariant():
    section = semantic_section("甲乙。\n\n丙丁！\n \t \n戊己，庚辛")
    chunks = rag_chunking().SemanticChunker(max_code_points=4).chunk(
        section,
        attraction=attraction_metadata(),
    )

    assert strip_ws(rag_hashing().normalize_content(section.content)) == strip_ws(
        "".join(chunk.normalized_content for chunk in chunks)
    )


def test_chunking_is_deterministic_pure_and_within_configured_budget():
    attraction = attraction_metadata()
    section = semantic_section("甲乙丙丁。\n\n戊己庚辛。")
    before_section = section.model_dump()
    before_attraction = attraction.model_dump()
    chunker = rag_chunking().SemanticChunker(max_code_points=8)

    first = chunker.chunk(section, attraction=attraction)
    second = chunker.chunk(section, attraction=attraction)

    assert first == second
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert all(len(chunk.normalized_content) <= 8 for chunk in first)
    assert section.model_dump() == before_section
    assert attraction.model_dump() == before_attraction
