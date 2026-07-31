"""Reviewed OSM tag schema for network-build evidence extraction."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OSM_TAG_SCHEMA_PATH = PROJECT_ROOT / "pipeline" / "config" / "osm_tags.yaml"


@dataclass(frozen=True)
class OsmTagSchema:
    version: str
    network_extra_attributes: tuple[str, ...]
    covered_values: frozenset[str]
    tunnel_covered_values: frozenset[str]
    indoor_covered_values: frozenset[str]
    location_covered_values: frozenset[str]
    negative_shelter_values: frozenset[str]
    explicit_shelter_query_keys: tuple[str, ...]
    explicit_shelter_tags_as_columns: tuple[str, ...]
    shelter_yes_values: frozenset[str]


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    values = data.get(key)
    if not isinstance(values, list) or not values:
        raise ValueError(f"osm tag schema requires a non-empty list for {key}")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"osm tag schema {key} contains a non-string/blank value")
        result.append(value.strip())
    duplicates = sorted({value for value in result if result.count(value) > 1})
    if duplicates:
        raise ValueError(f"osm tag schema {key} contains duplicates: {duplicates}")
    return result


def load_osm_tag_schema(path: Path = DEFAULT_OSM_TAG_SCHEMA_PATH) -> OsmTagSchema:
    with open(path, "r", encoding="utf-8") as f:
        data: Any = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise TypeError(f"osm tag schema must be a mapping: {path}")

    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("osm tag schema requires a version")

    network_extra_attributes = _string_list(data, "network_extra_attributes")
    explicit_shelter_query_keys = _string_list(data, "explicit_shelter_query_keys")
    explicit_shelter_tags_as_columns = _string_list(data, "explicit_shelter_tags_as_columns")

    missing_query_columns = sorted(
        set(explicit_shelter_query_keys) - set(explicit_shelter_tags_as_columns)
    )
    if missing_query_columns:
        raise ValueError(
            "osm tag schema explicit_shelter_query_keys must also be listed in "
            f"explicit_shelter_tags_as_columns: {missing_query_columns}"
        )

    return OsmTagSchema(
        version=version.strip(),
        network_extra_attributes=tuple(network_extra_attributes),
        covered_values=frozenset(_string_list(data, "covered_values")),
        tunnel_covered_values=frozenset(_string_list(data, "tunnel_covered_values")),
        indoor_covered_values=frozenset(_string_list(data, "indoor_covered_values")),
        location_covered_values=frozenset(_string_list(data, "location_covered_values")),
        negative_shelter_values=frozenset(_string_list(data, "negative_shelter_values")),
        explicit_shelter_query_keys=tuple(explicit_shelter_query_keys),
        explicit_shelter_tags_as_columns=tuple(explicit_shelter_tags_as_columns),
        shelter_yes_values=frozenset(_string_list(data, "shelter_yes_values")),
    )
