import json
from pathlib import Path

from scripts.rebuild_network_debug import build_debug_geojson, rebuild_debug_geojson


def test_build_debug_geojson_uses_final_and_osm_residual_points() -> None:
    report = {
        "residual_components_gt_50_final": [
            {
                "size": 64,
                "lat": 1.3001,
                "lon": 103.8001,
                "gap_m": 12.5,
                "class": "CLIP_EDGE",
                "evidence": "dist_to_boundary=4.0m (<20m)",
            }
        ],
        "residual_components_gt_50_osm_only": [
            {
                "size": 72,
                "lat": 1.4001,
                "lon": 103.9001,
                "gap_m": 20.0,
                "class": "PRIVATE_ESTATE",
                "evidence": "private access",
            }
        ],
    }

    payload = build_debug_geojson(report)

    assert payload["type"] == "FeatureCollection"
    assert payload["shiok_debug_kind"] == "compact_residual_points_from_conflation_qa"
    assert len(payload["features"]) == 2
    assert payload["features"][0]["geometry"] == {
        "type": "Point",
        "coordinates": [103.8001, 1.3001],
    }
    assert payload["features"][0]["properties"]["source"] == "residual_components_gt_50_final"
    assert payload["features"][1]["properties"]["source"] == "residual_components_gt_50_osm_only"


def test_rebuild_debug_geojson_writes_output(tmp_path: Path) -> None:
    qa_path = tmp_path / "conflation_qa_island.json"
    output_path = tmp_path / "island_debug.geojson"
    qa_path.write_text(
        json.dumps(
            {
                "residual_components_gt_50_final": [
                    {
                        "size": 64,
                        "lat": 1.3001,
                        "lon": 103.8001,
                        "gap_m": 12.5,
                        "class": "CLIP_EDGE",
                        "evidence": "dist_to_boundary=4.0m (<20m)",
                    }
                ],
                "residual_components_gt_50_osm_only": [],
            }
        ),
        encoding="utf-8",
    )

    report = rebuild_debug_geojson(qa_path, output_path)

    assert report["ok"] is True
    assert report["feature_count"] == 1
    assert (
        json.loads(output_path.read_text(encoding="utf-8"))["features"][0]["properties"]["class"]
        == "CLIP_EDGE"
    )
