from scripts.replay_onemap_outliers import (
    replay_row,
    route_source_profile,
    select_outliers,
    summarize_rows,
)


def test_select_outliers_filters_direction_type_delta_and_dedupes():
    report = {
        "top_outliers_preview": [
            {
                "postal": "123456",
                "best_node_type": "bus_stop",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 100.0,
            },
            {
                "postal": "123456",
                "best_node_type": "bus_stop",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 90.0,
            },
            {
                "postal": "234567",
                "best_node_type": "mrt_lrt_exit",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 100.0,
            },
            {
                "postal": "345678",
                "best_node_type": "bus_stop",
                "direction": "project_shorter_than_onemap",
                "abs_pct_delta": 100.0,
            },
            {
                "postal": "456789",
                "best_node_type": "bus_stop",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 10.0,
            },
        ]
    }

    selected = select_outliers(
        report,
        limit=10,
        node_type="bus_stop",
        direction="project_longer_than_onemap",
        min_abs_pct_delta=25.0,
    )

    assert [row["postal"] for row in selected] == ["123456"]


def test_select_outliers_prefers_direction_specific_queue():
    report = {
        "top_outliers_preview": [],
        "top_outliers_by_direction": {
            "project_shorter_than_onemap": [
                {
                    "postal": "123456",
                    "best_node_type": "mrt_lrt_exit",
                    "direction": "project_shorter_than_onemap",
                    "abs_pct_delta": 99.0,
                }
            ]
        },
    }

    selected = select_outliers(
        report,
        limit=10,
        node_type="any",
        direction="project_shorter_than_onemap",
        min_abs_pct_delta=25.0,
    )

    assert [row["postal"] for row in selected] == ["123456"]


def test_select_outliers_uses_full_results_when_available():
    report = {
        "results": [
            {
                "postal": "111111",
                "best_node_type": "bus_stop",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 80.0,
            },
            {
                "postal": "222222",
                "best_node_type": "bus_stop",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 120.0,
            },
        ],
        "top_outliers_preview": [
            {
                "postal": "333333",
                "best_node_type": "bus_stop",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 200.0,
            }
        ],
    }

    selected = select_outliers(
        report,
        limit=10,
        node_type="bus_stop",
        direction="project_longer_than_onemap",
        min_abs_pct_delta=25.0,
    )

    assert [row["postal"] for row in selected] == ["222222", "111111"]


def test_replay_row_extracts_bus_and_fallback_fields():
    row = replay_row(
        {
            "postal": "123456",
            "best_node_name": "Old Stop",
            "project_shortest_m": 400.0,
            "onemap_walk_m": 80.0,
            "abs_pct_delta": 400.0,
            "direction": "project_longer_than_onemap",
        },
        {
            "postal": "123456",
            "state": "SCORED_PARTIAL",
            "total": 48.8,
            "best_node": {"type": "bus_stop", "name": "New Stop"},
            "paths": {"shortest_m": 70.0, "routing_type": "direct_bus_fallback_unrouted"},
            "route_options": {
                "bus": {
                    "state": "SCORED_PARTIAL",
                    "paths": {
                        "shortest_m": 70.0,
                        "routing_type": "direct_bus_fallback_unrouted",
                    },
                }
            },
            "provenance": {
                "direct_bus_fallback": {
                    "reason": "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"
                },
                "untrusted_bus_routes": {
                    "reason_counts": {"large_unrouted_bus_stop_access_connector": 1}
                },
            },
        },
    )

    assert row["old_validation_best_node"] == "Old Stop"
    assert row["new_best_type"] == "bus_stop"
    assert row["new_bus_routing_type"] == "direct_bus_fallback_unrouted"
    assert (
        row["direct_bus_fallback_reason"]
        == "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"
    )
    assert row["untrusted_bus_route_reason_counts"] == {
        "large_unrouted_bus_stop_access_connector": 1
    }


def test_route_source_profile_summarizes_lengths_by_source():
    profile = route_source_profile(
        [
            {
                "length_m": 40.0,
                "is_covered": True,
                "source_layer": "inferred_hdb_void_deck",
                "synth_class": "INFERRED_HDB_VOID_DECK",
                "confidence": "inferred",
            },
            {
                "length_m": 60.0,
                "is_covered": False,
                "source_layer": "direct_bus_fallback",
                "synth_class": "unrouted_straight_line",
                "confidence": "partial_unrouted",
            },
            {
                "length_m": 10.0,
                "is_covered": True,
                "source_layer": "covered_linkway",
                "confidence": "source_polygon_match",
            },
        ]
    )

    assert profile == {
        "edge_count": 3,
        "total_m": 110.0,
        "covered_m": 50.0,
        "covered_ratio": 0.455,
        "exposed_m": 60.0,
        "inferred_hdb_m": 40.0,
        "direct_bus_fallback_m": 60.0,
        "bridge_underpass_m": 0.0,
        "official_lta_shelter_m": 10.0,
        "osm_shelter_m": 0.0,
        "source_layer_m": {
            "covered_linkway": 10.0,
            "direct_bus_fallback": 60.0,
            "inferred_hdb_void_deck": 40.0,
        },
        "synth_class_m": {
            "INFERRED_HDB_VOID_DECK": 40.0,
            "none": 10.0,
            "unrouted_straight_line": 60.0,
        },
        "confidence_m": {
            "inferred": 40.0,
            "partial_unrouted": 60.0,
            "source_polygon_match": 10.0,
        },
    }


def test_replay_row_can_include_route_source_profile():
    row = replay_row(
        {"postal": "123456"},
        {
            "postal": "123456",
            "paths": {"shortest_m": 40.0, "routing_type": "sheltered"},
            "_geometry": {
                "shortest_path_edges": [
                    {
                        "length_m": 40.0,
                        "is_covered": True,
                        "source_layer": "inferred_hdb_precinct",
                    }
                ],
                "sheltered_path_edges": [
                    {
                        "length_m": 40.0,
                        "is_covered": True,
                        "source_layer": "inferred_hdb_precinct",
                    }
                ],
            },
        },
        include_route_source_profile=True,
    )

    assert row["new_best_route_profile"]["shortest"]["inferred_hdb_m"] == 40.0
    assert row["new_bus_route_profile"] is None


def test_summarize_rows_counts_fallback_shapes():
    summary = summarize_rows(
        [
            {
                "new_best_type": "bus_stop",
                "new_best_routing_type": "direct_bus_fallback_unrouted",
                "new_bus_routing_type": "direct_bus_fallback_unrouted",
                "direct_bus_fallback_reason": "implausible",
                "untrusted_bus_route_reason_counts": {
                    "large_unrouted_bus_stop_access_connector": 1
                },
            },
            {
                "new_best_type": "mrt_lrt_exit",
                "new_best_routing_type": "sheltered",
                "new_bus_routing_type": None,
                "direct_bus_fallback_reason": None,
                "untrusted_bus_route_reason_counts": {"dominant_unrouted_bus_endpoint_snap": 2},
            },
        ]
    )

    assert summary["sample_size"] == 2
    assert summary["new_best_direct_bus_fallback_count"] == 1
    assert summary["new_bus_direct_bus_fallback_count"] == 1
    assert summary["new_best_type_counts"] == {"bus_stop": 1, "mrt_lrt_exit": 1}
    assert summary["fallback_reason_counts"] == {"implausible": 1, "none": 1}
    assert summary["untrusted_bus_route_reason_counts"] == {
        "dominant_unrouted_bus_endpoint_snap": 2,
        "large_unrouted_bus_stop_access_connector": 1,
    }


def test_summarize_rows_aggregates_route_source_profiles():
    summary = summarize_rows(
        [
            {
                "new_best_type": "bus_stop",
                "new_best_routing_type": "sheltered",
                "new_bus_routing_type": "direct_bus_fallback_unrouted",
                "direct_bus_fallback_reason": None,
                "new_best_route_profile": {
                    "shortest": {
                        "total_m": 100.0,
                        "covered_m": 40.0,
                        "exposed_m": 60.0,
                        "inferred_hdb_m": 40.0,
                        "direct_bus_fallback_m": 0.0,
                        "bridge_underpass_m": 0.0,
                        "official_lta_shelter_m": 0.0,
                        "osm_shelter_m": 0.0,
                        "source_layer_m": {"inferred_hdb_precinct": 40.0, "unknown": 60.0},
                        "synth_class_m": {"INFERRED_HDB_PRECINCT_CONNECTOR": 40.0},
                        "confidence_m": {"inferred": 40.0, "unknown": 60.0},
                    }
                },
                "new_bus_route_profile": {
                    "shortest": {
                        "total_m": 50.0,
                        "covered_m": 0.0,
                        "exposed_m": 50.0,
                        "inferred_hdb_m": 0.0,
                        "direct_bus_fallback_m": 50.0,
                        "bridge_underpass_m": 0.0,
                        "official_lta_shelter_m": 0.0,
                        "osm_shelter_m": 0.0,
                        "source_layer_m": {"direct_bus_fallback": 50.0},
                        "synth_class_m": {"unrouted_straight_line": 50.0},
                        "confidence_m": {"partial_unrouted": 50.0},
                    }
                },
            }
        ]
    )

    profiles = summary["route_source_profile_summary"]
    assert profiles["new_best_shortest"]["profiled_rows"] == 1
    assert profiles["new_best_shortest"]["flag_row_counts"] == {"inferred_hdb_m": 1}
    assert profiles["new_best_shortest"]["source_layer_m"] == {
        "inferred_hdb_precinct": 40.0,
        "unknown": 60.0,
    }
    assert profiles["new_bus_shortest"]["flag_row_counts"] == {"direct_bus_fallback_m": 1}
    assert profiles["new_bus_shortest"]["source_layer_m"] == {"direct_bus_fallback": 50.0}
