import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from pipeline.shade import (
    SHADE_ONLY_NOTE,
    compute_edge_shade_ratio,
    prepare_shade_proxy_geometries,
)


def test_prepare_shade_proxy_buffers_greenery_lines_for_heat_only():
    features = gpd.GeoDataFrame(
        [{"geometry": LineString([(0, 0), (10, 0)])}],
        crs="EPSG:3414",
    )

    shade = prepare_shade_proxy_geometries(
        features,
        source_key="nparks_park_connector_loop",
        line_buffer_m=2.0,
    )

    assert len(shade) == 1
    assert shade.iloc[0]["score_use"] == SHADE_ONLY_NOTE
    assert shade.iloc[0]["shade_weight"] == 0.5
    assert shade.iloc[0].geometry.area > 0


def test_prepare_shade_proxy_buffers_points_but_keeps_polygons():
    features = gpd.GeoDataFrame(
        [{"geometry": Point(0, 0)}, {"geometry": Polygon([(10, 0), (20, 0), (20, 5), (10, 5)])}],
        crs="EPSG:3414",
    )

    shade = prepare_shade_proxy_geometries(
        features,
        source_key="nparks_heritage_trees",
        point_buffer_m=3.0,
    )

    assert len(shade) == 2
    assert all(shade.geometry.area > 0)


def test_heritage_road_green_buffers_are_supported_heat_only_polygons():
    features = gpd.GeoDataFrame(
        [{"geometry": Polygon([(0, 0), (20, 0), (20, 8), (0, 8)])}],
        crs="EPSG:3414",
    )

    shade = prepare_shade_proxy_geometries(
        features,
        source_key="nparks_heritage_road_green_buffers",
    )

    assert len(shade) == 1
    assert shade.iloc[0]["source_layer"] == "nparks_heritage_road_green_buffers"
    assert shade.iloc[0]["score_use"] == SHADE_ONLY_NOTE
    assert shade.iloc[0].geometry.equals(features.iloc[0].geometry)


def test_compute_edge_shade_ratio_measures_partial_route_shade():
    edges = gpd.GeoDataFrame(
        [{"geometry": LineString([(0, 0), (10, 0)])}],
        crs="EPSG:3414",
    )
    shade = gpd.GeoDataFrame(
        [{"geometry": Polygon([(0, -1), (5, -1), (5, 1), (0, 1)])}],
        crs="EPSG:3414",
    )

    ratios = compute_edge_shade_ratio(edges, shade)

    assert ratios.iloc[0] == 0.5
