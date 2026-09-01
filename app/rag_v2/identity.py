from __future__ import annotations

from datetime import datetime
from typing import Collection, Protocol
from uuid import UUID

from app.rag_v2.models import (
    AttractionVersionMetadata,
    StableAttraction,
)


__all__ = [
    "AttractionIdentitySource",
    "handle_corpus_absence",
    "merge_attraction",
    "rename_attraction",
    "retire_attraction",
]


class AttractionIdentitySource(Protocol):
    def resolve(self, registry_key: str) -> UUID | None:
        """Return the stable identity registered for a key, if one exists."""

    def allocate(self, registry_key: str) -> UUID:
        """Allocate and persist a stable identity for a new registry key."""


def rename_attraction(
    metadata: AttractionVersionMetadata,
    *,
    canonical_name: str,
) -> AttractionVersionMetadata:
    """Return renamed version metadata without changing its stable identity."""

    values = metadata.model_dump()
    values["canonical_name"] = canonical_name
    return AttractionVersionMetadata.model_validate(values)


def merge_attraction(
    source: StableAttraction,
    target: StableAttraction | None,
    *,
    known_descendants: Collection[UUID],
) -> StableAttraction:
    """Mark a source attraction as merged into a validated target identity."""

    if target is None:
        raise ValueError("merge target is required")
    if source.attraction_id == target.attraction_id:
        raise ValueError("an attraction cannot merge into itself")
    if target.attraction_id in known_descendants:
        raise ValueError("merge would create a known descendant cycle")

    values = source.model_dump()
    values.update(
        lifecycle_status="merged",
        retired_at=None,
        merged_into_attraction_id=target.attraction_id,
    )
    return StableAttraction.model_validate(values)


def retire_attraction(
    attraction: StableAttraction,
    *,
    retired_at: datetime,
) -> StableAttraction:
    """Return an explicitly retired attraction without mutating the input."""

    values = attraction.model_dump()
    values.update(
        lifecycle_status="retired",
        retired_at=retired_at,
        merged_into_attraction_id=None,
    )
    return StableAttraction.model_validate(values)


def handle_corpus_absence(
    attraction: StableAttraction,
    *,
    known_in_source: bool,
) -> StableAttraction:
    """Preserve entity lifecycle when an attraction is absent from a corpus."""

    del known_in_source
    return StableAttraction.model_validate(attraction.model_dump())
