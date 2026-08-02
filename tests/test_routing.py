from shapely.geometry import LineString

from pipeline.routing import RoutingGraph, route_worker


def test_routing_detour_cap():
    # Simple grid network
    # 0 -- 1 -- 2 (short path, uncovered, len 10+10 = 20)
    # |         |
    # 3 -- 4 -- 5 (long path, covered, len 15+15+15 = 45)
    # origin: 0, dest: 2

    edges_dict = {
        "u": [0, 1, 0, 3, 4, 5, 2],
        "v": [1, 2, 3, 4, 5, 2, 5],  # 2-5 connection
        "length_m": [10.0, 10.0, 15.0, 15.0, 15.0, 5.0, 5.0],
        "is_covered": [0, 0, 1, 1, 1, 1, 1],
    }
    # shortest = 0-1-2 (len = 20)
    # sheltered = 0-3-4-5-2 (len = 15+15+15+5 = 50)

    od_pairs = {0: [2]}
    # lambda=3.0 makes uncovered cost = 20 * 4 = 80.
    # sheltered path cost = 50 (since it's covered).
    # So sheltered routing will pick the 50m path.
    shelter_lambda = 3.0
    detour_budget = 1.25

    # Budget = 20 * 1.25 = 25. The covered path (50) is > 25.
    # It should fallback to shortest.

    res = route_worker((edges_dict, od_pairs, shelter_lambda, detour_budget))
    assert len(res) == 1

    assert res[0]["routing_type"] == "shortest_due_to_detour"
    assert res[0]["length_m"] == 20.0


def test_routing_sheltered_success():
    # 0 -- 1 -- 2 (short path, uncovered, len 10+10 = 20)
    # |         |
    # 3 -- 4 -- 2 (long path, covered, len 10+10+2 = 22)
    # origin: 0, dest: 2
    edges_dict = {
        "u": [0, 1, 0, 3, 4],
        "v": [1, 2, 3, 4, 2],
        "length_m": [10.0, 10.0, 10.0, 10.0, 2.0],
        "is_covered": [0, 0, 1, 1, 1],
    }

    # shortest = 0-1-2 (len = 20)
    # sheltered = 0-3-4-2 (len = 22)
    # budget = 20 * 1.25 = 25. 22 is within budget!

    od_pairs = {0: [2]}
    res = route_worker((edges_dict, od_pairs, 0.6, 1.25))

    assert res[0]["routing_type"] == "sheltered"
    assert res[0]["length_m"] == 22.0
    assert res[0]["covered_m"] == 22.0
    assert res[0]["covered_ratio"] == 1.0


def test_routing_lambda_zero():
    # 0 -- 1 -- 2 (short path, uncovered, len 20)
    # |         |
    # 3 -- 4 -- 2 (long path, covered, len 22)
    # origin: 0, dest: 2
    edges_dict = {
        "u": [0, 1, 0, 3, 4],
        "v": [1, 2, 3, 4, 2],
        "length_m": [10.0, 10.0, 10.0, 10.0, 2.0],
        "is_covered": [0, 0, 1, 1, 1],
    }
    od_pairs = {0: [2]}

    # Lambda = 0 means we do NOT care about shelter.
    # Cost is equal to geometric length.
    # Therefore, shortest path (20) wins over covered path (22).
    # Since it's within budget (1.25*20 = 25), it might be labeled 'sheltered'
    # because it fell within budget, BUT the path length will be 20.0, not 22.0.
    res = route_worker((edges_dict, od_pairs, 0.0, 1.25))

    assert res[0]["length_m"] == 20.0
    assert res[0]["covered_m"] == 0.0
    assert res[0]["covered_ratio"] == 0.0


def test_reusable_routing_graph_matches_route_worker():
    edges_dict = {
        "u": [0, 1, 0, 3, 4],
        "v": [1, 2, 3, 4, 2],
        "length_m": [10.0, 10.0, 10.0, 10.0, 2.0],
        "is_covered": [0, 0, 1, 1, 1],
    }
    od_pairs = {0: [2]}

    worker_result = route_worker((edges_dict, od_pairs, 0.6, 1.25))[0]
    graph_result = RoutingGraph.from_edges_dict(edges_dict).route(
        od_pairs,
        0.6,
        1.25,
        include_geometry=False,
    )[0]

    assert graph_result["routing_type"] == worker_result["routing_type"]
    assert graph_result["length_m"] == worker_result["length_m"]
    assert graph_result["covered_m"] == worker_result["covered_m"]
    assert graph_result["covered_ratio"] == worker_result["covered_ratio"]
    assert graph_result["geometry"] is None
    assert graph_result["shortest_path_edges"] == []
    assert graph_result["sheltered_path_edges"] == []
    assert graph_result["path_edges"] == []


def test_routing_exports_shortest_and_sheltered_path_edges_with_geometry():
    edges_dict = {
        "u": [0, 1, 0, 3, 4],
        "v": [1, 2, 3, 4, 2],
        "length_m": [10.0, 10.0, 10.0, 10.0, 2.0],
        "is_covered": [0, 0, 1, 1, 1],
        "geometry": [
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 0), (20, 0)]),
            LineString([(0, 0), (0, 10)]),
            LineString([(0, 10), (10, 10)]),
            LineString([(10, 10), (20, 0)]),
        ],
    }

    result = RoutingGraph.from_edges_dict(edges_dict).route({(0.0, 0.0): [(20.0, 0.0)]}, 0.6, 1.25)[
        0
    ]

    assert result["routing_type"] == "sheltered"
    assert [edge["is_covered"] for edge in result["shortest_path_edges"]] == [False, False]
    assert [edge["is_covered"] for edge in result["sheltered_path_edges"]] == [True, True, True]
    assert result["path_edges"] == result["sheltered_path_edges"]


def test_routing_orients_reversed_edge_geometry_in_route_order():
    edges_dict = {
        "u": [(0.0, 0.0), (10.0, 0.0)],
        "v": [(10.0, 0.0), (20.0, 0.0)],
        "length_m": [10.0, 10.0],
        "is_covered": [0, 0],
        "geometry": [
            LineString([(10.0, 0.0), (0.0, 0.0)]),
            LineString([(20.0, 0.0), (10.0, 0.0)]),
        ],
    }

    result = RoutingGraph.from_edges_dict(edges_dict).route(
        {(0.0, 0.0): [(20.0, 0.0)]},
        0.0,
        1.25,
    )[0]

    assert list(result["shortest_path_edges"][0]["geometry"].coords) == [
        (0.0, 0.0),
        (10.0, 0.0),
    ]
    assert list(result["shortest_path_edges"][1]["geometry"].coords) == [
        (10.0, 0.0),
        (20.0, 0.0),
    ]
    assert list(result["shortest_geometry"].coords) == [
        (0.0, 0.0),
        (10.0, 0.0),
        (20.0, 0.0),
    ]


def test_routing_preserves_pedestrian_qa_metadata_on_path_edges():
    edges_dict = {
        "u": [(0.0, 0.0)],
        "v": [(10.0, 0.0)],
        "length_m": [10.0],
        "is_covered": [0],
        "geometry": [LineString([(0, 0), (10, 0)])],
        "access": ["yes"],
        "foot": ["designated"],
        "foot:conditional": ["no @ (construction)"],
        "footway": ["crossing"],
        "sidewalk": ["both"],
        "crossing": ["traffic_signals"],
        "crossing:markings": ["zebra"],
        "traffic_calming": ["table"],
        "shelter": ["no"],
        "weather_protection": ["no"],
        "name": ["Test Walk"],
    }

    result = RoutingGraph.from_edges_dict(edges_dict).route({(0.0, 0.0): [(10.0, 0.0)]}, 0.6, 1.25)[
        0
    ]
    edge = result["path_edges"][0]

    assert edge["access"] == "yes"
    assert edge["foot"] == "designated"
    assert edge["foot:conditional"] == "no @ (construction)"
    assert edge["footway"] == "crossing"
    assert edge["sidewalk"] == "both"
    assert edge["crossing"] == "traffic_signals"
    assert edge["crossing:markings"] == "zebra"
    assert edge["traffic_calming"] == "table"
    assert edge["shelter"] == "no"
    assert edge["weather_protection"] == "no"
    assert edge["name"] == "Test Walk"
