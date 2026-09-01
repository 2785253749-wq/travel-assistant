from datetime import date
from hashlib import sha256
from importlib import import_module
from uuid import UUID

import pytest
from pydantic import ValidationError


SCHEMA_VERSION = "rag-v2-manifest-v1"
DATASET_KEY = "travel-attractions-cn"
EMBEDDING_INPUT_SCHEMA_VERSION = "rag-v2-embedding-input-v1"
ATTRACTION_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ATTRACTION_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def rag_models():
    """Resolve future manifest models only when a RED test executes."""
    return import_module("app.rag_v2.models")


def rag_hashing():
    """Resolve future manifest hashing APIs only when a RED test executes."""
    return import_module("app.rag_v2.hashing")


def embedding_profile(**overrides):
    values = {
        "model": "jina-embeddings-v3",
        "task": "retrieval.passage",
        "dimensions": 1024,
        "input_schema_version": EMBEDDING_INPUT_SCHEMA_VERSION,
    }
    values.update(overrides)
    return rag_models().EmbeddingProfile(**values)


def manifest_chunk(
    *,
    attraction_id=ATTRACTION_A,
    chunk_key=None,
    chunk_type="overview",
    ordinal=0,
    content_hash="c" * 64,
    embedding_input_hash="d" * 64,
    source_label="厦门市文化和旅游局",
    source_url="https://example.gov.cn/xiamen/gulangyu",
    source_type="official",
    reviewed_on=date(2026, 8, 31),
):
    if chunk_key is None:
        chunk_key = f"rag-v2-chunk-key-v1|{attraction_id}|{chunk_type}|{ordinal}"
    return rag_models().ManifestChunk(
        chunk_key=chunk_key,
        chunk_type=chunk_type,
        ordinal=ordinal,
        content_hash=content_hash,
        embedding_input_hash=embedding_input_hash,
        source_label=source_label,
        source_url=source_url,
        source_type=source_type,
        reviewed_on=reviewed_on,
    )


def manifest_attraction(
    *,
    attraction_id=ATTRACTION_A,
    metadata_hash="a" * 64,
    chunks=None,
):
    if chunks is None:
        chunks = (manifest_chunk(attraction_id=attraction_id),)
    return rag_models().ManifestAttraction(
        attraction_id=attraction_id,
        metadata_hash=metadata_hash,
        chunks=chunks,
    )


def manifest(
    *,
    dataset_key=DATASET_KEY,
    profile=None,
    attractions=None,
    attraction_id=ATTRACTION_A,
    metadata_hash="a" * 64,
    chunk=None,
):
    if profile is None:
        profile = embedding_profile()
    if attractions is None:
        if chunk is None:
            chunk = manifest_chunk(attraction_id=attraction_id)
        attractions = (manifest_attraction(attraction_id=attraction_id, metadata_hash=metadata_hash, chunks=(chunk,)),)
    return rag_models().ManifestInput(
        schema_version=SCHEMA_VERSION,
        dataset_key=dataset_key,
        embedding_profile=profile,
        attractions=attractions,
    )


def two_attraction_manifest():
    a_transport = manifest_chunk(
        attraction_id=ATTRACTION_A,
        chunk_type="transport",
        content_hash="e" * 64,
        embedding_input_hash="f" * 64,
        source_label="厦门市交通局",
        source_url="https://example.gov.cn/xiamen/transport",
        reviewed_on=date(2026, 8, 30),
    )
    a_overview = manifest_chunk(attraction_id=ATTRACTION_A)
    b_highlights = manifest_chunk(
        attraction_id=ATTRACTION_B,
        chunk_type="highlights",
        content_hash="1" * 64,
        embedding_input_hash="2" * 64,
        source_label="福建省文化和旅游厅",
        source_url="https://example.gov.cn/fujian/highlights",
        reviewed_on=date(2026, 8, 29),
    )
    return manifest(
        attractions=(
            manifest_attraction(
                attraction_id=ATTRACTION_B,
                metadata_hash="b" * 64,
                chunks=(b_highlights,),
            ),
            manifest_attraction(
                attraction_id=ATTRACTION_A,
                metadata_hash="a" * 64,
                chunks=(a_transport, a_overview),
            ),
        )
    )


def test_manifest_models_have_exact_fields_and_order():
    models = rag_models()

    assert tuple(models.ManifestInput.model_fields) == (
        "schema_version",
        "dataset_key",
        "embedding_profile",
        "attractions",
    )
    assert tuple(models.ManifestAttraction.model_fields) == (
        "attraction_id",
        "metadata_hash",
        "chunks",
    )
    assert tuple(models.ManifestChunk.model_fields) == (
        "chunk_key",
        "chunk_type",
        "ordinal",
        "content_hash",
        "embedding_input_hash",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
    )


def test_manifest_uses_fixed_schema_version_and_document_embedding_profile():
    value = manifest()

    assert value.schema_version == SCHEMA_VERSION
    assert value.embedding_profile.model == "jina-embeddings-v3"
    assert value.embedding_profile.task.value == "retrieval.passage"
    assert value.embedding_profile.dimensions == 1024
    assert value.embedding_profile.input_schema_version == EMBEDDING_INPUT_SCHEMA_VERSION


def test_manifest_rejects_noncanonical_schema_version():
    models = rag_models()

    with pytest.raises(ValidationError):
        models.ManifestInput(
            schema_version="rag-v2-manifest-v0",
            dataset_key=DATASET_KEY,
            embedding_profile=embedding_profile(),
            attractions=(manifest_attraction(),),
        )


def test_manifest_rejects_query_embedding_profile_for_document_manifest():
    models = rag_models()

    with pytest.raises(ValidationError):
        models.ManifestInput(
            schema_version=SCHEMA_VERSION,
            dataset_key=DATASET_KEY,
            embedding_profile=embedding_profile(task="retrieval.query"),
            attractions=(manifest_attraction(),),
        )


def test_canonical_manifest_json_uses_exact_nested_field_order_and_sorting():
    expected = (
        '{"schema_version":"rag-v2-manifest-v1","dataset_key":"travel-attractions-cn",'
        '"embedding_profile":{"model":"jina-embeddings-v3","task":"retrieval.passage",'
        '"dimensions":1024,"input_schema_version":"rag-v2-embedding-input-v1"},'
        '"attractions":[{"attraction_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",'
        '"metadata_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"chunks":[{"chunk_key":"rag-v2-chunk-key-v1|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa|overview|0",'
        '"chunk_type":"overview","ordinal":0,'
        '"content_hash":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        '"embedding_input_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
        '"source_label":"厦门市文化和旅游局","source_url":"https://example.gov.cn/xiamen/gulangyu",'
        '"source_type":"official","reviewed_on":"2026-08-31"},'
        '{"chunk_key":"rag-v2-chunk-key-v1|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa|transport|0",'
        '"chunk_type":"transport","ordinal":0,'
        '"content_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",'
        '"embedding_input_hash":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",'
        '"source_label":"厦门市交通局","source_url":"https://example.gov.cn/xiamen/transport",'
        '"source_type":"official","reviewed_on":"2026-08-30"}]},'
        '{"attraction_id":"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",'
        '"metadata_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"chunks":[{"chunk_key":"rag-v2-chunk-key-v1|bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb|highlights|0",'
        '"chunk_type":"highlights","ordinal":0,'
        '"content_hash":"1111111111111111111111111111111111111111111111111111111111111111",'
        '"embedding_input_hash":"2222222222222222222222222222222222222222222222222222222222222222",'
        '"source_label":"福建省文化和旅游厅","source_url":"https://example.gov.cn/fujian/highlights",'
        '"source_type":"official","reviewed_on":"2026-08-29"}]}]}'
    )

    assert rag_hashing().canonical_manifest_json(two_attraction_manifest()) == expected


def test_manifest_serializes_uuid_enum_and_reviewed_date_as_canonical_values():
    canonical = rag_hashing().canonical_manifest_json(manifest())

    assert '"attraction_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"' in canonical
    assert '"chunk_type":"overview"' in canonical
    assert '"reviewed_on":"2026-08-31"' in canonical
    assert "UUID('" not in canonical
    assert "ChunkType." not in canonical


@pytest.mark.parametrize(
    "excluded_field",
    [
        "corpus_version_id",
        "version_label",
        "vector",
        "embedding",
        "status",
        "execution_status",
        "created_at",
        "updated_at",
        "embedded_at",
        "database_id",
        "row_id",
        "error",
        "retry_count",
    ],
)
def test_canonical_manifest_json_excludes_runtime_and_execution_fields(excluded_field):
    canonical = rag_hashing().canonical_manifest_json(manifest())

    assert f'"{excluded_field}"' not in canonical


def test_canonical_manifest_json_excludes_raw_content_and_metadata():
    canonical = rag_hashing().canonical_manifest_json(manifest())

    assert '"content":' not in canonical
    assert '"canonical_name":' not in canonical
    assert '"aliases":' not in canonical
    assert '"destination":' not in canonical
    assert '"tags":' not in canonical


def test_manifest_canonicalization_is_invariant_to_attraction_and_chunk_input_order():
    hashing = rag_hashing()
    first = two_attraction_manifest()
    second = two_attraction_manifest().model_copy(
        update={"attractions": tuple(reversed(two_attraction_manifest().attractions))}
    )

    assert hashing.canonical_manifest_json(first) == hashing.canonical_manifest_json(second)
    assert hashing.manifest_hash(first) == hashing.manifest_hash(second)


def test_manifest_hash_is_sha256_of_exact_canonical_manifest_json():
    hashing = rag_hashing()
    value = two_attraction_manifest()
    canonical = hashing.canonical_manifest_json(value)
    expected = sha256(canonical.encode("utf-8")).hexdigest()

    assert hashing.manifest_hash(value) == expected
    assert len(expected) == 64
    assert expected == expected.lower()


def test_manifest_hash_has_no_trailing_newline_and_is_deterministic():
    hashing = rag_hashing()
    value = two_attraction_manifest()

    first_json = hashing.canonical_manifest_json(value)
    second_json = hashing.canonical_manifest_json(value)

    assert first_json == second_json
    assert not first_json.endswith("\n")
    assert hashing.manifest_hash(value) == hashing.manifest_hash(value)


@pytest.mark.parametrize(
    ("profile_overrides", "label"),
    [
        ({"model": "jina-embeddings-v4"}, "model"),
        ({"dimensions": 1536}, "dimensions"),
        ({"input_schema_version": "rag-v2-embedding-input-v2"}, "input schema version"),
    ],
)
def test_manifest_rejects_noncanonical_embedding_profile(profile_overrides, label):
    models = rag_models()

    with pytest.raises(ValidationError):
        models.ManifestInput(
            schema_version=SCHEMA_VERSION,
            dataset_key=DATASET_KEY,
            embedding_profile=embedding_profile(**profile_overrides),
            attractions=(manifest_attraction(),),
        )


@pytest.mark.parametrize(
    ("changed_value", "label"),
    [
        (DATASET_KEY + "-v2", "dataset key"),
        (ATTRACTION_B, "attraction id"),
        ("b" * 64, "metadata hash"),
        ("changed-chunk-key", "chunk key"),
        ("highlights", "chunk type"),
        (1, "ordinal"),
        ("e" * 64, "content hash"),
        ("f" * 64, "embedding input hash"),
        ("另一个来源", "source label"),
        ("https://example.gov.cn/other", "source url"),
        ("government", "source type"),
        (date(2026, 9, 1), "reviewed date"),
    ],
)
def test_manifest_hash_changes_for_manifest_identity_or_provenance_field(changed_value, label):
    hashing = rag_hashing()
    baseline = manifest()

    if label == "dataset key":
        changed = manifest(dataset_key=changed_value)
    elif label == "attraction id":
        changed = manifest(attraction_id=changed_value)
    elif label == "metadata hash":
        changed = manifest(metadata_hash=changed_value)
    else:
        chunk_kwargs = {
            "chunk_key": baseline.attractions[0].chunks[0].chunk_key,
            "chunk_type": baseline.attractions[0].chunks[0].chunk_type,
            "ordinal": baseline.attractions[0].chunks[0].ordinal,
            "content_hash": baseline.attractions[0].chunks[0].content_hash,
            "embedding_input_hash": baseline.attractions[0].chunks[0].embedding_input_hash,
            "source_label": baseline.attractions[0].chunks[0].source_label,
            "source_url": baseline.attractions[0].chunks[0].source_url,
            "source_type": baseline.attractions[0].chunks[0].source_type,
            "reviewed_on": baseline.attractions[0].chunks[0].reviewed_on,
        }
        field_by_label = {
            "chunk key": "chunk_key",
            "chunk type": "chunk_type",
            "ordinal": "ordinal",
            "content hash": "content_hash",
            "embedding input hash": "embedding_input_hash",
            "source label": "source_label",
            "source url": "source_url",
            "source type": "source_type",
            "reviewed date": "reviewed_on",
        }
        chunk_kwargs[field_by_label[label]] = changed_value
        changed = manifest(chunk=manifest_chunk(**chunk_kwargs))

    assert hashing.manifest_hash(changed) != hashing.manifest_hash(baseline), label


def test_manifest_canonicalization_does_not_mutate_input():
    hashing = rag_hashing()
    value = two_attraction_manifest()
    before = value.model_dump()

    hashing.canonical_manifest_json(value)
    hashing.manifest_hash(value)

    assert value.model_dump() == before


def test_manifest_rejects_invalid_chunk_type():
    models = rag_models()

    with pytest.raises(ValidationError):
        models.ManifestChunk(
            chunk_key="rag-v2-chunk-key-v1|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa|description|0",
            chunk_type="description",
            ordinal=0,
            content_hash="c" * 64,
            embedding_input_hash="d" * 64,
            source_label="厦门市文化和旅游局",
            source_url="https://example.gov.cn/xiamen/gulangyu",
            source_type="official",
            reviewed_on=date(2026, 8, 31),
        )


def test_manifest_models_reject_extra_fields():
    models = rag_models()
    values = manifest().model_dump()

    with pytest.raises(ValidationError):
        models.ManifestInput(**values, unexpected="value")
