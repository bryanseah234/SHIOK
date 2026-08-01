from scripts.audit_current_bundle import sample_postals


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
