import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point

from pipeline.bus import BusStopCandidate
from pipeline.routing import RoutingGraph, route_worker
from pipeline.scoring_integration import (
    CandidateNode,
    CrossingCounter,
    assemble_score_record,
    build_provenance,
    bus_connectivity_from_routed_candidates,
    json_safe_score_record,
    load_postal_universe_points,
    nearest_graph_node_in_components,
    score_candidate_route,
    select_bus_stop_candidates,
    select_mrt_exit_candidates,
)

PARAMS = {
    "shelter_lambda": 0.6,
    "detour_budget": 1.25,
    "transit_access": {
        "full_credit_m": 400.0,
        "linear_floor_m": 800.0,
        "score_at_800m": 40.0,
        "zero_credit_m": 1200.0,
        "bus_interchange_full_credit_m": 200.0,
    },
    "bus_connectivity": {
        "routed_max_m": 250.0,
        "straight_line_candidate_m": 300.0,
        "full_credit_wait_min": 2.0,
        "zero_credit_wait_min": 15.0,
    },
    "crossing_friction": {
        "penalty_per_crossing": 20.0,
    },
}

WEIGHTS = {
    "transit_access": 0.35,
    "bus_connectivity": 0.20,
    "rain_shelter": 0.25,
    "heat_comfort": 0.15,
    "crossing_friction": 0.05,
}


def test_node_set_includes_all_nearest_exits_and_second_station_within_ratio():
    nodes = [(0.0, 0.0), (10.0, 0.0), (12.0, 0.0), (30.0, 0.0)]
    node_xy_array = np.asarray(nodes, dtype=float)

    exits = gpd.GeoDataFrame(
        [
            {
                "STATION_NA": "ALPHA MRT STATION",
                "EXIT_CODE": "Exit 1",
                "OBJECTID": 1,
                "geometry": Point(10.0, 0.0),
            },
            {
                "STATION_NA": "ALPHA MRT STATION",
                "EXIT_CODE": "Exit 2",
                "OBJECTID": 2,
                "geometry": Point(12.0, 0.0),
            },
            {
                "STATION_NA": "BETA MRT STATION",
                "EXIT_CODE": "Exit 1",
                "OBJECTID": 3,
                "geometry": Point(12.0, 0.0),
            },
            {
                "STATION_NA": "GAMMA MRT STATION",
                "EXIT_CODE": "Exit 1",
                "OBJECTID": 4,
                "geometry": Point(30.0, 0.0),
            },
        ],
        crs="EPSG:3414",
    )

    candidates = select_mrt_exit_candidates(Point(0.0, 0.0), exits, nodes, node_xy_array)

    assert [candidate.name for candidate in candidates] == [
        "ALPHA MRT STATION Exit 1",
        "ALPHA MRT STATION Exit 2",
        "BETA MRT STATION Exit 1",
    ]
    assert {candidate.station_name for candidate in candidates} == {
        "ALPHA MRT STATION",
        "BETA MRT STATION",
    }


class FakeBusIndex:
    def nearby_stop_candidates(self, _postal_point, _straight_line_radius_m):
        return [
            BusStopCandidate(
                bus_stop_code="54321",
                description="OPP TEST BLK",
                graph_node=(100.0, 0.0),
                straight_line_m=90.0,
                snap_distance_m=3.0,
                service_headways_min={("10", 1): 8.0, ("11", 1): 12.0},
            ),
            BusStopCandidate(
                bus_stop_code="99999",
                description="NO SERVICE",
                graph_node=(120.0, 0.0),
                straight_line_m=120.0,
                snap_distance_m=4.0,
                service_headways_min={},
            ),
        ]


def test_bus_stop_candidates_require_frequency_data():
    candidates = select_bus_stop_candidates(
        Point(0.0, 0.0),
        FakeBusIndex(),  # type: ignore[arg-type]
        straight_line_radius_m=300.0,
    )

    assert len(candidates) == 1
    assert candidates[0].node_type == "bus_stop"
    assert candidates[0].name == "OPP TEST BLK"
    assert candidates[0].exit_code == "54321"
    assert round(candidates[0].expected_wait_min or 0.0, 3) == 2.4


def test_nearest_graph_node_in_components_respects_component_and_cap():
    edges = pd.DataFrame(
        [
            {"u": (0.0, 0.0), "v": (10.0, 0.0), "length_m": 10.0, "is_covered": 0},
            {"u": (100.0, 0.0), "v": (110.0, 0.0), "length_m": 10.0, "is_covered": 0},
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (10.0, 0.0), (100.0, 0.0), (110.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    allowed_component = {routing_graph.component_membership[routing_graph.node_map[(100.0, 0.0)]]}

    result = nearest_graph_node_in_components(
        Point(98.0, 0.0),
        nodes,
        node_xy,
        routing_graph,
        allowed_component,
        max_distance_m=5.0,
    )

    assert result == ((100.0, 0.0), 2.0)
    assert (
        nearest_graph_node_in_components(
            Point(98.0, 0.0),
            nodes,
            node_xy,
            routing_graph,
            allowed_component,
            max_distance_m=1.0,
        )
        is None
    )


def test_bus_connectivity_reuses_combined_routing_results():
    bus_candidate = CandidateNode(
        node_type="bus_stop",
        name="OPP TEST BLK",
        station_name="OPP TEST BLK",
        exit_code="54321",
        graph_node=(100.0, 0.0),
        straight_line_m=90.0,
        snap_distance_m=3.0,
        service_headways_min={("10", 1): 8.0, ("11", 1): 12.0},
    )
    mrt_candidate = CandidateNode(
        node_type="mrt_lrt_exit",
        name="TEST MRT STATION Exit 1",
        station_name="TEST MRT STATION",
        exit_code="Exit 1",
        graph_node=(500.0, 0.0),
        straight_line_m=500.0,
        snap_distance_m=2.0,
    )

    result = bus_connectivity_from_routed_candidates(
        [
            {"destination": (100.0, 0.0), "shortest_length_m": 180.0},
            {"destination": (500.0, 0.0), "shortest_length_m": 500.0},
        ],
        {
            (100.0, 0.0): [bus_candidate],
            (500.0, 0.0): [mrt_candidate],
        },
        routed_max_m=250.0,
        straight_line_stop_count=1,
    )

    assert result.routed_stop_count == 1
    assert result.straight_line_stop_count == 1
    assert result.service_count == 2
    assert result.nearest_routed_m == 180.0
    assert round(result.expected_wait_min or 0.0, 3) == 2.4


def test_record_assembly_marks_missing_bus_data_partial_without_fabricating_subscore():
    candidate = CandidateNode(
        node_type="mrt_lrt_exit",
        name="TEST MRT STATION Exit 1",
        station_name="TEST MRT STATION",
        exit_code="Exit 1",
        graph_node=(100.0, 0.0),
        straight_line_m=300.0,
        snap_distance_m=2.0,
    )
    route_result = {
        "routing_type": "sheltered",
        "length_m": 320.0,
        "covered_m": 160.0,
        "covered_ratio": 0.5,
        "shortest_length_m": 300.0,
        "shortest_covered_ratio": 0.2,
        "path_edges": [
            {
                "length_m": 160.0,
                "is_covered": False,
                "geometry": LineString([(0.0, 0.0), (160.0, 0.0)]),
            },
            {
                "length_m": 160.0,
                "is_covered": True,
                "geometry": LineString([(160.0, 0.0), (320.0, 0.0)]),
            },
        ],
    }

    candidate_score = score_candidate_route(
        candidate,
        route_result,
        PARAMS,
        WEIGHTS,
        crossing_count=1,
        bus_data_available=False,
    )
    record = assemble_score_record(
        "123456",
        [candidate_score],
        "2026-07-26T14:39:38+00:00",
        {"subscore_status": {"bus": "pending_lta_datamall_account_key"}},
    )

    assert record["state"] == "SCORED_PARTIAL"
    assert record["subscores"] == {
        "access": 100.0,
        "bus": None,
        "rain": 50.0,
        "heat": 50.0,
        "crossing": 80.0,
    }
    assert record["total"] == 59.0
    assert record["exposure_gaps"][0]["len_m"] == 160.0


def test_record_assembly_scores_real_zero_bus_as_zero_not_partial():
    candidate = CandidateNode(
        node_type="mrt_lrt_exit",
        name="TEST MRT STATION Exit 1",
        station_name="TEST MRT STATION",
        exit_code="Exit 1",
        graph_node=(100.0, 0.0),
        straight_line_m=300.0,
        snap_distance_m=2.0,
    )
    route_result = {
        "routing_type": "sheltered",
        "length_m": 300.0,
        "covered_m": 300.0,
        "covered_ratio": 1.0,
        "shortest_length_m": 300.0,
        "shortest_covered_ratio": 1.0,
        "path_edges": [],
    }

    candidate_score = score_candidate_route(
        candidate,
        route_result,
        PARAMS,
        WEIGHTS,
        crossing_count=0,
        bus_expected_wait_min=None,
        bus_data_available=True,
    )
    record = assemble_score_record("123456", [candidate_score], None, {})

    assert record["state"] == "SCORED"
    assert record["subscores"]["bus"] == 0.0
    assert record["total"] == 80.0


def test_record_assembly_selects_highest_scoring_candidate():
    low = {
        "total": 62.0,
        "subscores": {"access": 100.0, "bus": None, "rain": 20.0, "heat": 20.0, "crossing": 100.0},
        "best_node": {"name": "SHORT EXPOSED", "routed_m": 100.0},
        "paths": {"shortest_m": 100.0},
        "exposure_gaps": [],
    }
    high = {
        "total": 75.0,
        "subscores": {"access": 95.0, "bus": None, "rain": 80.0, "heat": 80.0, "crossing": 100.0},
        "best_node": {"name": "COVERED EXIT", "routed_m": 180.0},
        "paths": {"shortest_m": 180.0},
        "exposure_gaps": [],
    }

    record = assemble_score_record("123456", [low, high], None, {})

    assert record["best_node"]["name"] == "COVERED EXIT"
    assert record["total"] == 75.0


def test_load_postal_universe_points_filters_ready_rows_and_preserves_requested_order(
    tmp_path: Path,
):
    universe_path = tmp_path / "postal_universe.parquet"
    pd.DataFrame(
        [
            {
                "postal_code": "000001",
                "status": "READY_TO_SCORE",
                "x": 100.0,
                "y": 200.0,
            },
            {
                "postal_code": "000002",
                "status": "NEEDS_GEOCODE",
                "x": None,
                "y": None,
            },
            {
                "postal_code": "000003",
                "status": "READY_TO_SCORE",
                "x": 300.0,
                "y": 400.0,
            },
        ]
    ).to_parquet(universe_path, index=False)

    points = load_postal_universe_points(
        universe_path,
        postal_codes=["000003", "000002", "000001"],
    )

    assert points.crs.to_epsg() == 3414
    assert points["postal_code"].tolist() == ["000003", "000001"]
    assert [(point.x, point.y) for point in points.geometry] == [(300.0, 400.0), (100.0, 200.0)]


def test_build_provenance_records_selected_network_and_postal_universe_paths():
    crossing_counter = CrossingCounter(None, None, eps_m=20.0, min_samples=2)

    provenance = build_provenance(
        PARAMS,
        crossing_counter,
        bus_data_available=True,
        network_path=Path("processed/network_island.parquet"),
        postal_universe_path=Path("processed/postal_universe_candidate_full_registered.parquet"),
    )

    assert provenance["routing"]["network"] == "processed\\network_island.parquet"
    assert (
        provenance["postal_universe"]
        == "processed\\postal_universe_candidate_full_registered.parquet"
    )
    assert provenance["subscore_status"]["bus"] == "real"


def test_route_worker_coalesces_length_column_into_length_m():
    edges_dict = {
        "u": [0, 1],
        "v": [1, 2],
        "length": [7.0, 11.0],
        "is_covered": [1, 0],
    }

    result = route_worker((edges_dict, {0: [2]}, 0.6, 1.25))

    assert result[0]["length_m"] == 18.0
    assert result[0]["covered_m"] == 7.0


def test_json_safe_score_record_serializes_private_geometry_payload():
    record = {
        "postal": "123456",
        "_geometry": {
            "shortest": LineString([(0.0, 0.0), (1.0, 0.0)]),
            "sheltered": LineString([(0.0, 0.0), (0.0, 1.0)]),
            "shortest_path_edges": [
                {
                    "length_m": 1.0,
                    "geometry": LineString([(0.0, 0.0), (1.0, 0.0)]),
                }
            ],
            "sheltered_path_edges": [
                {
                    "length_m": 1.0,
                    "geometry": LineString([(0.0, 0.0), (0.0, 1.0)]),
                }
            ],
            "exposure_gap_edges": [
                {
                    "length_m": 1.0,
                    "geometry": LineString([(1.0, 1.0), (2.0, 2.0)]),
                }
            ],
        },
    }

    safe = json_safe_score_record(record)

    json.dumps(safe, sort_keys=True)
    assert safe["_geometry"]["shortest"] == "LINESTRING (0 0, 1 0)"
    assert safe["_geometry"]["sheltered"] == "LINESTRING (0 0, 0 1)"
    assert safe["_geometry"]["shortest_path_edges"][0]["geometry"] == "LINESTRING (0 0, 1 0)"
    assert safe["_geometry"]["sheltered_path_edges"][0]["geometry"] == "LINESTRING (0 0, 0 1)"
    assert safe["_geometry"]["exposure_gap_edges"][0]["geometry"] == "LINESTRING (1 1, 2 2)"
