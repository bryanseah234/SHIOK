from scripts.audit_current_bundle import sample_postals, summarize_state_report


def _record(
    postal: str,
    area: str,
    *,
    state: str = "NO_TRANSIT_IN_RANGE",
    bus_candidates: int = 0,
) -> dict:
    return {
        "postal": postal,
        "_area": area,
        "state": state,
        "provenance": {
            "transit_node_set": {
                "bus_stop_candidates_direct": bus_candidates,
            }
        },
    }


def test_sample_postals_respects_replay_limit_during_top_area_selection():
    records = [
        _record("100001", "SERANGOON"),
        _record("100002", "BUKIT_TIMAH"),
        _record("100003", "ANG_MO_KIO"),
        _record("100004", "HOUGANG"),
        _record("100005", "CLEMENTI", bus_candidates=2),
        _record("100006", "BEDOK", bus_candidates=3),
    ]

    selected = sample_postals(records, replay_limit=4)

    assert selected == ["100001", "100002", "100003", "100004"]


def test_sample_postals_ignores_scored_records_and_zero_limit():
    records = [
        _record("100001", "SERANGOON", state="SCORED"),
        _record("100002", "BUKIT_TIMAH"),
    ]

    assert sample_postals(records, replay_limit=0) == []
    assert sample_postals(records, replay_limit=10) == ["100002"]


def test_sample_postals_tops_up_when_direct_bus_bucket_is_empty():
    records = [
        _record(f"{index:06d}", "SERANGOON" if index % 2 else "BUKIT_TIMAH")
        for index in range(100001, 100021)
    ]

    selected = sample_postals(records, replay_limit=12)

    assert len(selected) == 12
    assert len(set(selected)) == 12


def test_summarize_state_report_keeps_only_operator_counts():
    summary = summarize_state_report(
        {
            "bundle": "generated_example",
            "manifest_record_count": 124032,
            "state_counts": {"SCORED": 2, "NOT_YET_SCORED": 1},
            "not_yet_scored": {"count": 1, "samples": [{"postal": "999999"}]},
            "no_transit_in_range": {"count": 3, "samples": [{"postal": "888888"}]},
            "scored": {"count": 2},
        }
    )

    assert summary == {
        "bundle": "generated_example",
        "manifest_record_count": 124032,
        "state_counts": {"SCORED": 2, "NOT_YET_SCORED": 1},
        "no_transit_count": 3,
        "not_yet_count": 1,
    }
