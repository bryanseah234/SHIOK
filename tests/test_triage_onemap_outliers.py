from pathlib import Path

from scripts.triage_onemap_outliers import (
    build_triage_queues,
    classify_row,
    compact_row,
    source_flags,
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
    assert payload["queue_summaries"]["possible_overpermissive_project_path"]["count"] == 1
    assert payload["queue_summaries"]["mrt_lrt_outlier"]["count"] == 1
    assert payload["queue_summaries"]["hdb_bridge_connector_review"]["count"] == 1
    assert payload["queues"]["missing_bus_connector"][0]["postal"] == "532183"
    assert payload["queues"]["mrt_lrt_outlier"][0]["postal"] == "489929"


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
