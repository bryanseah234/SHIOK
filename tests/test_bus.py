from pipeline.bus import (
    build_stop_service_headways,
    combined_expected_wait_min,
    parse_peak_frequency_minutes,
)


def test_parse_peak_frequency_minutes():
    assert parse_peak_frequency_minutes("06-08") == 7.0
    assert parse_peak_frequency_minutes("10") == 10.0
    assert parse_peak_frequency_minutes("-") is None
    assert parse_peak_frequency_minutes("") is None
    assert parse_peak_frequency_minutes("bad") is None


def test_combined_expected_wait_min():
    assert combined_expected_wait_min([]) is None
    assert combined_expected_wait_min([10.0]) == 5.0
    assert combined_expected_wait_min([10.0, 10.0]) == 2.5


def test_build_stop_service_headways_joins_routes_to_parseable_am_peak_services():
    services = [
        {"ServiceNo": "10", "Direction": 1, "AM_Peak_Freq": "08-10"},
        {"ServiceNo": "20", "Direction": 1, "AM_Peak_Freq": "-"},
        {"ServiceNo": "30", "Direction": 2, "AM_Peak_Freq": "06-08"},
    ]
    routes = [
        {"BusStopCode": "01012", "ServiceNo": "10", "Direction": 1},
        {"BusStopCode": "01012", "ServiceNo": "20", "Direction": 1},
        {"BusStopCode": "01013", "ServiceNo": "30", "Direction": 2},
    ]

    stop_headways = build_stop_service_headways(services, routes)

    assert stop_headways == {
        "01012": {("10", 1): 9.0},
        "01013": {("30", 2): 7.0},
    }
