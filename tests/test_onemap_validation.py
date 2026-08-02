import json
from pathlib import Path

import pandas as pd

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


def test_build_validation_sample_prefers_postal_and_transit_source_endpoints(tmp_path: Path):
    record = sample_record("123456")
    record["best_node"] = {
        "type": "bus_stop",
        "exit": "54321",
        "name": "Test Stop",
        "routed_m": 200.0,
    }
    bundle_dir = tmp_path / "bundle"
    export_static_artifacts([record], output_dir=bundle_dir)
    (bundle_dir / "transit").mkdir(exist_ok=True)
    (bundle_dir / "transit" / "pois.json").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [103.812345, 1.323456]},
                        "properties": {
                            "kind": "bus_stop",
                            "code": "54321",
                            "name": "Test Stop",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    universe_path = tmp_path / "universe.parquet"
    pd.DataFrame(
        [
            {
                "postal_code": "123456",
                "lat": 1.312345,
                "lon": 103.801234,
                "status": "READY_TO_SCORE",
            }
        ]
    ).to_parquet(universe_path, index=False)

    payload = build_validation_sample(
        bundle_dir=bundle_dir,
        postal_universe_path=universe_path,
        sample_size=1,
    )

    assert payload["samples"][0]["endpoint_source"] == "postal_universe_to_transit_poi"
    assert payload["samples"][0]["start"] == {"lat": 1.312345, "lon": 103.801234}
    assert payload["samples"][0]["end"] == {"lat": 1.323456, "lon": 103.812345}


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
                "start": start,
                "end": end,
                "project_shortest_m": 105.0,
                "best_node": {"type": "bus_stop", "name": "Test Stop"},
                "endpoint_source": "postal_universe_to_transit_poi",
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
    assert report["results_preview"][0]["signed_pct_delta"] == 5.0
    assert report["results_preview"][0]["direction"] == "project_longer_than_onemap"
    assert report["results_preview"][0]["start"] == start
    assert report["transit_type_summary"] == [
        {
            "best_node_type": "bus_stop",
            "count": 1,
            "median_abs_pct_delta": 5.0,
            "p95_abs_pct_delta": 5.0,
            "max_abs_pct_delta": 5.0,
            "over_25_pct_count": 0,
            "over_50_pct_count": 0,
        }
    ]
    assert report["direction_summary"] == [
        {
            "direction": "project_longer_than_onemap",
            "count": 1,
            "median_abs_pct_delta": 5.0,
            "p95_abs_pct_delta": 5.0,
            "max_abs_pct_delta": 5.0,
            "over_25_pct_count": 0,
            "over_50_pct_count": 0,
        }
    ]
    assert report["top_outliers_preview"][0]["best_node_name"] == "Test Stop"


def test_evaluate_cached_results_reports_zero_distance_as_invalid(tmp_path: Path):
    start = {"lat": 1.3, "lon": 103.8}
    end = {"lat": 1.301, "lon": 103.801}
    cache_key = route_cache_key(start, end)
    sample_payload = {
        "bundle": "generated_test",
        "sample_size": 1,
        "samples": [
            {
                "postal": "123456",
                "area": "TEST",
                "cache_key": cache_key,
                "project_shortest_m": 105.0,
            }
        ],
    }
    (tmp_path / f"{cache_key}.json").write_text(
        json.dumps({"route_summary": {"total_distance": 0}}),
        encoding="utf-8",
    )

    report = evaluate_cached_results(sample_payload, tmp_path)

    assert report["gate_passed"] is False
    assert report["cached_results"] == 0
    assert report["invalid_cache_results"] == 1
    assert report["invalid_cache_preview"][0]["reason"] == "missing_or_non_positive_distance"


def test_evaluate_cached_results_keeps_top_100_outlier_preview(tmp_path: Path):
    samples = []
    for index in range(25):
        start = {"lat": 1.3, "lon": 103.8 + index / 100000}
        end = {"lat": 1.301, "lon": 103.801 + index / 100000}
        cache_key = route_cache_key(start, end)
        samples.append(
            {
                "postal": f"{index:06d}",
                "area": "TEST",
                "cache_key": cache_key,
                "project_shortest_m": 100.0 + index,
                "best_node": {"type": "bus_stop", "name": f"Stop {index}"},
                "endpoint_source": "postal_universe_to_transit_poi",
            }
        )
        (tmp_path / f"{cache_key}.json").write_text(
            json.dumps({"route_summary": {"total_distance": 100.0}}),
            encoding="utf-8",
        )

    report = evaluate_cached_results(
        {"bundle": "generated_test", "sample_size": len(samples), "samples": samples},
        tmp_path,
    )

    assert len(report["results_preview"]) == 20
    assert len(report["top_outliers_preview"]) == 25
    assert report["top_outliers_preview"][0]["postal"] == "000024"


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
