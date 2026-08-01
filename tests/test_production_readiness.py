import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.export import export_static_artifacts
from scripts.production_readiness import build_readiness_report, vercel_readiness
from tests.test_export import sample_record


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_universe(path: Path, rows: int = 1) -> None:
    table = pa.table(
        {
            "postal_code": [f"{index:06d}" for index in range(1, rows + 1)],
            "status": ["READY_TO_SCORE"] * rows,
        }
    )
    pq.write_table(table, path)


def write_production_island_qa(path: Path) -> None:
    payload = {
        "nodes": 100,
        "edges": 120,
        "mean_edge_length_m": 18.0,
        "connected_components_count": 1,
        "top_5_component_sizes": [100],
        "residual_components_gt_50_osm_only": [],
        "residual_components_gt_50_final": [],
        "real_disconnection_count_osm_only": 0,
        "real_disconnection_count_final": 0,
        "flags": [],
        "covered_edge_length_m_osm_tags": 1.0,
        "covered_edge_length_m_lta_bridge_underpass_match": 1.0,
        "covered_edge_length_m_osm_roof_canopy": 1.0,
        "covered_edge_length_m_inferred_hdb_precinct_footways": 1.0,
        "covered_edge_length_m_inferred_hdb_point_footways": 1.0,
        "covered_edge_length_m_inferred_hdb_void_deck": 1.0,
        "shade_proxy_edge_count": 1,
        "shade_proxy_weighted_length_m": 1.0,
        "shade_proxy_sources": {
            "nparks_heritage_road_green_buffers": {
                "status": "loaded",
                "features_raw": 1,
                "features_in_scope": 1,
                "proxy_polygons": 1,
            },
            "nparks_heritage_trees": {
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
    write_json(path, payload)


def test_vercel_readiness_prefers_root_project_settings(tmp_path: Path):
    write_json(
        tmp_path / ".vercel" / "project.json",
        {
            "projectId": "prj_test",
            "projectName": "sgshiok",
            "settings": {"rootDirectory": "web"},
        },
    )
    write_json(
        tmp_path / "web" / ".vercel" / "project.json",
        {"projectId": "prj_test", "projectName": "old-name"},
    )

    report = vercel_readiness(tmp_path, tmp_path / "web")

    assert report["linked"] is True
    assert report["project_name"] == "sgshiok"
    assert report["root_directory_ok"] is True
    assert report["warnings"] == ["root and web Vercel project names differ but project ID matches"]


def test_build_readiness_report_accepts_minimal_valid_current_state(tmp_path: Path):
    web_dir = tmp_path / "web"
    bundle_dir = web_dir / "public" / "data" / "generated_test"
    export_static_artifacts([sample_record("123456")], output_dir=bundle_dir)
    write_json(web_dir / "data-bundle.json", {"bundle": "generated_test"})
    write_json(
        tmp_path / ".vercel" / "project.json",
        {
            "projectId": "prj_test",
            "projectName": "sgshiok",
            "settings": {"rootDirectory": "web"},
        },
    )

    summary_path = tmp_path / "processed" / "postal_universe_candidate_full_registered_summary.json"
    universe_path = tmp_path / "processed" / "postal_universe_candidate_full_registered.parquet"
    write_json(
        summary_path,
        {
            "mode": "candidate_full_registered",
            "total_unique_postals": 1,
            "ready_to_score": 1,
            "needs_geocode": 0,
            "source_stats": [],
            "source_only_counts": {},
            "warnings": [],
        },
    )
    write_universe(universe_path, rows=1)
    params_path = tmp_path / "params.yaml"
    params_path.write_text("onemap:\n  client_delay_sec: 2.0\n", encoding="utf-8")
    qa_path = tmp_path / "qa" / "conflation_qa_island.json"
    debug_path = tmp_path / "qa" / "island_debug.geojson"
    write_production_island_qa(qa_path)
    debug_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    ok, report = build_readiness_report(
        project_root=tmp_path,
        web_dir=web_dir,
        bundle_dir=bundle_dir,
        summary_path=summary_path,
        universe_path=universe_path,
        params_path=params_path,
        qa_path=qa_path,
        debug_path=debug_path,
        network_path=tmp_path / "unused_network.parquet",
        postal_universe_path=universe_path,
    )

    assert ok, report
    assert report["bundle"]["manifest_record_count"] == 1
    assert report["bundle"]["state_total_matches_manifest"] is True
    assert report["bundle"]["static_validation"]["geometry_postals_with_route_segments"] == 1
    assert report["network"]["ok"] is True
    assert report["vercel"]["root_directory_ok"] is True
    assert report["features"]["incorporated"]["bus_as_transit_direct_fallback"] is True
    assert report["features"]["incorporated"]["ura_no_dwelling_units_postal_source"] is True
    assert "124443" in report["features"]["not_incorporated"]["ura_expanded_scores_live"]
    assert (
        "complete accepted source-of-record"
        in report["features"]["not_incorporated"]["canonical_140k_postal_universe"]
    )
    assert (
        "outlier review/rescore"
        in report["features"]["not_incorporated"]["overture_addresses_sg_candidate"]
    )
    assert (
        "has not been collected/evaluated yet"
        in report["features"]["not_incorporated"]["onemap_walk_validation_gate"]
    )


def test_build_readiness_report_warns_when_bundle_predates_network(tmp_path: Path):
    web_dir = tmp_path / "web"
    bundle_dir = web_dir / "public" / "data" / "generated_test"
    export_static_artifacts([sample_record("123456")], output_dir=bundle_dir)
    write_json(web_dir / "data-bundle.json", {"bundle": "generated_test"})
    write_json(
        tmp_path / ".vercel" / "project.json",
        {
            "projectId": "prj_test",
            "projectName": "sgshiok",
            "settings": {"rootDirectory": "web"},
        },
    )

    summary_path = tmp_path / "processed" / "postal_universe_candidate_full_registered_summary.json"
    universe_path = tmp_path / "processed" / "postal_universe_candidate_full_registered.parquet"
    write_json(
        summary_path,
        {
            "mode": "candidate_full_registered",
            "total_unique_postals": 1,
            "ready_to_score": 1,
            "needs_geocode": 0,
            "source_stats": [],
            "source_only_counts": {},
            "warnings": [],
        },
    )
    write_universe(universe_path, rows=1)
    params_path = tmp_path / "params.yaml"
    params_path.write_text("onemap:\n  client_delay_sec: 2.0\n", encoding="utf-8")
    qa_path = tmp_path / "qa" / "conflation_qa_island.json"
    debug_path = tmp_path / "qa" / "island_debug.geojson"
    write_production_island_qa(qa_path)
    debug_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    network_path = tmp_path / "processed" / "network_island.parquet"
    network_path.write_bytes(b"newer network")

    old_time = 1_800_000_000
    new_time = old_time + 120
    os.utime(bundle_dir / "manifest.json", (old_time, old_time))
    os.utime(network_path, (new_time, new_time))

    ok, report = build_readiness_report(
        project_root=tmp_path,
        web_dir=web_dir,
        bundle_dir=bundle_dir,
        summary_path=summary_path,
        universe_path=universe_path,
        params_path=params_path,
        qa_path=qa_path,
        debug_path=debug_path,
        network_path=network_path,
        postal_universe_path=universe_path,
    )

    assert ok, report
    assert report["bundle"]["freshness"]["active_bundle_reflects_current_network"] is False
    assert report["bundle"]["freshness"]["stale_seconds"] == 120
    assert any(
        "active bundle predates current network build" in warning for warning in report["warnings"]
    )
