import geopandas as gpd
from shapely.geometry import LineString, Point

from scripts.run_network_build import (
    graph_nodes_from_edges,
    nearest_point_and_index_on_geometry,
    nearest_point_on_geometry,
    split_edges_at_points,
)


def test_split_edges_at_points_creates_routable_host_node():
    edges = gpd.GeoDataFrame(
        [
            {
                "geometry": LineString([(0, 0), (10, 0)]),
                "length_m": 10.0,
                "highway": "footway",
            }
        ],
        crs="EPSG:3414",
    )

    split_edges = split_edges_at_points(edges, {0: [Point(4, 0)]})

    assert len(split_edges) == 2
    assert sorted(round(float(geom.length), 1) for geom in split_edges.geometry) == [4.0, 6.0]
    endpoints = {
        (round(coord[0], 1), round(coord[1], 1))
        for geom in split_edges.geometry
        for coord in [geom.coords[0], geom.coords[-1]]
    }
    assert (4.0, 0.0) in endpoints


def test_linkway_snap_targets_ignore_raw_mid_edge_nodes_until_edge_split():
    edges = gpd.GeoDataFrame(
        [
            {
                "geometry": LineString([(0, 0), (10, 0)]),
                "length_m": 10.0,
                "highway": "footway",
            }
        ],
        crs="EPSG:3414",
    )
    raw_nodes = gpd.GeoSeries([Point(0, 0), Point(5, 0), Point(10, 0)], crs="EPSG:3414")
    routable_nodes = graph_nodes_from_edges(edges)

    raw_snap, raw_dist = nearest_point_on_geometry(
        raw_nodes,
        raw_nodes.sindex,
        Point(5, 0),
        max_distance=0.1,
    )
    route_snap, route_dist = nearest_point_on_geometry(
        routable_nodes.geometry,
        routable_nodes.sindex,
        Point(5, 0),
        max_distance=0.1,
    )
    edge_snap, edge_dist, edge_idx = nearest_point_and_index_on_geometry(
        edges.geometry,
        edges.sindex,
        Point(5, 0),
        max_distance=0.1,
    )

    assert raw_snap is not None
    assert raw_dist == 0.0
    assert route_snap is None
    assert route_dist == float("inf")
    assert edge_snap is not None
    assert edge_dist == 0.0
    assert edge_idx == 0
