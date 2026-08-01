import json

from scripts.mayflower_qa_summary import build_summary, write_markdown


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_summary_compacts_scores_and_connector_status(tmp_path):
    bundle = tmp_path / "bundle"
    write_json(bundle / "scores" / "index.json", {"ANG_MO_KIO_PART_001": ["560231"]})
    write_json(
        bundle / "scores" / "ANG_MO_KIO_PART_001.json",
        [
            {
                "postal": "560231",
                "state": "SCORED",
                "total": 72.1,
                "best_node": {"type": "bus_stop", "name": "Opp Mayflower", "routed_m": 128.1},
                "paths": {"sheltered_m": 128.1, "shortest_m": 128.1, "covered_ratio": 1.0},
                "route_options": {
                    "mrt_lrt": {
                        "state": "SCORED",
                        "total": 60.0,
                        "best_node": {
                            "type": "mrt_lrt_exit",
                            "name": "MAYFLOWER MRT STATION Exit 5",
                            "routed_m": 425.9,
                        },
                        "paths": {
                            "sheltered_m": 425.9,
                            "shortest_m": 425.9,
                            "covered_ratio": 0.31,
                        },
                    }
                },
            }
        ],
    )
    component_audit = tmp_path / "component.json"
    write_json(
        component_audit,
        {
            "candidates": [
                {
                    "postal": "560231",
                    "segment_index": 6,
                    "promotion_status": "blocked_insufficient_source_overlap_not_scoring",
                    "candidate_classification": "insufficient_source_overlap",
                    "length_m": 128.7,
                    "covered_overlap_ratio": 0.105,
                    "hdb_overlap_ratio": 0.105,
                }
            ]
        },
    )
    feedback_audit = tmp_path / "feedback.json"
    write_json(
        feedback_audit,
        {
            "segments": [
                {"postal": "560231", "classification": "hdb_void_deck_component_gap"},
                {
                    "postal": "560231",
                    "classification": "covered_evidence_nearby_check_connectivity_or_snap",
                },
            ]
        },
    )

    summary = build_summary(bundle, component_audit, feedback_audit, ["560231"])

    assert summary["scores"]["560231"]["best_transit"]["best_node"]["name"] == "Opp Mayflower"
    assert summary["scores"]["560231"]["mrt_lrt"]["paths"]["covered_ratio"] == 0.31
    assert summary["connector_candidates"]["promotion_status_counts"] == {
        "blocked_insufficient_source_overlap_not_scoring": 1
    }
    [candidate] = summary["connector_candidates"]["by_postal"]["560231"]
    assert candidate["audit_id"] == "feedback-560231-segment-6-insufficient-source-overlap"
    assert candidate["covered_overlap_ratio"] == 0.105
    assert summary["feedback_segments"]["by_postal"]["560231"]["hdb_void_deck_component_gap"] == 1
    assert summary["conclusion"]["score_override_used"] is False


def test_write_markdown_includes_reviewable_candidate_details(tmp_path):
    summary = {
        "bundle": "generated_test",
        "scores": {
            "560231": {
                "state": "SCORED",
                "total": 72.1,
                "best_transit": None,
                "mrt_lrt": {
                    "state": "SCORED",
                    "total": 72.1,
                    "best_node": {"name": "MAYFLOWER MRT STATION Exit 5"},
                    "paths": {"sheltered_m": 425.9, "covered_ratio": 0.312},
                },
                "bus": None,
            }
        },
        "feedback_segments": {
            "by_postal": {"560231": {"hdb_void_deck_component_gap": 1}},
        },
        "connector_candidates": {
            "candidate_count": 1,
            "promotion_status_counts": {
                "blocked_insufficient_source_overlap_not_scoring": 1,
            },
            "classification_counts": {"insufficient_source_overlap": 1},
            "by_postal": {
                "560231": [
                    {
                        "audit_id": "feedback-560231-segment-6-hdb-source-overlap-review",
                        "segment_index": 6,
                        "label": "void_deck",
                        "length_m": 128.7,
                        "promotion_status": "blocked_insufficient_source_overlap_not_scoring",
                        "candidate_classification": "insufficient_source_overlap",
                        "covered_overlap_ratio": 0.105,
                        "hdb_overlap_ratio": 0.105,
                        "osm_shelter_overlap_ratio": 0.0,
                        "official_shelter_overlap_ratio": 0.0,
                    }
                ]
            },
        },
        "conclusion": {
            "score_override_used": False,
            "approved_source_backed_corrections": 5,
            "ready_for_owner_review": 0,
            "blocked_without_more_source_evidence": 1,
        },
    }
    output = tmp_path / "summary.md"

    write_markdown(output, summary)

    content = output.read_text(encoding="utf-8")
    assert "## Candidate Details" in content
    assert "`feedback-560231-segment-6-hdb-source-overlap-review`" in content
    assert "status `blocked_insufficient_source_overlap_not_scoring`" in content
    assert "HDB overlap `0.105`" in content


def test_build_summary_subtracts_approved_review_ready_corrections(tmp_path):
    bundle = tmp_path / "bundle"
    write_json(bundle / "scores" / "index.json", {"ANG_MO_KIO_PART_001": ["560231"]})
    write_json(
        bundle / "scores" / "ANG_MO_KIO_PART_001.json",
        [
            {
                "postal": "560231",
                "state": "SCORED",
                "total": 72.1,
                "best_node": {"type": "mrt_lrt_exit", "name": "Mayflower", "routed_m": 425.9},
                "paths": {"sheltered_m": 425.9, "shortest_m": 425.9, "covered_ratio": 0.31},
            }
        ],
    )
    component_audit = tmp_path / "component.json"
    audit_id = "feedback-560231-segment-1-hdb-source-overlap-review"
    write_json(
        component_audit,
        {
            "candidates": [
                {
                    "audit_id": audit_id,
                    "postal": "560231",
                    "segment_index": 1,
                    "promotion_status": "review_ready_not_scoring",
                    "candidate_classification": "hdb_source_overlap_review",
                }
            ]
        },
    )
    feedback_audit = tmp_path / "feedback.json"
    write_json(feedback_audit, {"segments": []})
    approved = tmp_path / "approved.geojson"
    write_json(
        approved,
        {
            "type": "FeatureCollection",
            "features": [{"properties": {"audit_id": audit_id, "status": "approved"}}],
        },
    )

    summary = build_summary(
        bundle,
        component_audit,
        feedback_audit,
        ["560231"],
        approved_corrections_path=approved,
    )

    assert summary["conclusion"]["approved_source_backed_corrections"] == 1
    assert summary["conclusion"]["ready_for_owner_review"] == 0
