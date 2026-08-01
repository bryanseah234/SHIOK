import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from pipeline.route_feedback import audit_geojson, audit_report, classify_feedback_segments


def test_feedback_audit_marks_uncovered_user_shelter_as_missing_source():
    segments = gpd.GeoDataFrame(
        [
            {
                "postal": "560225",
                "destination": "Mayflower",
                "segment_index": 0,
                "label": "sheltered",
                "length_m": 10.0,
                "geometry": LineString([(0, 0), (10, 0)]),
            }
        ],
        crs="EPSG:3414",
    )
    network = gpd.GeoDataFrame(
        [{"is_covered": 0, "highway": "footway", "geometry": LineString([(0, 0), (10, 0)])}],
        crs="EPSG:3414",
    )

    audited = classify_feedback_segments(segments, network, search_m=2.0)

    assert audited.iloc[0]["classification"] == "routable_but_uncovered_or_missing_shelter_source"
    assert bool(audited.iloc[0]["needs_model_qa"]) is True


def test_feedback_audit_flags_bridge_endpoint_snap_when_bridge_evidence_is_nearby():
    segments = gpd.GeoDataFrame(
        [
            {
                "postal": "560231",
                "destination": "Mayflower",
                "segment_index": 0,
                "label": "covered_bridge",
                "length_m": 10.0,
                "geometry": LineString([(0, 0), (10, 0)]),
            }
        ],
        crs="EPSG:3414",
    )
    network = gpd.GeoDataFrame(
        [
            {
                "is_covered": 1,
                "source_layer": "overhead_bridge_underpass",
                "highway": "footway",
                "geometry": LineString([(0, 1), (10, 1)]),
            }
        ],
        crs="EPSG:3414",
    )

    audited = classify_feedback_segments(segments, network, search_m=2.0)

    assert (
        audited.iloc[0]["classification"] == "bridge_underpass_evidence_nearby_check_endpoint_snap"
    )
    assert bool(audited.iloc[0]["needs_model_qa"]) is False


def test_feedback_audit_flags_covered_component_gap():
    segments = gpd.GeoDataFrame(
        [
            {
                "postal": "560231",
                "destination": "Mayflower",
                "segment_index": 0,
                "label": "void_deck",
                "length_m": 100.0,
                "geometry": LineString([(0, 0), (100, 0)]),
            }
        ],
        crs="EPSG:3414",
    )
    network = gpd.GeoDataFrame(
        [
            {
                "is_covered": 1,
                "source_layer": "inferred_hdb_precinct",
                "synth_class": "INFERRED_HDB_PRECINCT_CONNECTOR",
                "highway": "footway",
                "geometry": LineString([(0, 0), (10, 0)]),
            },
            {
                "is_covered": 1,
                "source_layer": "inferred_hdb_point_footway",
                "synth_class": "INFERRED_HDB_POINT_FOOTWAY",
                "highway": "footway",
                "geometry": LineString([(90, 0), (100, 0)]),
            },
        ],
        crs="EPSG:3414",
    )

    audited = classify_feedback_segments(segments, network, search_m=2.0)
    row = audited.iloc[0]

    assert row["classification"] == "hdb_void_deck_component_gap"
    assert bool(row["needs_model_qa"]) is True
    assert bool(row["same_component"]) is False
    assert row["start_component_id"] != row["end_component_id"]
    assert row["endpoint_component_gap_m"] == 100.0


def test_feedback_report_counts_routes_and_classifications():
    audited = gpd.GeoDataFrame(
        [
            {
                "postal": "560700",
                "destination": "Ang Mo Kio",
                "segment_index": 0,
                "label": "void_deck",
                "length_m": 10.0,
                "classification": "missing_hdb_void_deck_connector",
                "needs_model_qa": True,
                "nearby_edge_count": 1,
                "nearby_covered_edge_count": 0,
                "nearest_edge_m": 0.5,
                "nearest_covered_edge_m": None,
                "nearby_sources": {},
                "geometry": LineString([(0, 0), (10, 0)]),
            }
        ],
        crs="EPSG:3414",
    )

    report = audit_report(audited)

    assert report["route_count"] == 1
    assert report["classification_counts"] == {"missing_hdb_void_deck_connector": 1}


def test_feedback_report_serializes_missing_distances_as_null():
    audited = gpd.GeoDataFrame(
        [
            {
                "postal": "560234",
                "destination": "Mayflower",
                "segment_index": 0,
                "label": "sheltered",
                "length_m": 10.0,
                "classification": "routable_but_uncovered_or_missing_shelter_source",
                "needs_model_qa": True,
                "nearby_edge_count": 1,
                "nearby_covered_edge_count": 0,
                "nearest_edge_m": 0.0,
                "nearest_covered_edge_m": pd.NA,
                "nearby_sources": {},
                "geometry": LineString([(0, 0), (10, 0)]),
            }
        ],
        crs="EPSG:3414",
    )

    report = audit_report(audited)

    assert report["segments"][0]["nearest_covered_edge_m"] is None
    geojson = audit_geojson(audited)
    assert geojson["features"][0]["properties"]["nearest_covered_edge_m"] is None
