import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point

from pipeline.routing import route_worker
from pipeline.scoring_integration import (
    CandidateNode,
    assemble_score_record,
    score_candidate_route,
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
