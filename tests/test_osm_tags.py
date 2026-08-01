import pytest

from pipeline.osm_tags import load_osm_tag_schema
from pipeline.routing import EDGE_METADATA_COLUMNS
from scripts.run_network_build import (
    OSM_COVERED_TAG_VALUES,
    OSM_EXPLICIT_SHELTER_QUERY_KEYS,
    OSM_NETWORK_EXTRA_ATTRIBUTES,
    OSM_SHELTER_NEGATIVE_VALUES,
)


def test_osm_tag_schema_contains_reviewed_production_tags():
    schema = load_osm_tag_schema()

    assert "covered" in schema.network_extra_attributes
    assert "weather_protection" in schema.network_extra_attributes
    assert "access:conditional" in schema.network_extra_attributes
    assert "oneway:foot" in schema.network_extra_attributes
    assert "crossing:signals" in schema.network_extra_attributes
    assert "tactile_paving" in schema.network_extra_attributes
    assert "railway" in schema.network_extra_attributes
    assert "building:levels" in schema.network_extra_attributes
    assert "building_arcade" in schema.covered_values
    assert "roof" in schema.covered_values
    assert "shelter" in schema.covered_values
    assert "canopy" in schema.covered_values
    assert "partial" in schema.covered_values
    assert "no" in schema.negative_shelter_values
    assert "building" in schema.explicit_shelter_query_keys


def test_network_build_constants_are_loaded_from_osm_tag_schema():
    schema = load_osm_tag_schema()

    assert OSM_NETWORK_EXTRA_ATTRIBUTES == list(schema.network_extra_attributes)
    assert OSM_COVERED_TAG_VALUES == schema.covered_values
    assert OSM_SHELTER_NEGATIVE_VALUES == schema.negative_shelter_values
    assert OSM_EXPLICIT_SHELTER_QUERY_KEYS == schema.explicit_shelter_query_keys


def test_routing_preserves_all_configured_osm_edge_metadata_columns():
    schema = load_osm_tag_schema()

    missing = sorted(set(schema.network_extra_attributes) - set(EDGE_METADATA_COLUMNS))

    assert missing == []


def test_osm_tag_schema_rejects_query_keys_missing_from_tags_as_columns(tmp_path):
    schema_path = tmp_path / "osm_tags.yaml"
    schema_path.write_text(
        """
version: test
network_extra_attributes: [covered]
covered_values: ["yes"]
tunnel_covered_values: ["yes"]
indoor_covered_values: ["yes"]
location_covered_values: [indoor]
negative_shelter_values: ["no"]
explicit_shelter_query_keys: [covered, shelter]
explicit_shelter_tags_as_columns: [covered]
shelter_yes_values: ["yes"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="explicit_shelter_query_keys"):
        load_osm_tag_schema(schema_path)
