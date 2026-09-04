from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlparse
from uuid import UUID

import yaml

from app.rag_v2.models import (
    AttractionVersionMetadata,
    AttractionVersionStatus,
    ChunkType,
    Destination,
    SemanticSection,
)


@dataclass(frozen=True)
class AuthoringAttraction:
    registry_key: str
    metadata: AttractionVersionMetadata
    sections: tuple[SemanticSection, ...]


@dataclass(frozen=True)
class AuthoringDocument:
    schema_version: Literal["rag-v2-authoring-v1"]
    destination: Destination
    attractions: tuple[AuthoringAttraction, ...]


_SCHEMA_VERSION = "rag-v2-authoring-v1"
_ROOT_KEYS = frozenset({"schema_version", "destination", "attractions"})
_ATTRACTION_KEYS = frozenset(
    {
        "registry_key",
        "attraction_id",
        "canonical_name",
        "aliases",
        "category",
        "tags",
        "status",
        "sections",
    }
)
_DESTINATION_KEYS = frozenset(
    {
        "destination_code",
        "destination_level",
        "destination_name",
        "province_code",
        "province_name",
        "district_name",
        "latitude",
        "longitude",
    }
)
_SECTION_KEYS = frozenset(
    {"content", "source_label", "source_url", "source_type", "reviewed_on"}
)
_CHUNK_TYPE_ORDER = (
    ChunkType.overview,
    ChunkType.highlights,
    ChunkType.transport,
    ChunkType.visit_advice,
    ChunkType.seasonal,
)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], name: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} contains unknown or missing fields")


def _parse_destination(value: object) -> Destination:
    destination = _require_mapping(value, "destination")
    if not set(destination).issubset(_DESTINATION_KEYS):
        raise ValueError("destination contains unknown fields")
    return Destination.model_validate(dict(destination))


def _validate_provenance(section: Mapping[str, Any]) -> None:
    for field in ("source_label", "source_type"):
        value = section[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be non-blank")

    source_url = section["source_url"]
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("source_url must be an absolute HTTPS URL")
    try:
        parsed = urlparse(source_url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("source_url must be an absolute HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or hostname is None
        or not hostname.strip()
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("source_url must be an absolute HTTPS URL without credentials")

    if "reviewed_on" not in section or section["reviewed_on"] is None:
        raise ValueError("reviewed_on is required")


def _parse_sections(
    value: object, *, attraction_id: UUID, status: AttractionVersionStatus
) -> tuple[SemanticSection, ...]:
    sections = _require_mapping(value, "sections")
    for key in sections:
        try:
            ChunkType(key)
        except ValueError as exc:
            raise ValueError(f"unsupported semantic section: {key}") from exc

    if status is AttractionVersionStatus.included:
        expected = {chunk_type.value for chunk_type in _CHUNK_TYPE_ORDER}
        if set(sections) != expected:
            raise ValueError("included attractions require all five semantic sections")

    parsed_sections: list[SemanticSection] = []
    for chunk_type in _CHUNK_TYPE_ORDER:
        if chunk_type.value not in sections:
            continue
        section = _require_mapping(sections[chunk_type.value], f"section {chunk_type.value}")
        _require_exact_keys(section, _SECTION_KEYS, f"section {chunk_type.value}")
        _validate_provenance(section)
        parsed_sections.append(
            SemanticSection.model_validate(
                {
                    **dict(section),
                    "attraction_id": attraction_id,
                    "chunk_type": chunk_type,
                }
            )
        )
    return tuple(parsed_sections)


def _parse_attraction(
    value: object, *, destination: Destination
) -> AuthoringAttraction:
    attraction = _require_mapping(value, "attraction")
    _require_exact_keys(attraction, _ATTRACTION_KEYS, "attraction")

    metadata = AttractionVersionMetadata.model_validate(
        {
            "attraction_id": attraction["attraction_id"],
            "canonical_name": attraction["canonical_name"],
            "aliases": attraction["aliases"],
            "destination": destination,
            "category": attraction["category"],
            "tags": attraction["tags"],
            "status": attraction["status"],
        }
    )
    registry_key = attraction["registry_key"]
    if not isinstance(registry_key, str) or not registry_key.strip():
        raise ValueError("registry_key must be non-blank")
    return AuthoringAttraction(
        registry_key=registry_key,
        metadata=metadata,
        sections=_parse_sections(
            attraction["sections"],
            attraction_id=metadata.attraction_id,
            status=metadata.status,
        ),
    )


def load_authoring_file(path: Path) -> AuthoringDocument:
    payload = yaml.load(
        path.read_text(encoding="utf-8"),
        Loader=_UniqueKeyLoader,
    )
    root = _require_mapping(payload, "authoring document")
    _require_exact_keys(root, _ROOT_KEYS, "authoring document")
    if root["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("unsupported authoring schema version")

    destination = _parse_destination(root["destination"])
    raw_attractions = root["attractions"]
    if not isinstance(raw_attractions, list):
        raise ValueError("attractions must be a list")

    attractions: list[AuthoringAttraction] = []
    registry_keys: set[str] = set()
    attraction_ids: set[UUID] = set()
    for raw_attraction in raw_attractions:
        attraction = _parse_attraction(raw_attraction, destination=destination)
        if attraction.registry_key in registry_keys:
            raise ValueError("duplicate registry_key")
        if attraction.metadata.attraction_id in attraction_ids:
            raise ValueError("duplicate attraction_id")
        registry_keys.add(attraction.registry_key)
        attraction_ids.add(attraction.metadata.attraction_id)
        attractions.append(attraction)

    return AuthoringDocument(
        schema_version="rag-v2-authoring-v1",
        destination=destination,
        attractions=tuple(attractions),
    )


def load_authoring_directory(directory: Path) -> tuple[AuthoringDocument, ...]:
    return tuple(
        load_authoring_file(path)
        for path in sorted(directory.glob("*.yaml"), key=lambda item: str(item))
    )
