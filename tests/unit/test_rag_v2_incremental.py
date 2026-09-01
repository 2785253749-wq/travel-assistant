from importlib import import_module
import inspect

import pytest


EMBEDDING_INPUT_HASH = "a" * 64
CHUNK_KEY = "rag-v2-chunk-key-v1|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa|overview|0"
OTHER_CHUNK_KEY = "rag-v2-chunk-key-v1|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa|overview|1"


def rag_incremental():
    """Resolve the future incremental module only when a RED test executes."""
    return import_module("app.rag_v2.incremental")


def embedding_identity(**overrides):
    incremental = rag_incremental()
    values = {
        "embedding_input_hash": EMBEDDING_INPUT_HASH,
        "embedding_model": "jina-embeddings-v3",
        "embedding_task": "retrieval.passage",
        "embedding_dimensions": 1024,
        "embedding_input_schema_version": "rag-v2-embedding-input-v1",
    }
    values.update(overrides)
    return incremental.EmbeddingIdentity(**values)


def previous_embedding(**overrides):
    incremental = rag_incremental()
    values = {
        "chunk_key": CHUNK_KEY,
        "identity": embedding_identity(),
        "vector": (0.0,) * 1024,
        "validated_corpus": True,
    }
    values.update(overrides)
    return incremental.PreviousEmbedding(**values)


def candidate(**overrides):
    incremental = rag_incremental()
    values = {
        "subject": incremental.IncrementalSubject.present_chunk,
        "current_chunk_key": OTHER_CHUNK_KEY,
        "current_identity": embedding_identity(),
        "previous_embeddings": (previous_embedding(),),
    }
    values.update(overrides)
    return incremental.IncrementalCandidate(**values)


def public_result_fields(result):
    return (
        getattr(result.action, "value", result.action),
        result.reason,
        result.reused_from_chunk_key,
    )


def candidate_public_snapshot(value):
    identity_fields = (
        "embedding_input_hash",
        "embedding_model",
        "embedding_task",
        "embedding_dimensions",
        "embedding_input_schema_version",
    )
    current_identity = tuple(
        getattr(value.current_identity, field) for field in identity_fields
    )
    previous = tuple(
        (
            item.chunk_key,
            tuple(getattr(item.identity, field) for field in identity_fields),
            None if item.vector is None else tuple(item.vector),
            item.validated_corpus,
        )
        for item in value.previous_embeddings
    )
    return (
        getattr(value.subject, "value", value.subject),
        value.current_chunk_key,
        current_identity,
        previous,
    )


def test_incremental_module_exposes_the_plan_defined_public_api():
    incremental = rag_incremental()

    for name in (
        "IncrementalSubject",
        "IncrementalAction",
        "EmbeddingIdentity",
        "PreviousEmbedding",
        "IncrementalCandidate",
        "IncrementalDecisionResult",
        "decide_incremental",
    ):
        assert hasattr(incremental, name)

    assert tuple(inspect.signature(incremental.decide_incremental).parameters) == (
        "candidate",
    )
    assert tuple(inspect.signature(incremental.EmbeddingIdentity).parameters) == (
        "embedding_input_hash",
        "embedding_model",
        "embedding_task",
        "embedding_dimensions",
        "embedding_input_schema_version",
    )
    assert tuple(inspect.signature(incremental.PreviousEmbedding).parameters) == (
        "chunk_key",
        "identity",
        "vector",
        "validated_corpus",
    )
    assert tuple(inspect.signature(incremental.IncrementalCandidate).parameters) == (
        "subject",
        "current_chunk_key",
        "current_identity",
        "previous_embeddings",
    )
    assert tuple(inspect.signature(incremental.IncrementalDecisionResult).parameters) == (
        "action",
        "reason",
        "reused_from_chunk_key",
    )


def test_incremental_subject_and_action_values_are_exact():
    incremental = rag_incremental()

    assert tuple(member.value for member in incremental.IncrementalSubject) == (
        "present_chunk",
        "removed_chunk",
        "attraction_absent",
        "explicitly_retired",
        "merged_source",
    )
    assert tuple(member.value for member in incremental.IncrementalAction) == (
        "reuse",
        "embed",
        "remove",
        "exclude",
        "no_action",
    )


def test_exact_embedding_identity_reuses_a_valid_vector():
    incremental = rag_incremental()

    result = incremental.decide_incremental(candidate())

    assert result.action is incremental.IncrementalAction.reuse
    assert result.reason
    assert result.reused_from_chunk_key == CHUNK_KEY
    assert not hasattr(result, "vector")
    assert not hasattr(result, "embedding")


def test_present_chunk_without_a_reusable_old_embedding_is_embedded():
    incremental = rag_incremental()

    result = incremental.decide_incremental(candidate(previous_embeddings=()))

    assert result.action is incremental.IncrementalAction.embed
    assert result.reason
    assert result.reused_from_chunk_key is None


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("embedding_input_hash", "b" * 64),
        ("embedding_model", "another-embedding-model"),
        ("embedding_task", "retrieval.query"),
        ("embedding_dimensions", 2048),
        ("embedding_input_schema_version", "rag-v2-embedding-input-v2"),
    ],
)
def test_any_embedding_identity_mismatch_disallows_reuse(field, changed_value):
    incremental = rag_incremental()
    changed_identity = embedding_identity(**{field: changed_value})
    vector = (0.0,) * changed_identity.embedding_dimensions

    result = incremental.decide_incremental(
        candidate(
            previous_embeddings=(
                previous_embedding(identity=changed_identity, vector=vector),
            )
        )
    )

    assert result.action is incremental.IncrementalAction.embed
    assert result.reused_from_chunk_key is None


def test_model_comparison_is_exact_not_case_or_whitespace_insensitive():
    incremental = rag_incremental()
    changed_identity = embedding_identity(embedding_model="JINA-EMBEDDINGS-V3 ")

    result = incremental.decide_incremental(
        candidate(previous_embeddings=(previous_embedding(identity=changed_identity),))
    )

    assert result.action is incremental.IncrementalAction.embed
    assert result.reused_from_chunk_key is None


@pytest.mark.parametrize("vector", [None, (), (0.0,) * 1023])
def test_missing_or_wrong_dimension_vector_disallows_reuse(vector):
    incremental = rag_incremental()

    result = incremental.decide_incremental(
        candidate(previous_embeddings=(previous_embedding(vector=vector),))
    )

    assert result.action is incremental.IncrementalAction.embed
    assert result.reused_from_chunk_key is None


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_vector_disallows_reuse_without_sanitization(invalid_value):
    incremental = rag_incremental()
    invalid_vector = (invalid_value,) + (0.0,) * 1023

    result = incremental.decide_incremental(
        candidate(previous_embeddings=(previous_embedding(vector=invalid_vector),))
    )

    assert result.action is incremental.IncrementalAction.embed
    assert result.reused_from_chunk_key is None


def test_unvalidated_old_corpus_disallows_reuse():
    incremental = rag_incremental()

    result = incremental.decide_incremental(
        candidate(
            previous_embeddings=(previous_embedding(validated_corpus=False),),
        )
    )

    assert result.action is incremental.IncrementalAction.embed
    assert result.reused_from_chunk_key is None


def test_same_chunk_key_with_changed_embedding_identity_does_not_reuse():
    incremental = rag_incremental()
    changed_identity = embedding_identity(embedding_input_hash="b" * 64)

    result = incremental.decide_incremental(
        candidate(
            current_chunk_key=CHUNK_KEY,
            previous_embeddings=(previous_embedding(identity=changed_identity),),
        )
    )

    assert result.action is incremental.IncrementalAction.embed
    assert result.reused_from_chunk_key is None


def test_different_chunk_key_with_exact_identity_may_reuse_old_vector():
    incremental = rag_incremental()
    old = previous_embedding(chunk_key=CHUNK_KEY)

    result = incremental.decide_incremental(
        candidate(current_chunk_key=OTHER_CHUNK_KEY, previous_embeddings=(old,))
    )

    assert result.action is incremental.IncrementalAction.reuse
    assert result.reused_from_chunk_key == CHUNK_KEY


def test_generic_1536_dimensions_with_exact_identity_can_reuse():
    incremental = rag_incremental()
    current_identity = embedding_identity(embedding_dimensions=1536)
    old = previous_embedding(
        identity=current_identity,
        vector=(0.0,) * 1536,
    )

    result = incremental.decide_incremental(
        candidate(
            current_identity=current_identity,
            previous_embeddings=(old,),
        )
    )

    assert result.action is incremental.IncrementalAction.reuse
    assert result.reused_from_chunk_key == CHUNK_KEY


def test_present_chunk_scans_past_invalid_and_nonmatching_candidates():
    incremental = rag_incremental()
    valid = previous_embedding(chunk_key="valid-key")
    invalid = previous_embedding(chunk_key="invalid-key", vector=None)
    nonmatching = previous_embedding(
        chunk_key="nonmatching-key",
        identity=embedding_identity(embedding_input_hash="b" * 64),
    )

    result = incremental.decide_incremental(
        candidate(previous_embeddings=(invalid, nonmatching, valid))
    )

    assert result.action is incremental.IncrementalAction.reuse
    assert result.reused_from_chunk_key == "valid-key"


def test_multiple_eligible_previous_embeddings_use_smallest_chunk_key():
    incremental = rag_incremental()
    previous = tuple(
        previous_embedding(chunk_key=chunk_key)
        for chunk_key in ("z-key", "a-key", "m-key")
    )

    result = incremental.decide_incremental(
        candidate(previous_embeddings=previous)
    )

    assert result.action is incremental.IncrementalAction.reuse
    assert result.reused_from_chunk_key == "a-key"


def test_previous_embedding_input_order_does_not_change_public_result_fields():
    incremental = rag_incremental()
    previous = {
        chunk_key: previous_embedding(chunk_key=chunk_key)
        for chunk_key in ("z-key", "a-key", "m-key")
    }

    first = incremental.decide_incremental(
        candidate(
            previous_embeddings=(previous["z-key"], previous["a-key"], previous["m-key"])
        )
    )
    second = incremental.decide_incremental(
        candidate(
            previous_embeddings=(previous["m-key"], previous["z-key"], previous["a-key"])
        )
    )

    assert public_result_fields(first) == public_result_fields(second)
    assert first.reused_from_chunk_key == "a-key"


def test_duplicate_eligible_chunk_keys_do_not_create_ambiguity_error():
    incremental = rag_incremental()
    previous = (
        previous_embedding(chunk_key="duplicate-key"),
        previous_embedding(chunk_key="duplicate-key", vector=(1.0,) * 1024),
    )

    result = incremental.decide_incremental(
        candidate(previous_embeddings=previous)
    )

    assert result.action is incremental.IncrementalAction.reuse
    assert result.reused_from_chunk_key == "duplicate-key"


@pytest.mark.parametrize(
    ("subject", "expected_action"),
    [
        ("removed_chunk", "remove"),
        ("attraction_absent", "no_action"),
        ("explicitly_retired", "exclude"),
        ("merged_source", "exclude"),
    ],
)
def test_lifecycle_subjects_precede_embedding_reuse_and_produce_exact_actions(
    subject, expected_action
):
    incremental = rag_incremental()
    subject_value = getattr(incremental.IncrementalSubject, subject)
    action_value = getattr(incremental.IncrementalAction, expected_action)

    result = incremental.decide_incremental(candidate(subject=subject_value))

    assert result.action is action_value
    assert result.reason
    assert result.reused_from_chunk_key is None


def test_metadata_and_provenance_are_not_incremental_input_fields():
    incremental = rag_incremental()
    candidate_fields = tuple(inspect.signature(incremental.IncrementalCandidate).parameters)
    previous_fields = tuple(inspect.signature(incremental.PreviousEmbedding).parameters)

    for excluded in (
        "metadata_hash",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
        "content_hash",
    ):
        assert excluded not in candidate_fields
        assert excluded not in previous_fields


def test_incremental_decision_is_pure_and_deterministic():
    incremental = rag_incremental()
    value = candidate()
    before = candidate_public_snapshot(value)

    first = incremental.decide_incremental(value)
    second = incremental.decide_incremental(value)

    assert public_result_fields(first) == public_result_fields(second)
    assert candidate_public_snapshot(value) == before
