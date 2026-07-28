from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point

from scripts.run_network_build import (
    build_audited_correction_edges,
    load_audited_shelter_corrections,
)


def test_load_audited_shelter_corrections_filters_to_approved_covered_lines(
    tmp_path: Path,
):
    path = tmp_path / "corrections.geojson"
    gdf = gpd.GeoDataFrame(
        [
            {
                "audit_id": "keep",
                "status": "approved",
                "covered": "yes",
                "geometry": LineString([(103.8, 1.3), (103.8001, 1.3001)]),
            },
            {
                "audit_id": "draft",
                "status": "draft",
                "covered": "yes",
                "geometry": LineString([(103.8, 1.3), (103.8001, 1.3001)]),
            },
            {
                "audit_id": "uncovered",
                "status": "approved",
                "covered": "no",
                "geometry": LineString([(103.8, 1.3), (103.8001, 1.3001)]),
            },
            {
                "audit_id": "point",
                "status": "approved",
                "covered": "yes",
                "geometry": Point(103.8, 1.3),
            },
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(path, driver="GeoJSON")

    corrections = load_audited_shelter_corrections(path)

    assert corrections.crs.to_epsg() == 3414
    assert corrections["audit_id"].tolist() == ["keep"]


def test_build_audited_correction_edges_snaps_approved_lines_to_existing_nodes():
    nodes = gpd.GeoDataFrame(
        geometry=[Point(0.0, 0.0), Point(10.0, 0.0), Point(40.0, 0.0)],
        crs="EPSG:3414",
    )
    corrections = gpd.GeoDataFrame(
        [
            {
                "audit_id": "ok",
                "source": "qa",
                "geometry": LineString([(0.4, 0.0), (5.0, 1.0), (9.7, 0.0)]),
            },
            {
                "audit_id": "too-far",
                "source": "qa",
                "geometry": LineString([(0.4, 0.0), (5.0, 1.0), (25.0, 0.0)]),
            },
        ],
        crs="EPSG:3414",
    )

    edges, report = build_audited_correction_edges(corrections, nodes, snap_max_m=1.0)

    assert report["candidate_lines"] == 2
    assert report["added_edges"] == 1
    assert report["skipped_edges"] == 1
    assert len(edges) == 1
    assert list(edges.iloc[0].geometry.coords)[0] == (0.0, 0.0)
    assert list(edges.iloc[0].geometry.coords)[-1] == (10.0, 0.0)
    assert edges.iloc[0]["is_covered"] == 1
    assert edges.iloc[0]["covered"] == "yes"
    assert edges.iloc[0]["synth_class"] == "AUDITED_SHELTER_CORRECTION"
