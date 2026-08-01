import geopandas as gpd
from shapely.geometry import LineString

from pipeline.connector_candidates import audit_connector_candidates, audit_geojson, audit_summary


def test_connector_candidate_with_hdb_overlap_requires_review_not_scoring():
    candidates = gpd.GeoDataFrame(
        [
            {
                "postal": "560231",
                "destination": "Mayflower",
                "segment_index": 6,
                "label": "void_deck",
                "geometry": LineString([(0, 0), (20, 0)]),
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
                "geometry": LineString([(0, 1), (20, 1)]),
            }
        ],
        crs="EPSG:3414",
    )

    audited = audit_connector_candidates(candidates, network, search_m=10, evidence_buffer_m=3)
    row = audited.iloc[0]

    assert row["candidate_classification"] == "hdb_source_overlap_review"
    assert row["promotion_status"] == "manual_review_required_not_scoring"
    assert row["hdb_overlap_ratio"] == 1.0
    assert row["covered_overlap_ratio"] == 1.0

    summary = audit_summary(audited)
    assert summary["classification_counts"] == {"hdb_source_overlap_review": 1}
    assert summary["candidates"][0]["promotion_status"] == "manual_review_required_not_scoring"


def test_connector_candidate_without_source_overlap_stays_insufficient():
    candidates = gpd.GeoDataFrame(
        [
            {
                "postal": "560225",
                "destination": "Mayflower",
                "segment_index": 0,
                "label": "void_deck",
                "geometry": LineString([(0, 0), (20, 0)]),
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
                "geometry": LineString([(100, 100), (120, 100)]),
            }
        ],
        crs="EPSG:3414",
    )

    audited = audit_connector_candidates(candidates, network, search_m=10, evidence_buffer_m=3)

    assert audited.iloc[0]["candidate_classification"] == "insufficient_source_overlap"
    assert audited.iloc[0]["hdb_overlap_ratio"] == 0.0
    geojson = audit_geojson(audited)
    assert geojson["features"][0]["properties"]["candidate_classification"] == (
        "insufficient_source_overlap"
    )


def test_connector_candidate_summary_handles_empty_network():
    candidates = gpd.GeoDataFrame(
        [
            {
                "postal": "560225",
                "destination": "Mayflower",
                "segment_index": 0,
                "label": "void_deck",
                "geometry": LineString([(0, 0), (20, 0)]),
            }
        ],
        crs="EPSG:3414",
    )
    network = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")

    audited = audit_connector_candidates(candidates, network)
    summary = audit_summary(audited)

    assert summary["classification_counts"] == {"missing_network_evidence": 1}
    assert summary["candidates"][0]["hdb_overlap_ratio"] == 0.0
