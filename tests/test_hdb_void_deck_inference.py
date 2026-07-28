import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from scripts.run_network_build import (
    build_hdb_precinct_connector_edges,
    build_hdb_void_deck_anchor_edges,
    build_hdb_void_deck_edges,
    compute_polygon_match_ratio,
    split_osm_building_shelter_layers,
)


def test_split_osm_building_shelter_layers_matches_hdb_residential_postcodes():
    buildings = gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "building": "residential",
                "addr:postcode": "560234",
                "geometry": Polygon([(0, 0), (20, 0), (20, 10), (0, 10)]),
            },
            {
                "id": 2,
                "building": "commercial",
                "addr:postcode": "560235",
                "geometry": Polygon([(30, 0), (50, 0), (50, 10), (30, 10)]),
            },
            {
                "id": 3,
                "building": "roof",
                "geometry": Polygon([(60, 0), (80, 0), (80, 10), (60, 10)]),
            },
        ],
        crs="EPSG:3414",
    )
    hdb_points = gpd.GeoDataFrame(
        [{"postal_code": "560234", "geometry": Point(10, 5)}],
        crs="EPSG:3414",
    )

    roof_gdf, hdb_footprints = split_osm_building_shelter_layers(buildings, hdb_points)

    assert roof_gdf["id"].tolist() == [3]
    assert hdb_footprints["id"].tolist() == [1]
    assert hdb_footprints.iloc[0]["source_layer"] == "inferred_hdb_void_deck"


def test_build_hdb_void_deck_edges_requires_line_inside_hdb_footprint():
    hdb_footprints = gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "postal_code": "560234",
                "geometry": Polygon([(0, 0), (40, 0), (40, 20), (0, 20)]),
            }
        ],
        crs="EPSG:3414",
    )
    nodes = gpd.GeoDataFrame(
        {"node": [(1.0, 10.0), (39.0, 10.0), (80.0, 80.0)]},
        geometry=[Point(1, 10), Point(39, 10), Point(80, 80)],
        crs="EPSG:3414",
    )

    edges, report = build_hdb_void_deck_edges(hdb_footprints, nodes, node_search_m=2.0)

    assert report["candidate_buildings"] == 1
    assert report["buildings_with_edges"] == 1
    assert report["added_edges"] == 1
    assert edges.iloc[0]["is_covered"] == 1
    assert edges.iloc[0]["synth_class"] == "INFERRED_HDB_VOID_DECK"
    assert list(edges.iloc[0].geometry.coords) == [(1.0, 10.0), (39.0, 10.0)]


def test_build_hdb_void_deck_anchor_edges_adds_block_origin_connector():
    hdb_footprints = gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "postal_code": "560234",
                "geometry": Polygon([(0, 0), (20, 0), (20, 20), (0, 20)]),
            }
        ],
        crs="EPSG:3414",
    )
    nodes = gpd.GeoDataFrame(
        {"node": [(22.0, 10.0), (80.0, 80.0)]},
        geometry=[Point(22, 10), Point(80, 80)],
        crs="EPSG:3414",
    )

    edges, report = build_hdb_void_deck_anchor_edges(
        hdb_footprints,
        nodes,
        node_search_m=3.0,
        coverage_buffer_m=3.0,
    )

    assert report["candidate_buildings"] == 1
    assert report["buildings_with_edges"] == 1
    assert report["added_edges"] == 1
    assert edges.iloc[0]["is_covered"] == 1
    assert edges.iloc[0]["synth_class"] == "INFERRED_HDB_VOID_DECK_ANCHOR"
    assert list(edges.iloc[0].geometry.coords)[-1] == (22.0, 10.0)


def test_build_hdb_precinct_connector_edges_links_nearby_nodes_inside_hdb_buffer():
    hdb_footprints = gpd.GeoDataFrame(
        [
            {
                "id": 1,
                "postal_code": "560234",
                "geometry": Polygon([(0, 0), (20, 0), (20, 20), (0, 20)]),
            }
        ],
        crs="EPSG:3414",
    )
    nodes = gpd.GeoDataFrame(
        {"node": [(5.0, 10.0), (25.0, 10.0), (100.0, 100.0)]},
        geometry=[Point(5, 10), Point(25, 10), Point(100, 100)],
        crs="EPSG:3414",
    )

    edges, report = build_hdb_precinct_connector_edges(
        hdb_footprints,
        nodes,
        coverage_buffer_m=6.0,
        max_pair_m=30.0,
        nearest_neighbours=2,
    )

    assert report["candidate_buildings"] == 1
    assert report["buildings_with_edges"] == 1
    assert report["added_edges"] >= 1
    assert set(edges["synth_class"]) == {"INFERRED_HDB_PRECINCT_CONNECTOR"}
    assert set(edges["is_covered"]) == {1}
    assert all(edge.length <= 30.0 for edge in edges.geometry)


def test_compute_polygon_match_ratio_marks_existing_footways_under_roof():
    edges = gpd.GeoDataFrame(
        [
            {"geometry": LineString([(0, 0), (10, 0)])},
            {"geometry": LineString([(0, 20), (10, 20)])},
        ],
        crs="EPSG:3414",
    )
    roof = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-1, -1), (11, -1), (11, 1), (-1, 1)])}],
        crs="EPSG:3414",
    )

    ratios = compute_polygon_match_ratio(edges, roof, buffer_m=0.0, label="test roof")

    assert ratios.tolist() == [1.0, 0.0]
