from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256

from app.rag_v2.models import AttractionVersionMetadata


__all__ = [
    "canonical_metadata_json",
    "content_hash",
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
