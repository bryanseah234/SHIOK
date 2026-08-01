import json
from pathlib import Path

from pipeline.export import export_static_artifacts
from pipeline.onemap_validation import (
    build_validation_sample,
    collect_onemap_walk_cache,
    decode_polyline,
    evaluate_cached_results,
    route_cache_key,
)
from tests.test_export import sample_record


def test_decode_polyline_known_google_example():
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_build_validation_sample_uses_stratified_score_geometry(tmp_path: Path):
    records = []
    for index, area in enumerate(["Ang Mo Kio", "Bedok", "Bedok"], start=1):
        record = sample_record(f"12345{index}")
        record["_area"] = area
        records.append(record)
    bundle_dir = tmp_path / "bundle"
    export_static_artifacts(records, output_dir=bundle_dir)

    payload = build_validation_sample(
        bundle_dir=bundle_dir,
        sample_size=2,
        seed="test-seed",
        onemap_delay_sec=2.0,
    )

    assert payload["ok"] is True
    assert payload["sample_size"] == 2
    assert payload["eligible_records"] == 3
    assert payload["projected_wall_clock_seconds"] == 4.0
    assert set(payload["area_quotas"]) == {"ANG_MO_KIO", "BEDOK"}
    assert all(sample["cache_key"] for sample in payload["samples"])
    assert all(sample["start"]["lat"] != sample["end"]["lat"] for sample in payload["samples"])


def test_evaluate_cached_results_reports_missing_and_thresholds(tmp_path: Path):
    start = {"lat": 1.3, "lon": 103.8}
    end = {"lat": 1.301, "lon": 103.801}
    cache_key = route_cache_key(start, end)
    sample_payload = {
        "bundle": "generated_test",
        "sample_size": 2,
        "samples": [
            {
                "postal": "123456",
                "area": "TEST",
                "cache_key": cache_key,
                "project_shortest_m": 105.0,
            },
            {
                "postal": "654321",
                "area": "TEST",
                "cache_key": "missing",
                "project_shortest_m": 200.0,
            },
        ],
    }
    cache_dir = tmp_path / "raw" / "validation" / "onemap_walk"
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{cache_key}.json").write_text(
        json.dumps({"route_summary": {"total_distance": 100.0}}),
        encoding="utf-8",
    )

    report = evaluate_cached_results(sample_payload, cache_dir)

    assert report["gate_passed"] is False
    assert report["cached_results"] == 1
    assert report["missing_cache_results"] == 1
    assert report["median_abs_pct_delta"] == 5.0
    assert report["p95_abs_pct_delta"] == 5.0
    assert report["results_preview"][0]["abs_pct_delta"] == 5.0


def test_collect_onemap_walk_cache_requires_explicit_confirmation(tmp_path: Path):
    sample_payload = {
        "bundle": "generated_test",
        "samples": [
            {
                "postal": "123456",
                "cache_key": "abc",
                "start": {"lat": 1.3, "lon": 103.8},
                "end": {"lat": 1.301, "lon": 103.801},
            }
        ],
    }

    ok, report = collect_onemap_walk_cache(sample_payload, cache_dir=tmp_path)

    assert not ok
    assert "requires --confirm-onemap-collection" in report["errors"][0]
    assert report["will_call_onemap"] is False


def test_collect_onemap_walk_cache_writes_fake_fetcher_result(tmp_path: Path):
    start = {"lat": 1.3, "lon": 103.8}
    end = {"lat": 1.301, "lon": 103.801}
    cache_key = route_cache_key(start, end)
    sample_payload = {
        "bundle": "generated_test",
        "samples": [
            {
                "postal": "123456",
                "area": "TEST",
                "cache_key": cache_key,
                "project_shortest_m": 100.0,
                "start": start,
                "end": end,
            }
        ],
    }

    ok, report = collect_onemap_walk_cache(
        sample_payload,
        cache_dir=tmp_path,
        delay_sec=0,
        confirm_onemap_collection=True,
        fetcher=lambda _sample: {"route_summary": {"total_distance": 101.0}},
    )

    assert ok, report
    assert report["written_cache_results"] == 1
    assert (tmp_path / f"{cache_key}.json").is_file()
    cached_report = evaluate_cached_results(sample_payload, tmp_path)
    assert cached_report["cached_results"] == 1
    assert cached_report["median_abs_pct_delta"] == 0.99
