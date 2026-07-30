import geopandas as gpd
from shapely.geometry import LineString

from pipeline.route_feedback import audit_report, classify_feedback_segments


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
