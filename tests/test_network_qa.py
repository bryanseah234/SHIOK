import json
from pathlib import Path

from pipeline.network_qa import validate_network_qa


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def valid_report() -> dict:
    residual = {
        "size": 64,
        "lat": 1.3001,
        "lon": 103.8001,
        "gap_m": 12.5,
        "class": "CLIP_EDGE",
        "evidence": "dist_to_boundary=4.0m (<20m)",
    }
    return {
        "nodes": 1000,
        "edges": 1200,
        "mean_edge_length_m": 18.5,
        "connected_components_count": 3,
        "top_5_component_sizes": [900, 50, 25, 15, 10],
        "residual_components_gt_50_osm_only": [residual],
        "residual_components_gt_50_final": [residual],
        "real_disconnection_count_osm_only": 0,
        "real_disconnection_count_final": 0,
        "flags": [],
        "covered_edge_length_m_osm_tags": 100.0,
        "covered_edge_length_m_lta_bridge_underpass_match": 20.0,
        "covered_edge_length_m_osm_roof_canopy": 15.0,
        "covered_edge_length_m_inferred_hdb_precinct_footways": 30.0,
        "covered_edge_length_m_inferred_hdb_point_footways": 40.0,
        "covered_edge_length_m_inferred_hdb_void_deck": 25.0,
        "shade_proxy_edge_count": 12,
        "shade_proxy_weighted_length_m": 55.0,
        "shade_proxy_sources": {
            "nparks_heritage_trees": {
                "status": "loaded",
                "features_raw": 1,
                "features_in_scope": 1,
                "proxy_polygons": 1,
            },
            "nparks_heritage_road_green_buffers": {
                "status": "loaded",
                "features_raw": 1,
                "features_in_scope": 1,
                "proxy_polygons": 1,
            },
            "nparks_nature_ways": {
                "status": "loaded",
                "features_raw": 1,
                "features_in_scope": 1,
                "proxy_polygons": 1,
            },
            "nparks_park_connector_loop": {
                "status": "loaded",
                "features_raw": 1,
                "features_in_scope": 1,
                "proxy_polygons": 1,
            },
            "nparks_tracks": {
                "status": "loaded",
                "features_raw": 1,
                "features_in_scope": 1,
                "proxy_polygons": 1,
            },
        },
    }


def test_validate_network_qa_accepts_clean_report(tmp_path: Path):
    qa_path = tmp_path / "conflation_qa_island.json"
    debug_path = tmp_path / "island_debug.geojson"
    write_json(qa_path, valid_report())
    debug_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    ok, summary = validate_network_qa(qa_path, debug_path)

    assert ok, summary
    assert summary["metrics"]["real_disconnection_count_final"] == 0
    assert summary["metrics"]["final_residual_components_gt_50"] == 1


def test_validate_network_qa_rejects_real_disconnections_and_flags(tmp_path: Path):
    qa_path = tmp_path / "conflation_qa_island.json"
    debug_path = tmp_path / "island_debug.geojson"
    report = valid_report()
    report["real_disconnection_count_final"] = 1
    report["flags"] = ["final_real_disconnections_present"]
    report["residual_components_gt_50_final"][0]["class"] = "REAL_DISCONNECTION"
    write_json(qa_path, report)
    debug_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    ok, summary = validate_network_qa(qa_path, debug_path)

    assert not ok
    assert "real_disconnection_count_final must be 0, got 1" in summary["errors"]
    assert "qa flags present: final_real_disconnections_present" in summary["errors"]
    assert "residual_components_gt_50_final[0] is REAL_DISCONNECTION" in summary["errors"]


def test_validate_network_qa_requires_debug_artifact(tmp_path: Path):
    qa_path = tmp_path / "conflation_qa_pilot.json"
    write_json(qa_path, valid_report())

    ok, summary = validate_network_qa(qa_path, tmp_path / "pilot_debug.geojson")

    assert not ok
    assert any(error.startswith("missing debug GeoJSON:") for error in summary["errors"])


def test_validate_network_qa_requires_production_shade_sources(tmp_path: Path):
    qa_path = tmp_path / "conflation_qa_island.json"
    debug_path = tmp_path / "island_debug.geojson"
    report = valid_report()
    report["shade_proxy_sources"]["nparks_tracks"]["status"] = "missing"
    report["shade_proxy_edge_count"] = 0
    write_json(qa_path, report)
    debug_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    ok, summary = validate_network_qa(
        qa_path,
        debug_path,
        require_production_sources=True,
    )

    assert not ok
    assert "shade_proxy_edge_count must be > 0, got 0" in summary["errors"]
    assert "shade_proxy_sources.nparks_tracks.status must be loaded" in summary["errors"]


def test_validate_network_qa_rejects_tree_as_rain_metric(tmp_path: Path):
    qa_path = tmp_path / "conflation_qa_island.json"
    debug_path = tmp_path / "island_debug.geojson"
    report = valid_report()
    report["covered_edge_length_m_nparks_shade"] = 10.0
    write_json(qa_path, report)
    debug_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    ok, summary = validate_network_qa(
        qa_path,
        debug_path,
        require_production_sources=True,
    )

    assert not ok
    assert any(
        error.startswith("shade/tree metrics must not be counted as rain shelter")
        for error in summary["errors"]
    )
