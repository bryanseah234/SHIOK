import geopandas as gpd
from shapely.geometry import LineString, Point

from scripts.run_network_build import split_edges_at_points


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
