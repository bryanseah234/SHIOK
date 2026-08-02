import json

from pipeline.export import encode_polyline
from scripts.mayflower_qa_summary import build_summary, route_gap_features, write_markdown


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
    write_json(bundle / "geom" / "postal-index.json", {"560231": "8_425_259"})
    write_json(
        bundle / "geom" / "h3" / "8_425_259.json",
        [
            {
                "postal": "560231",
                "route_segments": {
                    "sheltered": [
                        {
                            "len_m": 94.6,
                            "is_covered": True,
                            "source_class": "inferred_hdb_void_deck",
                        },
                        {"len_m": 15.3, "is_covered": False, "source_class": "exposed"},
                    ]
                },
                "exposure_gaps": [{"len_m": 15.3, "label": "exposed gap"}],
                "route_options": {
                    "mrt_lrt": {
                        "route_segments": {
                            "sheltered": [
                                {
                                    "len_m": 124.6,
                                    "is_covered": True,
                                    "source_class": "inferred_hdb_void_deck",
                                },
                                {"len_m": 307.2, "is_covered": False, "source_class": "exposed"},
                            ]
                        },
                        "exposure_gaps": [
                            {"len_m": 291.9, "label": "exposed gap near 1.36970, 103.83691"}
                        ],
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
    assert summary["route_geometry"]["560231"]["mrt_lrt"]["route_segments"]["sheltered"][
        "source_lengths_m"
    ] == {"exposed": 307.2, "inferred_hdb_void_deck": 124.6}
    assert summary["mrt_gap_signals"]["560231"]["largest_mrt_exposed_gap_m"] == 291.9
    assert summary["mrt_gap_signals"]["560231"]["mrt_specific_false_negative_signal"] is True
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
        "route_geometry": {
            "560231": {
                "best_transit": {
                    "route_segments": {
                        "sheltered": {
                            "covered_m": 94.6,
                            "exposed_m": 15.3,
                            "source_lengths_m": {
                                "exposed": 15.3,
                                "inferred_hdb_void_deck": 94.6,
                            },
                        }
                    },
                    "exposure_gaps": {"largest_gap_m": 15.3},
                },
                "mrt_lrt": {
                    "route_segments": {
                        "sheltered": {
                            "covered_m": 124.6,
                            "exposed_m": 307.2,
                            "source_lengths_m": {
                                "exposed": 307.2,
                                "inferred_hdb_void_deck": 124.6,
                            },
                        }
                    },
                    "exposure_gaps": {"largest_gap_m": 291.9},
                },
            }
        },
        "mrt_gap_signals": {
            "560231": {
                "mrt_lrt_covered_ratio": 0.312,
                "best_transit_covered_ratio": None,
                "largest_mrt_exposed_gap_m": 291.9,
                "mrt_specific_false_negative_signal": True,
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
    assert "## Active Route Segment Sources" in content
    assert "largest gap `292` m" in content
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


def test_route_gap_features_exports_exposed_mrt_segments_only(tmp_path):
    bundle = tmp_path / "bundle"
    write_json(bundle / "scores" / "index.json", {"ANG_MO_KIO_PART_001": ["560231"]})
    write_json(
        bundle / "scores" / "ANG_MO_KIO_PART_001.json",
        [
            {
                "postal": "560231",
                "state": "SCORED",
                "total": 80.0,
                "best_node": {"type": "bus_stop", "name": "Bus", "routed_m": 200.0},
                "paths": {"covered_ratio": 0.9},
                "route_options": {
                    "mrt_lrt": {
                        "state": "SCORED",
                        "paths": {"covered_ratio": 0.3},
                    }
                },
            }
        ],
    )
    write_json(bundle / "geom" / "postal-index.json", {"560231": "8_425_259"})
    write_json(
        bundle / "geom" / "h3" / "8_425_259.json",
        [
            {
                "postal": "560231",
                "route_options": {
                    "mrt_lrt": {
                        "route_segments": {
                            "sheltered": [
                                {
                                    "geom": encode_polyline(
                                        [(1.36970, 103.83691), (1.37000, 103.83750)]
                                    ),
                                    "is_covered": False,
                                    "len_m": 291.9,
                                    "source_class": "exposed",
                                },
                                {
                                    "geom": encode_polyline(
                                        [(1.37000, 103.83750), (1.37010, 103.83760)]
                                    ),
                                    "is_covered": True,
                                    "len_m": 20.0,
                                    "source_class": "osm_covered",
                                },
                                {
                                    "geom": encode_polyline(
                                        [(1.37010, 103.83760), (1.37011, 103.83761)]
                                    ),
                                    "is_covered": False,
                                    "len_m": 4.0,
                                    "source_class": "exposed",
                                },
                            ]
                        },
                        "exposure_gaps": [{"len_m": 291.9, "label": "large gap"}],
                    }
                },
            }
        ],
    )

    output = route_gap_features(bundle, ["560231"], min_exposed_m=50.0)

    assert output["type"] == "FeatureCollection"
    assert len(output["features"]) == 1
    [feature] = output["features"]
    assert feature["properties"]["postal"] == "560231"
    assert feature["properties"]["len_m"] == 291.9
    assert feature["properties"]["mrt_specific_false_negative_signal"] is True
    assert feature["geometry"]["coordinates"][0] == [103.83691, 1.3697]
