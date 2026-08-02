from pathlib import Path

from scripts.triage_onemap_outliers import (
    build_triage_queues,
    classify_row,
    compact_row,
    missing_bus_connector_priority_geojson,
    routed_vs_validation_direct_sanity,
    source_flags,
    triage_geojson,
    validation_lookup,
)


def profile(**metrics):
    return {"shortest": metrics, "sheltered": metrics}


def test_classify_row_flags_project_longer_direct_bus_as_missing_connector():
    row = {
        "postal": "532183",
        "old_direction": "project_longer_than_onemap",
        "new_best_type": "bus_stop",
        "new_best_routing_type": "direct_bus_fallback_unrouted",
        "direct_bus_fallback_reason": (
            "no_graph_routed_transit_candidate_but_datamall_bus_stop_within_direct_radius"
        ),
        "new_best_route_profile": profile(direct_bus_fallback_m=67.8),
    }

    assert classify_row(row) == [
        "direct_bus_fallback_review",
        "missing_bus_connector",
    ]


def test_classify_row_keeps_connector_fixed_bus_out_of_missing_connector_queue():
    row = {
        "postal": "760103",
        "old_direction": "project_longer_than_onemap",
        "new_best_type": "bus_stop",
        "new_best_routing_type": "sheltered_with_bus_stop_access_connector",
        "direct_bus_fallback_reason": (
            "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"
        ),
        "new_best_route_profile": profile(
            direct_bus_fallback_m=0.0,
            bus_stop_access_connector_m=45.1,
            source_layer_m={"bus_stop_access_connector": 45.1},
        ),
    }

    assert classify_row(row) == ["direct_bus_fallback_review"]


def test_classify_row_flags_shorter_hdb_path_for_overpermissive_review():
    row = {
        "postal": "123456",
        "old_direction": "project_shorter_than_onemap",
        "new_best_type": "bus_stop",
        "new_best_route_profile": profile(inferred_hdb_m=40.0, bridge_underpass_m=0.0),
    }

    assert classify_row(row) == [
        "possible_overpermissive_project_path",
        "hdb_bridge_connector_review",
    ]


def test_classify_row_flags_mrt_and_unscored_queues():
    row = {
        "postal": "489929",
        "old_validation_best_node": "TANAH MERAH MRT STATION Exit A",
        "old_direction": "project_shorter_than_onemap",
        "new_state": "NO_TRANSIT_IN_RANGE",
        "new_best_type": None,
    }

    assert classify_row(row) == [
        "possible_overpermissive_project_path",
        "mrt_lrt_outlier",
        "still_unscored_or_no_best",
    ]


def test_source_flags_keeps_compact_top_source_lengths():
    row = {
        "new_best_route_profile": profile(
            inferred_hdb_m=10.0,
            direct_bus_fallback_m=0.0,
            bridge_underpass_m=5.0,
            official_lta_shelter_m=7.0,
            osm_shelter_m=3.0,
            source_layer_m={
                "unknown": 100.0,
                "inferred_hdb_precinct": 10.0,
                "covered_linkway": 7.0,
                "osm_explicit_shelter": 3.0,
                "overhead_bridge_underpass": 5.0,
                "small": 1.0,
            },
        ),
        "new_bus_route_profile": profile(
            direct_bus_fallback_m=20.0,
            source_layer_m={"direct_bus_fallback": 20.0},
        ),
    }

    flags = source_flags(row)

    assert flags["best_inferred_hdb_m"] == 10.0
    assert flags["best_bridge_underpass_m"] == 5.0
    assert flags["best_bus_stop_access_connector_m"] == 0.0
    assert list(flags["best_top_source_layer_m"]) == [
        "unknown",
        "inferred_hdb_precinct",
        "covered_linkway",
        "overhead_bridge_underpass",
        "osm_explicit_shelter",
    ]
    assert flags["bus_direct_bus_fallback_m"] == 20.0


def test_build_triage_queues_from_profile_artifacts(tmp_path: Path):
    longer = tmp_path / "longer.json"
    shorter = tmp_path / "shorter.json"
    longer.write_text(
        """
        {
          "rows": [
            {
              "postal": "532183",
              "old_direction": "project_longer_than_onemap",
              "new_best_type": "bus_stop",
              "new_best_name": "Blk 181",
              "new_best_routing_type": "direct_bus_fallback_unrouted",
              "direct_bus_fallback_reason": "implausible_graph_route_to_datamall_bus_stop_within_direct_radius",
              "new_best_route_profile": {
                "shortest": {
                  "direct_bus_fallback_m": 67.8,
                  "source_layer_m": {"direct_bus_fallback": 67.8}
                }
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    shorter.write_text(
        """
        {
          "rows": [
            {
              "postal": "489929",
              "old_direction": "project_shorter_than_onemap",
              "old_validation_best_node": "TANAH MERAH MRT STATION Exit A",
              "new_best_type": "mrt_lrt_exit",
              "new_best_name": "TANAH MERAH MRT STATION Exit A",
              "new_best_route_profile": {
                "shortest": {
                  "inferred_hdb_m": 12.0,
                  "source_layer_m": {"inferred_hdb_precinct": 12.0}
                }
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    payload = build_triage_queues(
        longer_profile_path=longer,
        shorter_profile_path=shorter,
        generated_at="2026-08-02T00:00:00+00:00",
    )

    assert payload["inputs"]["input_rows"] == 2
    assert payload["queue_summaries"]["missing_bus_connector"]["count"] == 1
    assert payload["queue_summaries"]["missing_bus_connector"][
        "validation_distance_sanity_counts"
    ] == {"unknown": 1}
    assert payload["queue_summaries"]["missing_bus_connector"][
        "current_route_vs_validation_direct_sanity_counts"
    ] == {"unknown": 1}
    assert payload["queue_summaries"]["possible_overpermissive_project_path"]["count"] == 1
    assert payload["queue_summaries"]["mrt_lrt_outlier"]["count"] == 1
    assert payload["queue_summaries"]["hdb_bridge_connector_review"]["count"] == 1
    assert payload["queues"]["missing_bus_connector"][0]["postal"] == "532183"
    assert payload["queues"]["mrt_lrt_outlier"][0]["postal"] == "489929"


def test_build_triage_queues_enriches_from_validation_report(tmp_path: Path):
    longer = tmp_path / "longer.json"
    shorter = tmp_path / "shorter.json"
    validation_report = tmp_path / "validation.json"
    longer.write_text(
        """
        {
          "rows": [
            {
              "postal": "532183",
              "old_direction": "project_longer_than_onemap",
              "new_best_type": "bus_stop",
              "new_best_routing_type": "direct_bus_fallback_unrouted",
              "new_best_shortest_m": 67.8,
              "direct_bus_fallback_reason": "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    shorter.write_text('{"rows":[]}', encoding="utf-8")
    validation_report.write_text(
        """
        {
          "top_outliers_by_direction": {
            "project_longer_than_onemap": [
              {
                "postal": "532183",
                "direction": "project_longer_than_onemap",
                "area": "HOUGANG",
                "best_node_type": "bus_stop",
                "endpoint_source": "postal_universe_to_transit_poi",
                "direct_distance_m": 67.7,
                "onemap_vs_direct_delta_m": -61.7,
                "distance_sanity": "onemap_materially_shorter_than_direct",
                "abs_pct_delta": 100.0,
                "start": {"lat": 1.346263, "lon": 103.887204},
                "end": {"lat": 1.346168, "lon": 103.887806}
              }
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    payload = build_triage_queues(
        longer_profile_path=longer,
        shorter_profile_path=shorter,
        validation_report_path=validation_report,
        generated_at="2026-08-02T00:00:00+00:00",
    )

    row = payload["queues"]["missing_bus_connector"][0]
    assert row["validation_area"] == "HOUGANG"
    assert row["validation_best_node_type"] == "bus_stop"
    assert row["validation_direct_distance_m"] == 67.7
    assert row["validation_onemap_vs_direct_delta_m"] == -61.7
    assert row["validation_distance_sanity"] == "onemap_materially_shorter_than_direct"
    assert row["current_route_vs_validation_direct_sanity"] == "plausible"
    assert row["start"] == {"lat": 1.346263, "lon": 103.887204}
    assert row["end"] == {"lat": 1.346168, "lon": 103.887806}
    assert payload["queue_summaries"]["missing_bus_connector"][
        "validation_distance_sanity_counts"
    ] == {"onemap_materially_shorter_than_direct": 1}
    assert payload["queue_summaries"]["missing_bus_connector"][
        "current_route_vs_validation_direct_sanity_counts"
    ] == {"plausible": 1}


def test_validation_lookup_keeps_highest_delta_for_postal_direction(tmp_path: Path):
    report = tmp_path / "validation.json"
    report.write_text(
        """
        {
          "top_outliers_by_direction": {
            "project_longer_than_onemap": [
              {
                "postal": "532183",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 10.0,
                "area": "LOW"
              },
              {
                "postal": "532183",
                "direction": "project_longer_than_onemap",
                "abs_pct_delta": 20.0,
                "area": "HIGH"
              }
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    lookup = validation_lookup(report)

    assert lookup[("532183", "project_longer_than_onemap")]["area"] == "HIGH"


def test_triage_geojson_exports_start_end_lines():
    geojson = triage_geojson(
        {
            "missing_bus_connector": [
                {
                    "postal": "532183",
                    "start": {"lat": 1.346263, "lon": 103.887204},
                    "end": {"lat": 1.346168, "lon": 103.887806},
                    "new_best_name": "Blk 181",
                }
            ],
            "empty": [{"postal": "000000"}],
        }
    )

    assert geojson == {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[103.887204, 1.346263], [103.887806, 1.346168]],
                },
                "properties": {
                    "postal": "532183",
                    "new_best_name": "Blk 181",
                    "queue": "missing_bus_connector",
                },
            }
        ],
    }


def test_missing_bus_connector_priority_geojson_keeps_plausible_ranked_rows():
    geojson = missing_bus_connector_priority_geojson(
        {
            "missing_bus_connector": [
                {
                    "postal": "111111",
                    "old_abs_pct_delta": 10.0,
                    "validation_distance_sanity": "plausible",
                    "current_route_vs_validation_direct_sanity": "plausible",
                    "start": {"lat": 1.0, "lon": 103.0},
                    "end": {"lat": 1.1, "lon": 103.1},
                },
                {
                    "postal": "222222",
                    "old_abs_pct_delta": 50.0,
                    "validation_distance_sanity": "plausible",
                    "current_route_vs_validation_direct_sanity": "plausible",
                    "start": {"lat": 1.2, "lon": 103.2},
                    "end": {"lat": 1.3, "lon": 103.3},
                },
                {
                    "postal": "333333",
                    "old_abs_pct_delta": 100.0,
                    "validation_distance_sanity": "onemap_materially_shorter_than_direct",
                    "current_route_vs_validation_direct_sanity": "plausible",
                    "start": {"lat": 1.4, "lon": 103.4},
                    "end": {"lat": 1.5, "lon": 103.5},
                },
                {
                    "postal": "444444",
                    "old_abs_pct_delta": 200.0,
                    "validation_distance_sanity": "plausible",
                    "current_route_vs_validation_direct_sanity": (
                        "current_route_materially_shorter_than_validation_direct"
                    ),
                    "start": {"lat": 1.4, "lon": 103.4},
                    "end": {"lat": 1.5, "lon": 103.5},
                },
            ]
        }
    )

    assert [feature["properties"]["postal"] for feature in geojson["features"]] == [
        "222222",
        "111111",
    ]
    assert [feature["properties"]["priority_rank"] for feature in geojson["features"]] == [1, 2]


def test_routed_vs_validation_direct_sanity():
    validation = {"direct_distance_m": 100.0}

    assert routed_vs_validation_direct_sanity({"new_best_shortest_m": 60.0}, validation) == (
        "current_route_materially_shorter_than_validation_direct"
    )
    assert routed_vs_validation_direct_sanity({"new_best_shortest_m": 90.0}, validation) == (
        "current_route_slightly_shorter_than_validation_direct"
    )
    assert routed_vs_validation_direct_sanity({"new_best_shortest_m": 105.0}, validation) == (
        "plausible"
    )
    assert routed_vs_validation_direct_sanity({"new_best_shortest_m": None}, validation) == (
        "unknown"
    )


def test_compact_row_preserves_user_facing_triage_fields():
    row = {
        "postal": "123",
        "old_validation_best_node": "Old Stop",
        "old_abs_pct_delta": 99.0,
        "new_state": "SCORED",
        "new_best_type": "bus_stop",
        "new_best_name": "New Stop",
        "new_best_shortest_m": 80.0,
        "direct_bus_fallback_reason": None,
    }

    compact = compact_row(row, source_artifact="qa/source.json")

    assert compact["postal"] == "000123"
    assert compact["source_artifact"] == "qa/source.json"
    assert compact["old_validation_best_node"] == "Old Stop"
    assert compact["new_best_name"] == "New Stop"
    assert "source_flags" in compact
