from pipeline.scoring_integration import CandidateNode
from scripts.diagnose_bus_connectors import (
    choose_target_bus_candidate,
    diagnostic_action_summary,
    diagnostic_class,
    first_property,
    normalize_stop_name,
    score_recovers_target_bus_stop,
    stop_names_match,
    within_onemap_threshold,
)


def bus_candidate(name: str, direct_m: float, point_xy: tuple[float, float]) -> CandidateNode:
    return CandidateNode(
        node_type="bus_stop",
        name=name,
        station_name=name,
        exit_code=name,
        graph_node=point_xy,
        straight_line_m=direct_m,
        snap_distance_m=3.0,
        service_headways_min={("10", 1): 8.0},
        expected_wait_min=4.0,
        point_xy=point_xy,
    )


def test_stop_name_normalization_and_match():
    assert normalize_stop_name("  The   Rail Mall ") == "the rail mall"
    assert stop_names_match("The Rail Mall", " the rail mall ")
    assert not stop_names_match("The Rail Mall", "Opp The Rail Mall")


def test_first_property_supports_validation_priority_schema():
    props = {"best_node_name": "Blk 535", "project_shortest_m": 347.9}

    assert first_property(props, "new_best_name", "best_node_name") == "Blk 535"
    assert first_property(props, "new_best_shortest_m", "project_shortest_m") == 347.9
    assert first_property(props, "missing") is None


def test_choose_target_bus_candidate_prefers_name_then_endpoint_distance():
    far_same_name = bus_candidate("The Rail Mall", 80.0, (80.0, 0.0))
    near_same_name = bus_candidate("The Rail Mall", 50.0, (10.0, 0.0))
    closest_wrong_name = bus_candidate("Other Stop", 10.0, (0.0, 0.0))

    candidate, method = choose_target_bus_candidate(
        [far_same_name, near_same_name, closest_wrong_name],
        current_name="The Rail Mall",
        validation_name="The Rail Mall",
        validation_end_xy=(8.0, 0.0),
    )

    assert method == "matched_target_name"
    assert candidate == near_same_name


def test_choose_target_bus_candidate_falls_back_to_endpoint_distance():
    candidate, method = choose_target_bus_candidate(
        [
            bus_candidate("Far Stop", 20.0, (100.0, 0.0)),
            bus_candidate("Near Stop", 30.0, (5.0, 0.0)),
        ],
        current_name="Missing",
        validation_name="Also Missing",
        validation_end_xy=(0.0, 0.0),
    )

    assert method == "nearest_candidate_to_validation_end"
    assert candidate is not None
    assert candidate.name == "Near Stop"


def test_diagnostic_class_prioritizes_changed_stop_and_alternate_snap():
    recovered = {
        "target_match_method": "matched_target_name",
        "same_validation_and_current_stop_name": True,
        "target_bus_stop_name": "Aft Chong Pang CC",
        "current_score_best_name": "aft  chong pang cc",
        "current_score_best_type": "bus_stop",
        "current_score_state": "SCORED",
        "current_score_routing_type": "sheltered_with_bus_stop_access_connector",
        "current_score_best_routed_m": 68.0,
        "current_graph_route_state": "implausible_detour",
        "best_alternate_snap": {"route_plus_snap_m": 49.0},
    }

    assert score_recovers_target_bus_stop(recovered)
    assert diagnostic_class(recovered) == "scorer_recovered_target_bus_stop"

    assert (
        diagnostic_class(
            {
                "target_match_method": "matched_target_name",
                "same_validation_and_current_stop_name": False,
                "current_graph_route_state": "disconnected",
                "best_alternate_snap": {"route_plus_snap_m": 40.0},
            }
        )
        == "changed_stop_between_validation_and_replay"
    )
    assert (
        diagnostic_class(
            {
                "target_match_method": "matched_target_name",
                "same_validation_and_current_stop_name": True,
                "current_graph_route_state": "disconnected",
                "best_alternate_snap": {"route_plus_snap_m": 40.0},
            }
        )
        == "alternate_bus_snap_candidate"
    )
    assert (
        diagnostic_class(
            {
                "target_match_method": "matched_target_name",
                "same_validation_and_current_stop_name": True,
                "current_graph_route_state": "disconnected",
                "best_alternate_snap": None,
            }
        )
        == "bus_stop_graph_disconnected"
    )


def test_diagnostic_action_summary_separates_rescore_and_model_fix_rows():
    rows = [
        {
            "postal": "760103",
            "diagnostic_class": "scorer_recovered_target_bus_stop",
            "old_onemap_walk_m": 42.0,
            "current_score_best_routed_m": 68.0,
        },
        {
            "postal": "559038",
            "diagnostic_class": "scorer_recovered_target_bus_stop",
            "old_onemap_walk_m": 50.0,
            "current_score_best_routed_m": 55.0,
        },
        {
            "postal": "530535",
            "diagnostic_class": "alternate_bus_snap_candidate",
            "old_onemap_walk_m": 43.0,
            "best_alternate_snap": {"route_plus_snap_m": 340.4},
        },
        {
            "postal": "427835",
            "diagnostic_class": "current_routable",
            "old_onemap_walk_m": 59.0,
            "current_score_best_routed_m": 98.8,
        },
    ]

    summary = diagnostic_action_summary(rows)

    assert within_onemap_threshold(55.0, 50.0)
    assert not within_onemap_threshold(68.0, 42.0)
    assert summary["needs_rescore_candidate_count"] == 2
    assert summary["needs_bus_snap_or_connector_model_fix_count"] == 1
    assert summary["needs_current_routable_route_review_count"] == 1
    assert summary["current_score_within_threshold_count"] == 1
    assert summary["alternate_snap_within_threshold_count"] == 0
    assert summary["top_needs_rescore_candidates"][0]["postal"] == "760103"
