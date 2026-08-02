from scripts.compare_targeted_scores import (
    compare_record,
    compare_records,
    load_candidate_records,
    normalize_postal,
)


def record(
    postal,
    total,
    covered,
    state="SCORED",
    best_type="mrt_exit",
    best_name="Node",
    routed_m=100.0,
):
    return {
        "postal": postal,
        "state": state,
        "total": total,
        "paths": {"covered_ratio": covered, "sheltered_m": routed_m},
        "best_node": {
            "type": best_type,
            "name": best_name,
            "station": best_name,
            "exit": "1",
            "routed_m": routed_m,
        },
    }


def test_normalize_postal_zero_fills():
    assert normalize_postal("123") == "000123"


def test_compare_records_blocks_score_regression():
    active = {"560234": record("560234", 88.4, 0.71)}
    candidate = [record("560234", 96.6, 0.91)]

    report = compare_records(
        active,
        candidate,
        total_tolerance=0.5,
        coverage_tolerance=0.02,
    )

    assert report["ok"] is True
    assert report["blocking_count"] == 0
    assert report["safe_improvement_postals"] == ["560234"]
    assert report["flag_counts"]["total_improvement"] == 1
    assert report["flag_counts"]["coverage_improvement"] == 1


def test_compare_records_holds_wholesale_promotion_on_regression():
    active = {"560710": record("560710", 100.0, 1.0)}
    candidate = [record("560710", 95.4, 0.67)]

    report = compare_records(
        active,
        candidate,
        total_tolerance=0.5,
        coverage_tolerance=0.02,
    )

    assert report["ok"] is False
    assert report["blocking_count"] == 1
    assert report["safe_improvement_postals"] == []
    assert report["blocked_postals"] == ["560710"]
    assert report["flag_counts"]["total_regression"] == 1
    assert report["flag_counts"]["coverage_regression"] == 1
    assert report["promotion_recommendation"] == "hold_for_review_do_not_promote_wholesale"


def test_compare_records_allows_safe_improvement_without_wholesale_promotion():
    active = {
        "560234": record("560234", 88.4, 0.71),
        "560710": record("560710", 100.0, 1.0),
    }
    candidate = [
        record("560234", 96.6, 0.91),
        record("560710", 95.4, 0.67),
    ]

    report = compare_records(
        active,
        candidate,
        total_tolerance=0.5,
        coverage_tolerance=0.02,
    )

    assert report["ok"] is False
    assert report["safe_improvement_count"] == 1
    assert report["safe_improvement_postals"] == ["560234"]
    assert report["blocked_postals"] == ["560710"]
    assert report["promotion_recommendation"] == "promote_safe_improvements_only"


def test_compare_record_separates_same_node_distance_change_from_node_change():
    active = record("560234", 72.0, 0.1, routed_m=300.0)
    candidate = record("560234", 82.0, 0.1, routed_m=100.0)

    comparison = compare_record(
        active,
        candidate,
        total_tolerance=0.5,
        coverage_tolerance=0.02,
    )

    assert "best_node_distance_changed" in comparison["flags"]
    assert "best_node_changed" not in comparison["flags"]


def test_compare_record_flags_true_best_node_identity_change():
    active = record("560234", 72.0, 0.1, best_name="Old Stop")
    candidate = record("560234", 82.0, 0.1, best_name="New Stop")

    comparison = compare_record(
        active,
        candidate,
        total_tolerance=0.5,
        coverage_tolerance=0.02,
    )

    assert "best_node_changed" in comparison["flags"]
    assert "best_node_distance_changed" not in comparison["flags"]


def test_load_candidate_records_accepts_list(tmp_path):
    path = tmp_path / "candidate.json"
    path.write_text('[{"postal":"123456"},{"bad":true}]', encoding="utf-8")

    assert load_candidate_records(path) == [{"postal": "123456"}]


def test_load_candidate_records_accepts_targeted_refresh_report(tmp_path):
    path = tmp_path / "targeted.json"
    path.write_text(
        """
        {
          "comparisons": [
            {"postal": "123456", "after": {"state": "SCORED", "total": 72.0}},
            {"postal": "654321", "after": null}
          ]
        }
        """,
        encoding="utf-8",
    )

    assert load_candidate_records(path) == [{"postal": "123456", "state": "SCORED", "total": 72.0}]
