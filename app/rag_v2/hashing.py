from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256

from app.rag_v2.models import (
    AttractionVersionMetadata,
    ChunkType,
    DestinationLevel,
    EmbeddingInput,
    ManifestInput,
)


__all__ = [
    "build_embedding_input",
    "canonical_metadata_json",
    "canonical_embedding_text",
    "content_hash",
    "embedding_input_hash",
    "manifest_hash",
    "metadata_hash",
    "normalize_content",
    "normalize_text",
    "canonical_manifest_json",
]


_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\r\n]+")


def _normalize_string(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in normalized.split("\n"):
        line = _HORIZONTAL_WHITESPACE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def normalize_text(value: str) -> str:
    return _normalize_string(value)


def normalize_content(value: str) -> str:
    return _normalize_string(value)


def _sha256_hex(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def content_hash(value: str) -> str:
    return _sha256_hex(normalize_content(value))


def _normalize_string_collection(values: tuple[str, ...]) -> list[str]:
    return sorted(set(_normalize_string(value) for value in values))


def _canonical_optional_string(value: str | None) -> str | None:
    return None if value is None else _normalize_string(value)


def _canonical_coordinate(value: float | None) -> str | None:
    return None if value is None else f"{value:.7f}"


def canonical_metadata_json(metadata: AttractionVersionMetadata) -> str:
    destination = metadata.destination
    payload = {
        "canonical_name": _normalize_string(metadata.canonical_name),
        "aliases": _normalize_string_collection(metadata.aliases),
        "destination_code": destination.destination_code,
        "destination_level": destination.destination_level.value,
        "destination_name": _normalize_string(destination.destination_name),
        "province_code": destination.province_code,
        "province_name": _normalize_string(destination.province_name),
        "district_name": _canonical_optional_string(destination.district_name),
        "category": _canonical_optional_string(metadata.category),
        "tags": _normalize_string_collection(metadata.tags),
        "latitude": _canonical_coordinate(destination.latitude),
        "longitude": _canonical_coordinate(destination.longitude),
        "status": metadata.status.value,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def metadata_hash(metadata: AttractionVersionMetadata) -> str:
    return _sha256_hex(canonical_metadata_json(metadata))


def build_embedding_input(
    *,
    canonical_attraction_name: str,
    destination_name: str,
    destination_code: str,
    destination_level: DestinationLevel,
    chunk_type: ChunkType,
    normalized_content: str,
) -> EmbeddingInput:
    return EmbeddingInput(
        schema_version="rag-v2-embedding-input-v1",
        canonical_attraction_name=normalize_text(canonical_attraction_name),
        destination_name=normalize_text(destination_name),
        destination_code=destination_code,
        destination_level=destination_level,
        chunk_type=chunk_type,
        normalized_content=normalize_content(normalized_content),
    )


def canonical_embedding_text(value: EmbeddingInput) -> str:
    payload = {
        "schema_version": value.schema_version,
        "canonical_attraction_name": value.canonical_attraction_name,
        "destination_name": value.destination_name,
        "destination_code": value.destination_code,
        "destination_level": value.destination_level.value,
        "chunk_type": value.chunk_type.value,
        "normalized_content": value.normalized_content,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def embedding_input_hash(value: EmbeddingInput) -> str:
    return _sha256_hex(canonical_embedding_text(value))


_CHUNK_TYPE_ORDER = {
    ChunkType.overview: 0,
    ChunkType.highlights: 1,
    ChunkType.transport: 2,
    ChunkType.visit_advice: 3,
    ChunkType.seasonal: 4,
}


def canonical_manifest_json(manifest: ManifestInput) -> str:
    profile = manifest.embedding_profile
    attractions = []
    for attraction in sorted(manifest.attractions, key=lambda item: str(item.attraction_id)):
        chunks = []
        for chunk in sorted(
            attraction.chunks,
            key=lambda item: (_CHUNK_TYPE_ORDER[item.chunk_type], item.ordinal, item.chunk_key),
        ):
            chunks.append(
                {
                    "chunk_key": chunk.chunk_key,
                    "chunk_type": chunk.chunk_type.value,
                    "ordinal": chunk.ordinal,
                    "content_hash": chunk.content_hash,
                    "embedding_input_hash": chunk.embedding_input_hash,
                    "source_label": chunk.source_label,
                    "source_url": chunk.source_url,
                    "source_type": chunk.source_type,
                    "reviewed_on": chunk.reviewed_on.isoformat(),
                }
            )
        attractions.append(
            {
                "attraction_id": str(attraction.attraction_id),
                "metadata_hash": attraction.metadata_hash,
                "chunks": chunks,
            }
        )

    payload = {
        "schema_version": manifest.schema_version,
        "dataset_key": manifest.dataset_key,
        "embedding_profile": {
            "model": profile.model,
            "task": profile.task.value,
            "dimensions": profile.dimensions,
            "input_schema_version": profile.input_schema_version,
        },
        "attractions": attractions,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def manifest_hash(manifest: ManifestInput) -> str:
    return _sha256_hex(canonical_manifest_json(manifest))
