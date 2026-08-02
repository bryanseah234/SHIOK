from scripts.replay_onemap_outliers import replay_row, select_outliers, summarize_rows


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
                }
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


def test_summarize_rows_counts_fallback_shapes():
    summary = summarize_rows(
        [
            {
                "new_best_type": "bus_stop",
                "new_best_routing_type": "direct_bus_fallback_unrouted",
                "new_bus_routing_type": "direct_bus_fallback_unrouted",
                "direct_bus_fallback_reason": "implausible",
            },
            {
                "new_best_type": "mrt_lrt_exit",
                "new_best_routing_type": "sheltered",
                "new_bus_routing_type": None,
                "direct_bus_fallback_reason": None,
            },
        ]
    )

    assert summary["sample_size"] == 2
    assert summary["new_best_direct_bus_fallback_count"] == 1
    assert summary["new_bus_direct_bus_fallback_count"] == 1
    assert summary["new_best_type_counts"] == {"bus_stop": 1, "mrt_lrt_exit": 1}
    assert summary["fallback_reason_counts"] == {"implausible": 1, "none": 1}
