from __future__ import annotations

import re
from uuid import UUID

from app.rag_v2.hashing import (
    build_embedding_input,
    content_hash,
    embedding_input_hash,
    normalize_content,
)
from app.rag_v2.models import (
    AttractionVersionMetadata,
    ChunkType,
    SemanticChunk,
    SemanticSection,
)


__all__ = [
    "CHUNK_KEY_SCHEMA_VERSION",
    "DEFAULT_CHUNK_BUDGET",
    "chunk_key_for",
    "SemanticChunker",
]


CHUNK_KEY_SCHEMA_VERSION = "rag-v2-chunk-key-v1"
DEFAULT_CHUNK_BUDGET = 4000

_SENTENCE_TERMINATORS = frozenset("。！？.!?")
_CLAUSE_DELIMITERS = frozenset("，,；;：:")
_WHITESPACE = re.compile(r"\s+")


def chunk_key_for(
    *, attraction_id: UUID, chunk_type: ChunkType, ordinal: int
) -> str:
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")
    return f"{CHUNK_KEY_SCHEMA_VERSION}|{attraction_id}|{chunk_type.value}|{ordinal}"


def _canonicalize_line_endings(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _paragraphs(value: str) -> tuple[str, ...]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in value.split("\n"):
        if line.strip() == "":
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return tuple(paragraphs)


def _boundary_children(value: str, boundaries: frozenset[str]) -> tuple[str, ...]:
    children: list[str] = []
    start = 0
    for index, character in enumerate(value):
        if character in boundaries:
            children.append(value[start : index + 1])
            start = index + 1
    if start < len(value):
        children.append(value[start:])
    return tuple(child for child in children if normalize_content(child))


def _whitespace_children(value: str) -> tuple[str, ...]:
    return tuple(child for child in _WHITESPACE.split(value.strip()) if child)


def _split_oversized(value: str, *, max_code_points: int, level: int) -> list[str]:
    normalized = normalize_content(value)
    if not normalized:
        return []
    if len(normalized) <= max_code_points:
        return [normalized]

    if level == 0:
        children = _boundary_children(value, _SENTENCE_TERMINATORS)
    elif level == 1:
        children = _boundary_children(value, _CLAUSE_DELIMITERS)
    else:
        children = _whitespace_children(value)

    if len(children) < 2:
        if level >= 2:
            raise ValueError("an atomic semantic unit exceeds the chunk budget")
        return _split_oversized(
            value,
            max_code_points=max_code_points,
            level=level + 1,
        )

    fragments: list[str] = []
    for child in children:
        fragments.extend(
            _split_oversized(
                child,
                max_code_points=max_code_points,
                level=level + 1,
            )
        )
    return fragments


class SemanticChunker:
    def __init__(self, max_code_points: int = DEFAULT_CHUNK_BUDGET) -> None:
        if max_code_points <= 0:
            raise ValueError("max_code_points must be positive")
        self.max_code_points = max_code_points

    def chunk(
        self,
        section: SemanticSection,
        *,
        attraction: AttractionVersionMetadata,
    ) -> tuple[SemanticChunk, ...]:
        if section.attraction_id != attraction.attraction_id:
            raise ValueError("section and attraction IDs must match")

        canonical_content = _canonicalize_line_endings(section.content)
        whole_normalized = normalize_content(canonical_content)
        if not whole_normalized:
            raise ValueError("section content must not be empty after normalization")

        if len(whole_normalized) <= self.max_code_points:
            fragments = [whole_normalized]
        else:
            paragraphs = _paragraphs(canonical_content)
            if len(paragraphs) >= 2:
                fragments = []
                for paragraph in paragraphs:
                    fragments.extend(
                        _split_oversized(
                            paragraph,
                            max_code_points=self.max_code_points,
                            level=0,
                        )
                    )
            else:
                fragments = _split_oversized(
                    canonical_content,
                    max_code_points=self.max_code_points,
                    level=0,
                )

        return tuple(
            self._make_chunk(
                fragment=fragment,
                ordinal=ordinal,
                section=section,
                attraction=attraction,
            )
            for ordinal, fragment in enumerate(fragments)
        )

    @staticmethod
    def _make_chunk(
        *,
        fragment: str,
        ordinal: int,
        section: SemanticSection,
        attraction: AttractionVersionMetadata,
    ) -> SemanticChunk:
        embedding_input = build_embedding_input(
            canonical_attraction_name=attraction.canonical_name,
            destination_name=attraction.destination.destination_name,
            destination_code=attraction.destination.destination_code,
            destination_level=attraction.destination.destination_level,
            chunk_type=section.chunk_type,
            normalized_content=fragment,
        )
        return SemanticChunk(
            chunk_key=chunk_key_for(
                attraction_id=section.attraction_id,
                chunk_type=section.chunk_type,
                ordinal=ordinal,
            ),
            attraction_id=section.attraction_id,
            chunk_type=section.chunk_type,
            ordinal=ordinal,
            normalized_content=fragment,
            content_hash=content_hash(fragment),
            embedding_input_hash=embedding_input_hash(embedding_input),
            source_label=section.source_label,
            source_url=section.source_url,
            source_type=section.source_type,
            reviewed_on=section.reviewed_on,
        )
