import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point

from pipeline.bus import BusStopCandidate
from pipeline.routing import RoutingGraph, route_worker
from pipeline.scoring_integration import (
    NETWORK_PATH,
    CandidateNode,
    CrossingCounter,
    annotate_no_transit_reason,
    assemble_score_record,
    build_bus_stop_access_connector_route,
    build_mrt_lrt_exit_access_connector_route,
    build_provenance,
    bus_access_connector_is_plausible,
    bus_connectivity_from_routed_candidates,
    bus_route_direct_fallback_reason,
    bus_route_should_use_direct_fallback,
    bus_route_trust_rejection_reason,
    candidate_debug_rows,
    direct_bus_fallback_candidate_scores,
    json_safe_score_record,
    load_postal_universe_points,
    nearest_graph_node_in_components,
    score_candidate_route,
    score_postal_row,
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
        "access_connector_search_m": 50.0,
        "access_connector_max_candidates": 24,
        "access_connector_max_walk_m": 1200.0,
        "access_connector_max_direct_ratio": 2.5,
        "access_connector_max_extra_m": 120.0,
        "access_connector_detour_ratio": 2.0,
        "access_connector_min_extra_m": 100.0,
        "access_connector_scale_min_extra_to_direct": True,
    },
    "bus_connectivity": {
        "routed_max_m": 250.0,
        "straight_line_candidate_m": 300.0,
        "straight_line_candidate_tolerance_m": 5.0,
        "access_connector_search_m": 50.0,
        "access_connector_max_candidates": 24,
        "access_connector_max_walk_m": 300.0,
        "access_connector_max_direct_ratio": 2.5,
        "access_connector_max_extra_m": 100.0,
        "direct_fallback_detour_ratio": 3.0,
        "direct_fallback_near_stop_detour_ratio": 2.0,
        "direct_fallback_min_extra_m": 100.0,
        "direct_fallback_scale_min_extra_to_direct": True,
        "direct_fallback_shortcut_ratio": 0.5,
        "direct_fallback_min_missing_m": 50.0,
        "road_centerline_guard_min_m": 50.0,
        "road_centerline_guard_min_ratio": 0.5,
        "road_centerline_guard_max_pedestrian_m": 25.0,
        "endpoint_snap_guard_min_m": 25.0,
        "endpoint_snap_guard_min_ratio": 0.6,
        "combined_connector_guard_min_m": 50.0,
        "combined_connector_guard_min_ratio": 0.5,
        "access_connector_trust_max_m": 40.0,
        "access_connector_trust_min_ratio": 0.2,
        "access_connector_near_stop_direct_m": 60.0,
        "access_connector_near_stop_max_walk_m": 125.0,
        "access_connector_near_stop_max_extra_m": 75.0,
        "access_connector_near_stop_trust_max_m": 50.0,
        "access_connector_short_walk_direct_m": 250.0,
        "access_connector_short_walk_max_walk_m": 250.0,
        "access_connector_short_walk_max_extra_m": 60.0,
        "access_connector_short_walk_trust_max_m": 50.0,
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


def test_default_scoring_network_is_island_graph():
    assert NETWORK_PATH.name == "network_island.parquet"


def sample_candidate(node_type: str = "mrt_lrt_exit") -> CandidateNode:
    return CandidateNode(
        node_type=node_type,
        name="Test Transit",
        station_name="Test",
        exit_code="Exit 1",
        graph_node=(10.0, 0.0),
        straight_line_m=100.0,
        snap_distance_m=2.0,
    )


def test_annotate_no_transit_reason_marks_no_candidates():
    provenance: dict[str, object] = {}

    annotate_no_transit_reason(
        provenance,
        candidates=[],
        route_distances=[],
        candidate_scores=[],
        access_zero_m=1200.0,
    )

    assert provenance["reason"] == "no_transit_candidates_selected"


def test_annotate_no_transit_reason_marks_graph_disconnected_candidates():
    provenance: dict[str, object] = {}

    annotate_no_transit_reason(
        provenance,
        candidates=[sample_candidate()],
        route_distances=[],
        candidate_scores=[],
        access_zero_m=1200.0,
    )

    assert provenance["reason"] == "transit_candidates_graph_disconnected"


def test_annotate_no_transit_reason_marks_routed_candidates_beyond_access_range():
    provenance: dict[str, object] = {}

    annotate_no_transit_reason(
        provenance,
        candidates=[sample_candidate()],
        route_distances=[1500.0, 1800.0],
        candidate_scores=[{"total": "NO_TRANSIT_IN_RANGE"}],
        access_zero_m=1200.0,
    )

    assert provenance["reason"] == "all_routed_transit_candidates_beyond_access_range"
    assert provenance["nearest_routed_m"] == 1500.0
    assert provenance["access_zero_credit_m"] == 1200.0


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


def test_direct_bus_fallback_scores_partial_without_routed_shelter_geometry():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(105.0, 0.0),
        straight_line_m=95.0,
        snap_distance_m=5.0,
        service_headways_min={("10", 1): 8.0, ("11", 1): 12.0},
        expected_wait_min=2.4,
        point_xy=(95.0, 0.0),
    )

    scores = direct_bus_fallback_candidate_scores(
        [candidate],
        Point(0.0, 0.0),
        PARAMS,
        WEIGHTS,
        include_geometry=True,
    )
    record = assemble_score_record("123456", scores, None, {})

    assert record["state"] == "SCORED_PARTIAL"
    assert record["best_node"]["type"] == "bus_stop"
    assert record["best_node"]["routed_m"] is None
    assert record["best_node"]["straight_line_m"] == 95.0
    assert record["paths"]["routing_type"] == "direct_bus_fallback_unrouted"
    assert record["subscores"]["access"] == 100.0
    assert record["subscores"]["bus"] == 96.9
    assert record["subscores"]["rain"] is None
    assert record["subscores"]["heat"] is None
    assert record["subscores"]["crossing"] is None
    assert record["_geometry"]["sheltered"].coords[-1] == (95.0, 0.0)


def test_bus_route_should_use_direct_fallback_for_implausible_graph_detour():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(400.0, 0.0),
        straight_line_m=60.0,
        snap_distance_m=5.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(60.0, 0.0),
    )

    assert bus_route_should_use_direct_fallback(
        candidate,
        {"shortest_length_m": 400.0},
        {
            "straight_line_candidate_m": 300.0,
            "direct_fallback_detour_ratio": 3.0,
            "direct_fallback_min_extra_m": 100.0,
        },
    )

    assert not bus_route_should_use_direct_fallback(
        candidate,
        {"shortest_length_m": 150.0},
        {
            "straight_line_candidate_m": 300.0,
            "direct_fallback_detour_ratio": 3.0,
            "direct_fallback_min_extra_m": 100.0,
        },
    )
    assert (
        bus_route_direct_fallback_reason(
            candidate,
            {"shortest_length_m": 5.0},
            {
                "straight_line_candidate_m": 300.0,
                "direct_fallback_shortcut_ratio": 0.5,
                "direct_fallback_min_missing_m": 50.0,
            },
        )
        == "implausibly_short_graph_route_to_datamall_bus_stop_within_direct_radius"
    )
    # 45m routed vs 60m direct = 75% ratio: past the shortcut check (50%) but the
    # crow-flies snap-bug guard (0.98) still catches it because any routed walk
    # shorter than the direct line is geometrically impossible.
    assert (
        bus_route_direct_fallback_reason(
            candidate,
            {"shortest_length_m": 45.0},
            {
                "straight_line_candidate_m": 300.0,
                "direct_fallback_shortcut_ratio": 0.5,
                "direct_fallback_min_missing_m": 50.0,
            },
        )
        == "route_shorter_than_crow_flies_direct"
    )

    boundary_candidate = CandidateNode(
        node_type="bus_stop",
        name="Boundary Stop",
        station_name="Boundary Stop",
        exit_code="54322",
        graph_node=(1000.0, 0.0),
        straight_line_m=303.0,
        snap_distance_m=5.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(303.0, 0.0),
    )
    boundary_route = {"shortest_length_m": 1000.0}

    assert not bus_route_should_use_direct_fallback(
        boundary_candidate,
        boundary_route,
        {
            "straight_line_candidate_m": 300.0,
            "direct_fallback_detour_ratio": 3.0,
            "direct_fallback_min_extra_m": 100.0,
        },
    )
    assert bus_route_should_use_direct_fallback(
        boundary_candidate,
        boundary_route,
        {
            "straight_line_candidate_m": 300.0,
            "straight_line_candidate_tolerance_m": 5.0,
            "direct_fallback_detour_ratio": 3.0,
            "direct_fallback_min_extra_m": 100.0,
        },
    )


def test_bus_route_direct_fallback_scales_extra_for_near_stop_detour():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Near Stop",
        station_name="Near Stop",
        exit_code="54323",
        graph_node=(95.0, 0.0),
        straight_line_m=40.0,
        snap_distance_m=5.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(40.0, 0.0),
    )

    assert (
        bus_route_direct_fallback_reason(
            candidate,
            {"shortest_length_m": 95.0},
            {
                "straight_line_candidate_m": 300.0,
                "direct_fallback_detour_ratio": 3.0,
                "direct_fallback_near_stop_detour_ratio": 2.0,
                "direct_fallback_min_extra_m": 100.0,
                "direct_fallback_scale_min_extra_to_direct": True,
            },
        )
        == "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"
    )
    assert not bus_route_should_use_direct_fallback(
        candidate,
        {"shortest_length_m": 75.0},
        {
            "straight_line_candidate_m": 300.0,
            "direct_fallback_detour_ratio": 3.0,
            "direct_fallback_near_stop_detour_ratio": 2.0,
            "direct_fallback_min_extra_m": 100.0,
            "direct_fallback_scale_min_extra_to_direct": True,
        },
    )


def test_bus_route_direct_fallback_allows_bounded_near_stop_detour():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Near Stop",
        station_name="Near Stop",
        exit_code="54324",
        graph_node=(115.0, 0.0),
        straight_line_m=50.0,
        snap_distance_m=5.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(50.0, 0.0),
    )

    assert (
        bus_route_direct_fallback_reason(
            candidate,
            {"shortest_length_m": 115.0},
            PARAMS["bus_connectivity"],
        )
        is None
    )


def test_bus_route_direct_fallback_catches_snap_bug_shorter_than_crow_flies():
    """
    Snap-bug guard: any routed walk shorter than the crow-flies direct distance
    (with a 2% coordinate-rounding tolerance) must fall back to direct-bus. See
    qa/bus_median_gap_diagnosis_20260804.md - 80/1611 bus_stop samples in the
    honesty55 bundle report project_shortest_m < direct_distance_m, which is
    geometrically impossible. Endpoint connectors snapping origin+destination
    to the same or adjacent graph nodes collapse the walk to near-zero.
    """
    # Postal 489929 shape: direct=109.2m, project_shortest=2.3m
    # ~2% of direct - caught by both existing shortcut check and new guard;
    # existing shortcut fires first for backward-compat with reason strings.
    candidate_489929 = CandidateNode(
        node_type="bus_stop",
        name="Bef Bedok Rd",
        station_name="Bef Bedok Rd",
        exit_code="83179",
        graph_node=(109.2, 0.0),
        straight_line_m=109.2,
        snap_distance_m=1.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(109.2, 0.0),
    )
    assert bus_route_should_use_direct_fallback(
        candidate_489929,
        {"shortest_length_m": 2.3},
        PARAMS["bus_connectivity"],
    )

    # Postal 465460 shape: direct=101.4m, project_shortest=13.7m
    # ~14% of direct - also caught by existing shortcut check.
    candidate_465460 = CandidateNode(
        node_type="bus_stop",
        name="Bus Stop 465460",
        station_name="Bus Stop 465460",
        exit_code="00000",
        graph_node=(101.4, 0.0),
        straight_line_m=101.4,
        snap_distance_m=1.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(101.4, 0.0),
    )
    assert bus_route_should_use_direct_fallback(
        candidate_465460,
        {"shortest_length_m": 13.7},
        PARAMS["bus_connectivity"],
    )

    # Postal 141037 shape: direct=23.7m, project_shortest=21.8m
    # ~92% of direct - slips past the 50% shortcut check but the 0.98 crow-flies
    # rule catches it. This is the residual snap-bug cohort the new guard
    # explicitly targets.
    candidate_141037 = CandidateNode(
        node_type="bus_stop",
        name="Bus Stop 141037",
        station_name="Bus Stop 141037",
        exit_code="00000",
        graph_node=(23.7, 0.0),
        straight_line_m=23.7,
        snap_distance_m=1.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(23.7, 0.0),
    )
    assert (
        bus_route_direct_fallback_reason(
            candidate_141037,
            {"shortest_length_m": 21.8},
            PARAMS["bus_connectivity"],
        )
        == "route_shorter_than_crow_flies_direct"
    )
    assert bus_route_should_use_direct_fallback(
        candidate_141037,
        {"shortest_length_m": 21.8},
        PARAMS["bus_connectivity"],
    )

    # Boundary case: direct=100m, project_shortest=99m (99% of direct - 1% under)
    # sits inside the 2% coordinate-rounding tolerance, so the guard MUST NOT
    # fire. This protects against downgrading routes that are only marginally
    # shorter than the crow-flies line due to rounding artefacts.
    candidate_boundary = CandidateNode(
        node_type="bus_stop",
        name="Boundary Stop",
        station_name="Boundary Stop",
        exit_code="00000",
        graph_node=(100.0, 0.0),
        straight_line_m=100.0,
        snap_distance_m=1.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(100.0, 0.0),
    )
    assert (
        bus_route_direct_fallback_reason(
            candidate_boundary,
            {"shortest_length_m": 99.0},
            PARAMS["bus_connectivity"],
        )
        is None
    )
    assert not bus_route_should_use_direct_fallback(
        candidate_boundary,
        {"shortest_length_m": 99.0},
        PARAMS["bus_connectivity"],
    )


def test_direct_bus_fallback_reports_crow_flies_distance_for_snap_bug():
    """
    When the snap-bug guard fires, the score record must report the crow-flies
    direct distance (~109m for the 489929 shape) rather than the impossible
    project_shortest_m (~2m). Rain/heat/crossing subscores are pending so the
    published state is SCORED_PARTIAL, matching existing direct-bus-fallback
    semantics.
    """
    from shapely.geometry import Point

    candidate = CandidateNode(
        node_type="bus_stop",
        name="Bef Bedok Rd",
        station_name="Bef Bedok Rd",
        exit_code="83179",
        graph_node=(109.2, 0.0),
        straight_line_m=109.2,
        snap_distance_m=1.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(109.2, 0.0),
    )
    postal_point = Point(0.0, 0.0)

    fallback_scores = direct_bus_fallback_candidate_scores(
        [candidate],
        postal_point,
        PARAMS,
        WEIGHTS,
    )

    assert len(fallback_scores) == 1
    score = fallback_scores[0]
    assert score["paths"]["shortest_m"] == round(109.2, 1)
    assert score["paths"]["routing_type"] == "direct_bus_fallback_unrouted"
    assert score["subscores"]["rain"] is None
    assert score["subscores"]["heat"] is None
    assert score["subscores"]["crossing"] is None


def test_bus_access_connector_appends_exposed_endpoint_to_plausible_graph_route():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (90.0, 0.0),
                "length_m": 90.0,
                "is_covered": 1,
                "geometry": LineString([(0.0, 0.0), (90.0, 0.0)]),
            },
            {
                "u": (0.0, 0.0),
                "v": (400.0, 0.0),
                "length_m": 400.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (400.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (90.0, 0.0), (400.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(400.0, 0.0),
        straight_line_m=95.0,
        snap_distance_m=300.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(100.0, 0.0),
    )

    route = build_bus_stop_access_connector_route(
        candidate=candidate,
        origin_node=(0.0, 0.0),
        routing_graph=routing_graph,
        nodes=nodes,
        node_xy=node_xy,
        params=PARAMS,
    )

    assert route is not None
    assert route["routing_type"] == "sheltered_with_bus_stop_access_connector"
    assert route["shortest_length_m"] == 100.0
    assert route["length_m"] == 100.0
    assert route["covered_m"] == 90.0
    assert round(route["covered_ratio"], 3) == 0.9
    assert route["path_edges"][-1]["source_layer"] == "bus_stop_access_connector"
    assert route["path_edges"][-1]["is_covered"] is False
    assert route["geometry"].coords[-1] == (100.0, 0.0)


def test_bus_access_connector_rejects_implausibly_short_route():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(400.0, 0.0),
        straight_line_m=180.0,
        snap_distance_m=300.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(180.0, 0.0),
    )

    assert not bus_access_connector_is_plausible(
        candidate,
        {"shortest_length_m": 40.0},
        PARAMS["bus_connectivity"],
    )
    assert bus_access_connector_is_plausible(
        candidate,
        {"shortest_length_m": 100.0},
        PARAMS["bus_connectivity"],
    )


def test_bus_access_connector_builder_rejects_implausibly_short_origin_snap():
    edges = pd.DataFrame(
        [
            {
                "u": (100.0, 0.0),
                "v": (105.0, 0.0),
                "length_m": 5.0,
                "is_covered": 0,
                "geometry": LineString([(100.0, 0.0), (105.0, 0.0)]),
            },
            {
                "u": (100.0, 0.0),
                "v": (400.0, 0.0),
                "length_m": 300.0,
                "is_covered": 0,
                "geometry": LineString([(100.0, 0.0), (400.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(100.0, 0.0), (105.0, 0.0), (400.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(400.0, 0.0),
        straight_line_m=180.0,
        snap_distance_m=290.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(110.0, 0.0),
    )

    route = build_bus_stop_access_connector_route(
        candidate=candidate,
        origin_node=(100.0, 0.0),
        routing_graph=routing_graph,
        nodes=nodes,
        node_xy=node_xy,
        params=PARAMS,
    )

    assert route is None


def test_mrt_lrt_exit_access_connector_replaces_bad_exit_snap():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (95.0, 0.0),
                "length_m": 400.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (95.0, 0.0)]),
            },
            {
                "u": (0.0, 0.0),
                "v": (105.0, 0.0),
                "length_m": 105.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (105.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (95.0, 0.0), (105.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    candidate = CandidateNode(
        node_type="mrt_lrt_exit",
        name="TEST MRT STATION Exit A",
        station_name="TEST MRT STATION",
        exit_code="Exit A",
        graph_node=(95.0, 0.0),
        straight_line_m=100.0,
        snap_distance_m=5.0,
        point_xy=(100.0, 0.0),
    )

    route = build_mrt_lrt_exit_access_connector_route(
        candidate=candidate,
        origin_node=(0.0, 0.0),
        routing_graph=routing_graph,
        nodes=nodes,
        node_xy=node_xy,
        params=PARAMS,
    )

    assert route is not None
    assert route["routing_type"] == "sheltered_with_mrt_lrt_exit_access_connector"
    assert route["shortest_length_m"] == 110.0
    assert route["mrt_lrt_exit_access_connector_m"] == 5.0
    assert route["path_edges"][-1]["source_layer"] == "mrt_lrt_exit_access_connector"


def test_score_postal_row_uses_mrt_lrt_exit_access_connector():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (95.0, 0.0),
                "length_m": 400.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (95.0, 0.0)]),
            },
            {
                "u": (0.0, 0.0),
                "v": (105.0, 0.0),
                "length_m": 105.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (105.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (95.0, 0.0), (105.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        [
            {
                "STATION_NA": "TEST MRT STATION",
                "EXIT_CODE": "Exit A",
                "OBJECTID": 1,
                "geometry": Point(100.0, 0.0),
            }
        ],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)

    record = score_postal_row(
        pd.Series({"postal_code": "123461", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        include_geometry=True,
        base_provenance={},
    )

    assert record["state"] == "SCORED_PARTIAL"
    assert record["best_node"]["type"] == "mrt_lrt_exit"
    assert record["best_node"]["routed_m"] == 110.0
    assert record["paths"]["routing_type"] == "sheltered_with_mrt_lrt_exit_access_connector"
    assert record["paths"]["mrt_lrt_exit_access_connector_m"] == 5.0
    assert record["provenance"]["mrt_lrt_exit_access_connector"]["candidate_count"] == 1
    assert record["_geometry"]["sheltered_path_edges"][-1]["source_layer"] == (
        "mrt_lrt_exit_access_connector"
    )


def test_bus_route_trust_rejects_bare_road_centerline_bus_access():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(180.0, 0.0),
        straight_line_m=175.0,
        snap_distance_m=0.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(180.0, 0.0),
    )
    route = {
        "shortest_length_m": 180.0,
        "shortest_path_edges": [
            {
                "length_m": 180.0,
                "highway": "primary",
                "source_layer": "",
                "confidence": "",
            }
        ],
    }

    assert (
        bus_route_trust_rejection_reason(candidate, route, PARAMS["bus_connectivity"])
        == "low_trust_bus_stop_road_centerline_route"
    )

    route["shortest_path_edges"][0]["foot"] = "yes"
    assert bus_route_trust_rejection_reason(candidate, route, PARAMS["bus_connectivity"]) is None


def test_bus_route_trust_rejects_dominant_unrouted_endpoint_snap():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(30.0, 0.0),
        straight_line_m=30.0,
        snap_distance_m=8.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(30.0, 0.0),
    )
    route = {
        "shortest_length_m": 50.0,
        "endpoint_snap_connector_m": 32.0,
        "shortest_path_edges": [
            {"length_m": 32.0, "source_layer": "origin_graph_snap_connector"},
            {"length_m": 18.0, "highway": "footway", "source_layer": "", "confidence": ""},
        ],
    }

    assert (
        bus_route_trust_rejection_reason(candidate, route, PARAMS["bus_connectivity"])
        == "dominant_unrouted_bus_endpoint_snap"
    )


def test_bus_route_trust_rejects_large_bus_stop_access_connector():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Opp Test Blk",
        station_name="Opp Test Blk",
        exit_code="54321",
        graph_node=(180.0, 0.0),
        straight_line_m=175.0,
        snap_distance_m=40.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(180.0, 0.0),
    )
    route = {
        "shortest_length_m": 190.0,
        "bus_stop_access_connector_m": 55.0,
        "shortest_path_edges": [
            {"length_m": 135.0, "highway": "footway", "source_layer": "", "confidence": ""},
            {"length_m": 55.0, "source_layer": "bus_stop_access_connector"},
        ],
    }

    assert (
        bus_route_trust_rejection_reason(candidate, route, PARAMS["bus_connectivity"])
        == "large_unrouted_bus_stop_access_connector"
    )


def test_bus_route_trust_rejects_combined_unrouted_connectors():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Connector Heavy Stop",
        station_name="Connector Heavy Stop",
        exit_code="54326",
        graph_node=(120.0, 0.0),
        straight_line_m=90.0,
        snap_distance_m=8.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(120.0, 0.0),
    )
    route = {
        "shortest_length_m": 120.0,
        "endpoint_snap_connector_m": 30.0,
        "bus_stop_access_connector_m": 35.0,
        "shortest_path_edges": [
            {"length_m": 30.0, "source_layer": "origin_graph_snap_connector"},
            {"length_m": 55.0, "highway": "footway", "source_layer": "", "confidence": ""},
            {"length_m": 35.0, "source_layer": "bus_stop_access_connector"},
        ],
    }

    assert (
        bus_route_trust_rejection_reason(candidate, route, PARAMS["bus_connectivity"])
        == "dominant_unrouted_bus_endpoint_and_access_connectors"
    )


def test_bus_route_trust_allows_bounded_near_stop_access_connector():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Near Test Stop",
        station_name="Near Test Stop",
        exit_code="54320",
        graph_node=(55.0, 0.0),
        straight_line_m=50.0,
        snap_distance_m=45.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(55.0, 0.0),
    )
    route = {
        "shortest_length_m": 115.0,
        "bus_stop_access_connector_m": 48.0,
        "shortest_path_edges": [
            {"length_m": 67.0, "highway": "footway", "source_layer": "", "confidence": ""},
            {"length_m": 48.0, "source_layer": "bus_stop_access_connector"},
        ],
    }

    assert bus_route_trust_rejection_reason(candidate, route, PARAMS["bus_connectivity"]) is None


def test_bus_route_trust_allows_bounded_short_walk_access_connector():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Short Walk Stop",
        station_name="Short Walk Stop",
        exit_code="54325",
        graph_node=(175.0, 0.0),
        straight_line_m=145.0,
        snap_distance_m=45.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=8.0,
        point_xy=(145.0, 0.0),
    )
    route = {
        "shortest_length_m": 175.0,
        "bus_stop_access_connector_m": 44.0,
        "shortest_path_edges": [
            {"length_m": 131.0, "highway": "footway", "source_layer": "", "confidence": ""},
            {"length_m": 44.0, "source_layer": "bus_stop_access_connector"},
        ],
    }

    assert bus_route_trust_rejection_reason(candidate, route, PARAMS["bus_connectivity"]) is None


class ConnectorBusIndex:
    def nearby_stop_candidates(self, _postal_point, _straight_line_radius_m):
        return [
            BusStopCandidate(
                bus_stop_code="54321",
                description="OPP TEST BLK",
                graph_node=(400.0, 0.0),
                straight_line_m=95.0,
                snap_distance_m=300.0,
                service_headways_min={("10", 1): 8.0},
                point_xy=(100.0, 0.0),
            )
        ]


class LargeConnectorBusIndex:
    def nearby_stop_candidates(self, _postal_point, _straight_line_radius_m):
        return [
            BusStopCandidate(
                bus_stop_code="54325",
                description="LARGE CONNECTOR STOP",
                graph_node=(400.0, 0.0),
                straight_line_m=95.0,
                snap_distance_m=300.0,
                service_headways_min={("10", 1): 8.0},
                point_xy=(100.0, 0.0),
            )
        ]


class CombinedConnectorBusIndex:
    def nearby_stop_candidates(self, _postal_point, _straight_line_radius_m):
        return [
            BusStopCandidate(
                bus_stop_code="54327",
                description="CONNECTOR HEAVY STOP",
                graph_node=(400.0, 0.0),
                straight_line_m=120.0,
                snap_distance_m=280.0,
                service_headways_min={("10", 1): 8.0},
                point_xy=(120.0, 0.0),
            )
        ]


class StillDetouringConnectorBusIndex:
    def nearby_stop_candidates(self, _postal_point, _straight_line_radius_m):
        return [
            BusStopCandidate(
                bus_stop_code="54326",
                description="STILL DETOURING CONNECTOR STOP",
                graph_node=(400.0, 0.0),
                straight_line_m=70.0,
                snap_distance_m=330.0,
                service_headways_min={("10", 1): 8.0},
                point_xy=(70.0, 0.0),
            )
        ]


class EndpointSnapBusIndex:
    def nearby_stop_candidates(self, _postal_point, _straight_line_radius_m):
        return [
            BusStopCandidate(
                bus_stop_code="54323",
                description="TEST STOP",
                graph_node=(90.0, 0.0),
                straight_line_m=100.0,
                snap_distance_m=10.0,
                service_headways_min={("10", 1): 8.0},
                point_xy=(100.0, 0.0),
            )
        ]


class BoundaryBusIndex:
    def __init__(self) -> None:
        self.requested_radius_m: float | None = None

    def nearby_stop_candidates(self, _postal_point, straight_line_radius_m):
        self.requested_radius_m = straight_line_radius_m
        if straight_line_radius_m < 303.0:
            return []
        return [
            BusStopCandidate(
                bus_stop_code="54322",
                description="BOUNDARY STOP",
                graph_node=(1000.0, 0.0),
                straight_line_m=303.0,
                snap_distance_m=4.0,
                service_headways_min={("10", 1): 8.0},
                point_xy=(303.0, 0.0),
            )
        ]


class LowTrustRoadBusIndex:
    def nearby_stop_candidates(self, _postal_point, _straight_line_radius_m):
        return [
            BusStopCandidate(
                bus_stop_code="54324",
                description="OPP ROAD STOP",
                graph_node=(180.0, 0.0),
                straight_line_m=175.0,
                snap_distance_m=0.0,
                service_headways_min={("10", 1): 8.0},
                point_xy=(180.0, 0.0),
            )
        ]


def test_score_postal_row_applies_bus_candidate_coordinate_tolerance():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (10.0, 0.0),
                "length_m": 10.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            },
            {
                "u": (1000.0, 0.0),
                "v": (1010.0, 0.0),
                "length_m": 10.0,
                "is_covered": 0,
                "geometry": LineString([(1000.0, 0.0), (1010.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (10.0, 0.0), (1000.0, 0.0), (1010.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        columns=["STATION_NA", "EXIT_CODE", "OBJECTID", "geometry"],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)
    bus_index = BoundaryBusIndex()

    record = score_postal_row(
        pd.Series({"postal_code": "557323", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        bus_index=bus_index,  # type: ignore[arg-type]
        include_geometry=True,
        base_provenance={},
    )

    assert bus_index.requested_radius_m == 305.0
    assert record["state"] == "SCORED_PARTIAL"
    assert record["best_node"]["straight_line_m"] == 303.0
    assert record["paths"]["routing_type"] == "direct_bus_fallback_unrouted"
    assert record["provenance"]["transit_node_set"] == {
        "mrt_lrt_exit_candidates": 0,
        "bus_stop_candidates_direct": 1,
        "bus_stop_candidate_radius_m": 300.0,
        "bus_stop_candidate_tolerance_m": 5.0,
        "bus_stop_candidate_selection_radius_m": 305.0,
    }
    assert record["provenance"]["direct_bus_fallback"]["radius_m"] == 300.0
    assert record["provenance"]["direct_bus_fallback"]["coordinate_tolerance_m"] == 5.0
    assert record["provenance"]["direct_bus_fallback"]["selection_radius_m"] == 305.0


def test_score_postal_row_uses_bus_access_connector_before_direct_fallback():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (90.0, 0.0),
                "length_m": 90.0,
                "is_covered": 1,
                "geometry": LineString([(0.0, 0.0), (90.0, 0.0)]),
            },
            {
                "u": (0.0, 0.0),
                "v": (400.0, 0.0),
                "length_m": 400.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (400.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (90.0, 0.0), (400.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        columns=["STATION_NA", "EXIT_CODE", "OBJECTID", "geometry"],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)
    record = score_postal_row(
        pd.Series({"postal_code": "123456", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        bus_index=ConnectorBusIndex(),  # type: ignore[arg-type]
        include_geometry=True,
        base_provenance={},
    )

    assert record["state"] == "SCORED"
    assert record["best_node"]["type"] == "bus_stop"
    assert record["best_node"]["routed_m"] == 100.0
    assert record["paths"]["routing_type"] == "sheltered_with_bus_stop_access_connector"
    assert record["paths"]["covered_ratio"] == 0.9
    assert record["provenance"]["bus_stop_access_connector"]["candidate_count"] == 1
    assert "direct_bus_fallback" not in record["provenance"]
    assert record["_geometry"]["sheltered_path_edges"][-1]["source_layer"] == (
        "bus_stop_access_connector"
    )


def test_score_postal_row_uses_partial_fallback_for_combined_connector_dominant_route():
    edges = pd.DataFrame(
        [
            {
                "u": (30.0, 0.0),
                "v": (85.0, 0.0),
                "length_m": 55.0,
                "is_covered": 1,
                "geometry": LineString([(30.0, 0.0), (85.0, 0.0)]),
            },
            {
                "u": (30.0, 0.0),
                "v": (400.0, 0.0),
                "length_m": 370.0,
                "is_covered": 0,
                "geometry": LineString([(30.0, 0.0), (400.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(30.0, 0.0), (85.0, 0.0), (400.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        columns=["STATION_NA", "EXIT_CODE", "OBJECTID", "geometry"],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)

    record = score_postal_row(
        pd.Series({"postal_code": "123461", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        bus_index=CombinedConnectorBusIndex(),  # type: ignore[arg-type]
        include_geometry=True,
        base_provenance={},
    )

    assert record["state"] == "SCORED_PARTIAL"
    assert record["paths"]["routing_type"] == "direct_bus_fallback_unrouted"
    assert record["best_node"]["routed_m"] is None
    assert record["provenance"]["direct_bus_fallback"]["reason_counts"] == {
        "dominant_unrouted_bus_endpoint_and_access_connectors": 1
    }
    assert record["provenance"]["untrusted_bus_routes"]["reason_counts"] == {
        "dominant_unrouted_bus_endpoint_and_access_connectors": 1
    }
    assert "bus_stop_access_connector" not in record["provenance"]


def test_score_postal_row_uses_partial_fallback_when_connector_route_still_detours():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (90.0, 0.0),
                "length_m": 150.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (90.0, 0.0)]),
            },
            {
                "u": (0.0, 0.0),
                "v": (400.0, 0.0),
                "length_m": 400.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (400.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (90.0, 0.0), (400.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        columns=["STATION_NA", "EXIT_CODE", "OBJECTID", "geometry"],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)

    record = score_postal_row(
        pd.Series({"postal_code": "123460", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        bus_index=StillDetouringConnectorBusIndex(),  # type: ignore[arg-type]
        include_geometry=True,
        base_provenance={},
    )

    assert record["state"] == "SCORED_PARTIAL"
    assert record["paths"]["routing_type"] == "direct_bus_fallback_unrouted"
    assert record["best_node"]["routed_m"] is None
    assert record["best_node"]["straight_line_m"] == 70.0
    assert record["provenance"]["direct_bus_fallback"]["reason_counts"] == {
        "implausible_graph_route_to_datamall_bus_stop_within_direct_radius": 1
    }
    assert "bus_stop_access_connector" not in record["provenance"]


def test_score_postal_row_uses_partial_fallback_for_untrusted_bus_access_connector():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (50.0, 0.0),
                "length_m": 130.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (50.0, 0.0)]),
            },
            {
                "u": (0.0, 0.0),
                "v": (400.0, 0.0),
                "length_m": 400.0,
                "is_covered": 0,
                "geometry": LineString([(0.0, 0.0), (400.0, 0.0)]),
            },
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (50.0, 0.0), (400.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        columns=["STATION_NA", "EXIT_CODE", "OBJECTID", "geometry"],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)

    record = score_postal_row(
        pd.Series({"postal_code": "123459", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        bus_index=LargeConnectorBusIndex(),  # type: ignore[arg-type]
        include_geometry=True,
        base_provenance={},
    )

    assert record["state"] == "SCORED_PARTIAL"
    assert record["paths"]["routing_type"] == "direct_bus_fallback_unrouted"
    assert record["best_node"]["routed_m"] is None
    assert record["provenance"]["direct_bus_fallback"]["candidate_count"] == 1
    assert record["provenance"]["untrusted_bus_routes"]["reason_counts"] == {
        "large_unrouted_bus_stop_access_connector": 1
    }
    assert "bus_stop_access_connector" not in record["provenance"]


def test_score_postal_row_skips_low_trust_bus_road_centerline_route():
    edges = pd.DataFrame(
        [
            {
                "u": (0.0, 0.0),
                "v": (180.0, 0.0),
                "length_m": 180.0,
                "is_covered": 0,
                "highway": "primary",
                "source_layer": "",
                "confidence": "",
                "geometry": LineString([(0.0, 0.0), (180.0, 0.0)]),
            }
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(0.0, 0.0), (180.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        columns=["STATION_NA", "EXIT_CODE", "OBJECTID", "geometry"],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)

    record = score_postal_row(
        pd.Series({"postal_code": "123458", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        bus_index=LowTrustRoadBusIndex(),  # type: ignore[arg-type]
        include_geometry=True,
        base_provenance={},
    )

    assert record["state"] == "NO_TRANSIT_IN_RANGE"
    assert record["provenance"]["reason"] == (
        "all_numeric_transit_candidates_rejected_by_bus_route_trust_gate"
    )
    assert record["provenance"]["untrusted_bus_routes"]["reason_counts"] == {
        "low_trust_bus_stop_road_centerline_route": 1
    }
    assert "direct_bus_fallback" not in record["provenance"]


def test_score_postal_row_includes_origin_and_destination_snap_connectors():
    edges = pd.DataFrame(
        [
            {
                "u": (10.0, 0.0),
                "v": (90.0, 0.0),
                "length_m": 80.0,
                "is_covered": 1,
                "geometry": LineString([(10.0, 0.0), (90.0, 0.0)]),
            }
        ]
    )
    routing_graph = RoutingGraph(edges)
    nodes = [(10.0, 0.0), (90.0, 0.0)]
    node_xy = np.asarray(nodes, dtype=float)
    mrt_exits = gpd.GeoDataFrame(
        columns=["STATION_NA", "EXIT_CODE", "OBJECTID", "geometry"],
        geometry="geometry",
        crs="EPSG:3414",
    )
    empty_signals = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    crossing_counter = CrossingCounter(empty_signals, None, eps_m=20.0, min_samples=2)

    record = score_postal_row(
        pd.Series({"postal_code": "123457", "geometry": Point(0.0, 0.0)}),
        mrt_exits,
        edges.to_dict("list"),
        routing_graph,
        nodes,
        node_xy,
        PARAMS,
        WEIGHTS,
        crossing_counter,
        bus_index=EndpointSnapBusIndex(),  # type: ignore[arg-type]
        include_geometry=True,
        base_provenance={},
    )

    assert record["state"] == "SCORED"
    assert record["best_node"]["routed_m"] == 100.0
    assert record["paths"]["shortest_m"] == 100.0
    assert record["paths"]["covered_m"] == 80.0
    assert record["paths"]["covered_ratio"] == 0.8
    assert record["paths"]["origin_snap_connector_m"] == 10.0
    assert record["paths"]["destination_snap_connector_m"] == 10.0
    assert record["paths"]["endpoint_snap_connector_m"] == 20.0
    assert record["_geometry"]["shortest_path_edges"][0]["source_layer"] == (
        "origin_graph_snap_connector"
    )
    assert record["_geometry"]["shortest_path_edges"][-1]["source_layer"] == (
        "destination_graph_snap_connector"
    )


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


def test_record_assembly_prefers_node_set_eligible_bus_over_farther_high_score_bus():
    near = {
        "total": 93.0,
        "node_set_eligible": True,
        "subscores": {
            "access": 100.0,
            "bus": 100.0,
            "rain": 82.4,
            "heat": 82.4,
            "crossing": 100.0,
        },
        "best_node": {
            "type": "bus_stop",
            "name": "Aft Ang Mo Kio Int",
            "routed_m": 92.9,
        },
        "paths": {"shortest_m": 92.9, "sheltered_m": 92.9, "routing_type": "sheltered"},
        "exposure_gaps": [],
    }
    far = {
        "total": 95.4,
        "node_set_eligible": False,
        "subscores": {
            "access": 88.6,
            "bus": 100.0,
            "rain": 98.4,
            "heat": 98.4,
            "crossing": 100.0,
        },
        "best_node": {
            "type": "bus_stop",
            "name": "Bef Al-Muttaqin Mque",
            "routed_m": 476.3,
        },
        "paths": {"shortest_m": 476.3, "sheltered_m": 476.3, "routing_type": "sheltered"},
        "exposure_gaps": [],
    }
    provenance = {}

    record = assemble_score_record("560710", [near, far], None, provenance)

    assert record["best_node"]["name"] == "Aft Ang Mo Kio Int"
    assert record["total"] == 93.0
    assert record["route_options"]["bus"]["best_node"]["name"] == "Aft Ang Mo Kio Int"
    assert provenance["candidate_selection"] == {
        "reason": "excluded_graph_routed_bus_candidates_beyond_routed_cap_from_default_choice",
        "node_set_eligible_count": 1,
        "skipped_ineligible_count": 1,
        "selected_total_rank": 2,
    }


def test_candidate_debug_rows_serializes_ranked_candidate_details():
    candidate = CandidateNode(
        node_type="bus_stop",
        name="Test Stop",
        station_name="Test Stop",
        exit_code="54321",
        graph_node=(100.0, 0.0),
        straight_line_m=80.0,
        snap_distance_m=5.0,
        service_headways_min={("10", 1): 6.0},
        expected_wait_min=3.0,
        point_xy=(80.0, 0.0),
    )
    rows = candidate_debug_rows(
        [
            {
                "candidate": candidate,
                "total": 88.88,
                "subscores": {
                    "access": 100.0,
                    "bus": 92.0,
                    "rain": 70.0,
                    "heat": 70.0,
                    "crossing": 100.0,
                },
                "best_node": {
                    "type": "bus_stop",
                    "name": "Test Stop",
                    "station": "Test Stop",
                    "exit": "54321",
                    "straight_line_m": 80.0,
                    "snap_distance_m": 5.0,
                },
                "paths": {
                    "shortest_m": 95.0,
                    "sheltered_m": 100.0,
                    "covered_ratio": 0.7,
                    "routing_type": "sheltered",
                },
            }
        ]
    )

    assert rows == [
        {
            "rank": 1,
            "type": "bus_stop",
            "name": "Test Stop",
            "station": "Test Stop",
            "exit": "54321",
            "total": 88.9,
            "subscores": {
                "access": 100.0,
                "bus": 92.0,
                "rain": 70.0,
                "heat": 70.0,
                "crossing": 100.0,
            },
            "shortest_m": 95.0,
            "sheltered_m": 100.0,
            "covered_ratio": 0.7,
            "routing_type": "sheltered",
            "node_set_eligible": True,
            "straight_line_m": 80.0,
            "snap_distance_m": 5.0,
            "expected_wait_min": 3.0,
        }
    ]


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
    assert set(provenance["scoring_fingerprints"]) == {
        "pipeline\\config\\params.yaml",
        "pipeline\\config\\weights.yaml",
        "pipeline\\routing.py",
        "pipeline\\scoring.py",
        "pipeline\\scoring_integration.py",
    }
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
