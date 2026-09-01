"""Deterministic decisions for incremental embedding reuse."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite


class IncrementalSubject(str, Enum):
    present_chunk = "present_chunk"
    removed_chunk = "removed_chunk"
    attraction_absent = "attraction_absent"
    explicitly_retired = "explicitly_retired"
    merged_source = "merged_source"


class IncrementalAction(str, Enum):
    reuse = "reuse"
    embed = "embed"
    remove = "remove"
    exclude = "exclude"
    no_action = "no_action"


@dataclass(frozen=True)
class EmbeddingIdentity:
    embedding_input_hash: str
    embedding_model: str
    embedding_task: str
    embedding_dimensions: int
    embedding_input_schema_version: str


@dataclass(frozen=True)
class PreviousEmbedding:
    chunk_key: str
    identity: EmbeddingIdentity
    vector: tuple[float, ...] | None
    validated_corpus: bool


@dataclass(frozen=True)
class IncrementalCandidate:
    subject: IncrementalSubject
    current_chunk_key: str
    current_identity: EmbeddingIdentity
    previous_embeddings: tuple[PreviousEmbedding, ...]


@dataclass(frozen=True)
class IncrementalDecisionResult:
    action: IncrementalAction
    reason: str
    reused_from_chunk_key: str | None


_IDENTITY_FIELDS = (
    "embedding_input_hash",
    "embedding_model",
    "embedding_task",
    "embedding_dimensions",
    "embedding_input_schema_version",
)


def _identity_matches(
    current: EmbeddingIdentity,
    previous: EmbeddingIdentity,
) -> bool:
    return all(
        getattr(current, field) == getattr(previous, field)
        for field in _IDENTITY_FIELDS
    )


def _vector_is_eligible(previous: PreviousEmbedding) -> bool:
    if not previous.validated_corpus or previous.vector is None:
        return False
    if not previous.vector:
        return False
    if len(previous.vector) != previous.identity.embedding_dimensions:
        return False
    try:
        return all(isfinite(value) for value in previous.vector)
    except (TypeError, ValueError):
        return False


def decide_incremental(candidate: IncrementalCandidate) -> IncrementalDecisionResult:
    """Return the deterministic lifecycle or reuse decision for a candidate."""

    lifecycle_decisions = {
        IncrementalSubject.removed_chunk: (
            IncrementalAction.remove,
            "chunk was removed",
        ),
        IncrementalSubject.attraction_absent: (
            IncrementalAction.no_action,
            "attraction is absent",
        ),
        IncrementalSubject.explicitly_retired: (
            IncrementalAction.exclude,
            "chunk was explicitly retired",
        ),
        IncrementalSubject.merged_source: (
            IncrementalAction.exclude,
            "source was merged",
        ),
    }
    lifecycle_decision = lifecycle_decisions.get(candidate.subject)
    if lifecycle_decision is not None:
        action, reason = lifecycle_decision
        return IncrementalDecisionResult(action, reason, None)

    eligible = [
        previous
        for previous in candidate.previous_embeddings
        if _identity_matches(candidate.current_identity, previous.identity)
        and _vector_is_eligible(previous)
    ]
    if not eligible:
        return IncrementalDecisionResult(
            IncrementalAction.embed,
            "no eligible previous embedding",
            None,
        )

    selected = min(eligible, key=lambda previous: previous.chunk_key)
    return IncrementalDecisionResult(
        IncrementalAction.reuse,
        "reused an eligible previous embedding",
        selected.chunk_key,
    )


__all__ = [
    "IncrementalSubject",
    "IncrementalAction",
    "EmbeddingIdentity",
    "PreviousEmbedding",
    "IncrementalCandidate",
    "IncrementalDecisionResult",
    "decide_incremental",
]
