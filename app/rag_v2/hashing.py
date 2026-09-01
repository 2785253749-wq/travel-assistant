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
)


__all__ = [
    "build_embedding_input",
    "canonical_metadata_json",
    "canonical_embedding_text",
    "content_hash",
    "embedding_input_hash",
    "metadata_hash",
    "normalize_content",
    "normalize_text",
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
