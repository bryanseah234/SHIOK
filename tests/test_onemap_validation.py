import json
import gzip
from pathlib import Path

import pandas as pd

from pipeline.export import export_static_artifacts
from pipeline.onemap_validation import (
    build_targeted_risk_validation_sample,
    build_validation_sample,
    collect_onemap_walk_cache,
    decode_polyline,
    evaluate_cached_results,
    haversine_distance_m,
    load_gate_config,
    onemap_distance_sanity,
    route_cache_key,
    validation_route_trust,
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
    assert all(sample["routing_type"] == "unknown" for sample in payload["samples"])
    assert all(sample["route_trust"] == "graph_routed_mrt_lrt" for sample in payload["samples"])


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


def test_build_validation_sample_skips_missing_geometry_shard(tmp_path: Path):
    records = [sample_record("123456"), sample_record("123457")]
    bundle_dir = tmp_path / "bundle"
    export_static_artifacts(records, output_dir=bundle_dir)
    index_path = bundle_dir / "geom" / "postal-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["123456"] = "missing-shard"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    payload = build_validation_sample(
        bundle_dir=bundle_dir,
        sample_size=2,
        seed="test-seed",
    )

    assert payload["ok"] is True
    assert payload["raw_candidate_records"] == 2
    assert payload["eligible_records"] == 1
    assert payload["skipped_endpoint_records"] == 1
    assert payload["sample_size"] == 1
    assert payload["samples"][0]["postal"] == "123457"


def test_build_validation_sample_reads_gzipped_bundle_artifacts(tmp_path: Path):
    record = sample_record("123456")
    record["best_node"] = {
        "type": "bus_stop",
        "exit": "54321",
        "name": "Test Stop",
        "routed_m": 200.0,
    }
    bundle_dir = tmp_path / "bundle"
    export_static_artifacts([record], output_dir=bundle_dir)
    transit_payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [103.812345, 1.323456]},
                "properties": {"kind": "bus_stop", "code": "54321", "name": "Test Stop"},
            }
        ],
    }
    transit_path = bundle_dir / "transit" / "pois.json"
    transit_path.parent.mkdir(exist_ok=True)
    transit_path.write_text(json.dumps(transit_payload), encoding="utf-8")
    for path in [transit_path, next((bundle_dir / "geom" / "h3").glob("*.json"))]:
        raw_payload = path.read_bytes()
        with gzip.open(f"{path}.gz", "wb") as f:
            f.write(raw_payload)
        path.unlink()
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

    sample_payload = build_validation_sample(
        bundle_dir=bundle_dir,
        postal_universe_path=universe_path,
        sample_size=1,
    )

    assert sample_payload["ok"] is True
    assert sample_payload["eligible_records"] == 1
    assert sample_payload["skipped_endpoint_records"] == 0
    assert sample_payload["samples"][0]["endpoint_source"] == "postal_universe_to_transit_poi"


def test_build_targeted_risk_validation_sample_prioritizes_bus_connectors_and_partials(
    tmp_path: Path,
):
    bus_record = sample_record("123456")
    bus_record["state"] = "SCORED_PARTIAL"
    bus_record["total"] = None
    bus_record["subscores"] = None
    bus_record["best_node"] = {
        "type": "bus_stop",
        "exit": "54321",
        "name": "Risk Stop",
        "routed_m": 75.0,
    }
    bus_record["paths"]["shortest_m"] = 75.0
    bus_record["paths"]["sheltered_m"] = 75.0
    bus_record["paths"]["routing_type"] = "sheltered_with_bus_stop_access_connector"
    mrt_record = sample_record("123457")
    bundle_dir = tmp_path / "bundle"
    export_static_artifacts([bus_record, mrt_record], output_dir=bundle_dir)
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
                            "name": "Risk Stop",
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
            },
            {
                "postal_code": "123457",
                "lat": 1.312346,
                "lon": 103.801235,
                "status": "READY_TO_SCORE",
            },
        ]
    ).to_parquet(universe_path, index=False)
    prior_report = tmp_path / "prior.json"
    prior_report.write_text(
        json.dumps({"results": [{"postal": "123456", "abs_pct_delta": 55.0}]}),
        encoding="utf-8",
    )

    payload = build_targeted_risk_validation_sample(
        bundle_dir=bundle_dir,
        postal_universe_path=universe_path,
        sample_size=10,
        prior_report_path=prior_report,
    )

    assert payload["ok"] is True
    assert payload["sample_kind"] == "targeted_high_risk"
    assert payload["sample_size"] == 1
    sample = payload["samples"][0]
    assert sample["postal"] == "123456"
    assert sample["risk_score"] > 10
    assert set(sample["risk_flags"]) >= {
        "bus_route",
        "endpoint_connector",
        "scored_partial",
        "very_short_route",
        "prior_delta_over_50_pct",
    }
    assert payload["risk_flag_counts"]["bus_route"] == 1


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
                "routing_type": "sheltered_with_bus_stop_access_connector",
                "route_trust": "graph_route_with_endpoint_connector",
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
    assert report["median_abs_delta_m"] == 5.0
    assert report["p95_abs_delta_m"] == 5.0
    assert report["results_preview"][0]["abs_delta_m"] == 5.0
    assert report["results_preview"][0]["signed_delta_m"] == 5.0
    assert report["results_preview"][0]["onemap_walk_bucket"] == "gt_50m_le_100m"
    assert report["results_preview"][0]["abs_pct_delta"] == 5.0
    assert report["results_preview"][0]["signed_pct_delta"] == 5.0
    assert report["results_preview"][0]["direction"] == "project_longer_than_onemap"
    assert report["results_preview"][0]["start"] == start
    assert report["results_preview"][0]["routing_type"] == (
        "sheltered_with_bus_stop_access_connector"
    )
    assert report["results_preview"][0]["route_trust"] == ("graph_route_with_endpoint_connector")
    assert 150.0 < report["results_preview"][0]["direct_distance_m"] < 160.0
    assert report["results_preview"][0]["distance_sanity"] == (
        "onemap_materially_shorter_than_direct"
    )
    assert report["subset_summary"]["all_valid_cached"]["count"] == 1
    assert report["subset_summary"]["all_valid_cached"]["thresholds_passed"] is True
    assert report["subset_summary"]["endpoint_connector"]["count"] == 1
    assert report["subset_summary"]["endpoint_connector_plausible_onemap_distance"]["count"] == 0
    assert report["subset_summary"]["graph_routed_without_endpoint_connector"]["count"] == 0
    assert (
        report["subset_summary"][
            "graph_routed_without_endpoint_connector_plausible_onemap_distance"
        ]["count"]
        == 0
    )
    assert (
        report["subset_summary"]["graph_routed_without_endpoint_connector"]["thresholds_passed"]
        is None
    )
    assert "results" not in report
    full_report = evaluate_cached_results(sample_payload, cache_dir, include_results=True)
    assert full_report["results"] == full_report["results_preview"]
    assert report["distance_sanity_summary"] == {"onemap_materially_shorter_than_direct": 1}
    assert report["route_trust_summary"] == [
        {
            "route_trust": "graph_route_with_endpoint_connector",
            "count": 1,
            "median_abs_pct_delta": 5.0,
            "p95_abs_pct_delta": 5.0,
            "max_abs_pct_delta": 5.0,
            "median_abs_delta_m": 5.0,
            "p95_abs_delta_m": 5.0,
            "max_abs_delta_m": 5.0,
            "over_25_pct_count": 0,
            "over_50_pct_count": 0,
        }
    ]
    assert report["routing_type_summary"] == [
        {
            "routing_type": "sheltered_with_bus_stop_access_connector",
            "count": 1,
            "median_abs_pct_delta": 5.0,
            "p95_abs_pct_delta": 5.0,
            "max_abs_pct_delta": 5.0,
            "median_abs_delta_m": 5.0,
            "p95_abs_delta_m": 5.0,
            "max_abs_delta_m": 5.0,
            "over_25_pct_count": 0,
            "over_50_pct_count": 0,
        }
    ]
    assert report["transit_type_summary"] == [
        {
            "best_node_type": "bus_stop",
            "count": 1,
            "median_abs_pct_delta": 5.0,
            "p95_abs_pct_delta": 5.0,
            "max_abs_pct_delta": 5.0,
            "median_abs_delta_m": 5.0,
            "p95_abs_delta_m": 5.0,
            "max_abs_delta_m": 5.0,
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
            "median_abs_delta_m": 5.0,
            "p95_abs_delta_m": 5.0,
            "max_abs_delta_m": 5.0,
            "over_25_pct_count": 0,
            "over_50_pct_count": 0,
        }
    ]
    assert report["onemap_walk_bucket_summary"] == [
        {
            "onemap_walk_bucket": "gt_50m_le_100m",
            "count": 1,
            "median_abs_pct_delta": 5.0,
            "p95_abs_pct_delta": 5.0,
            "max_abs_pct_delta": 5.0,
            "median_abs_delta_m": 5.0,
            "p95_abs_delta_m": 5.0,
            "max_abs_delta_m": 5.0,
            "over_25_pct_count": 0,
            "over_50_pct_count": 0,
        }
    ]
    assert report["top_outliers_preview"][0]["best_node_name"] == "Test Stop"


def test_haversine_distance_and_onemap_distance_sanity():
    direct_m = haversine_distance_m(
        {"lat": 1.3, "lon": 103.8},
        {"lat": 1.301, "lon": 103.801},
    )

    assert direct_m is not None
    assert 150.0 < direct_m < 160.0
    assert onemap_distance_sanity(100.0, direct_m) == "onemap_materially_shorter_than_direct"
    assert onemap_distance_sanity(145.0, direct_m) == "onemap_slightly_shorter_than_direct"
    assert onemap_distance_sanity(170.0, direct_m) == "plausible"
    assert onemap_distance_sanity(100.0, None) == "missing_coordinates"


def test_validation_route_trust_classifies_route_contract():
    assert (
        validation_route_trust(
            node_type="bus_stop",
            routing_type="direct_bus_fallback_unrouted",
        )
        == "partial_unrouted_bus_fallback"
    )
    assert (
        validation_route_trust(
            node_type="bus_stop",
            routing_type="sheltered_with_bus_stop_access_connector",
        )
        == "graph_route_with_endpoint_connector"
    )
    assert (
        validation_route_trust(
            node_type="mrt_lrt_exit",
            routing_type="sheltered_with_mrt_lrt_exit_access_connector",
        )
        == "graph_route_with_endpoint_connector"
    )
    assert (
        validation_route_trust(node_type="bus_stop", routing_type="sheltered")
        == "graph_routed_bus_stop"
    )
    assert (
        validation_route_trust(node_type="mrt_lrt_exit", routing_type="sheltered")
        == "graph_routed_mrt_lrt"
    )


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
    assert len(report["top_outliers_by_direction"]["project_longer_than_onemap"]) == 24
    assert len(report["top_outliers_by_direction"]["same_length"]) == 1
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


def _write_cache(cache_dir: Path, cache_key: str, onemap_m: float) -> None:
    (cache_dir / f"{cache_key}.json").write_text(
        json.dumps({"route_summary": {"total_distance": onemap_m}}),
        encoding="utf-8",
    )


def _gate_realism_sample_payload(cache_dir: Path) -> dict:
    """Build a three-row synthetic sample: plausible, snap-bug, OneMap-impossible.

    All three rows share the same ~100 m direct crow-flies distance so that the
    plausibility filter alone decides membership.
    """

    row_configs = [
        ("100001", (1.30, 103.8), (1.3009, 103.8), 100.0, 105.0),  # plausible
        ("100002", (1.31, 103.8), (1.3109, 103.8), 80.0, 100.0),  # project snap-bug
        ("100003", (1.32, 103.8), (1.3209, 103.8), 100.0, 70.0),  # onemap-impossible
    ]
    samples: list[dict] = []
    for postal, start_ll, end_ll, project_m, onemap_m in row_configs:
        start = {"lat": start_ll[0], "lon": start_ll[1]}
        end = {"lat": end_ll[0], "lon": end_ll[1]}
        cache_key = route_cache_key(start, end)
        _write_cache(cache_dir, cache_key, onemap_m)
        samples.append(
            {
                "postal": postal,
                "area": "TEST",
                "cache_key": cache_key,
                "start": start,
                "end": end,
                "project_shortest_m": project_m,
                "best_node": {"type": "bus_stop", "name": f"Stop {postal}"},
                "routing_type": "sheltered",
                "route_trust": "graph_routed_bus_stop",
                "endpoint_source": "postal_universe_to_transit_poi",
            }
        )
    return {
        "bundle": "generated_test",
        "sample_size": len(samples),
        "samples": samples,
    }


def test_load_gate_config_defaults_when_yaml_section_missing(tmp_path: Path):
    empty_yaml = tmp_path / "params.yaml"
    empty_yaml.write_text("unrelated: true\n", encoding="utf-8")

    config = load_gate_config(empty_yaml)

    assert config == {
        "median_abs_pct_delta_max": 12.0,
        "p95_abs_pct_delta_max": 100.0,
        "require_distance_sanity_plausible": True,
        "project_snap_bug_ratio": 0.98,
    }


def test_load_gate_config_reads_repo_params_yaml():
    config = load_gate_config()

    assert config["median_abs_pct_delta_max"] == 12.0
    assert config["p95_abs_pct_delta_max"] == 100.0
    assert config["require_distance_sanity_plausible"] is True
    assert config["project_snap_bug_ratio"] == 0.98


def test_evaluate_cached_results_gate_metrics_filters_plausible_and_snap_bug(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    sample_payload = _gate_realism_sample_payload(cache_dir)

    report = evaluate_cached_results(sample_payload, cache_dir)

    # Full unfiltered stats are preserved for transparency.
    assert report["cached_results"] == 3
    assert report["median_abs_pct_delta"] == 20.0
    assert report["p95_abs_pct_delta"] == 40.571
    assert report["distance_sanity_summary"]["plausible"] == 2
    assert report["distance_sanity_summary"]["onemap_materially_shorter_than_direct"] == 1

    # Gate metrics compute over the plausible + non-snap-bug subset (row 1 only).
    gate_metrics = report["gate_metrics"]
    assert gate_metrics["filtered_row_count"] == 1
    assert gate_metrics["filter_excluded_count"] == 2
    assert gate_metrics["median_abs_pct_delta"] == 4.762
    assert gate_metrics["p95_abs_pct_delta"] == 4.762
    assert gate_metrics["require_distance_sanity_plausible"] is True
    assert gate_metrics["project_snap_bug_ratio"] == 0.98

    # Gate uses the filtered subset: 4.762 <= 12 median max and <= 100 p95 max.
    assert report["gate_passed"] is True
    assert report["ok"] is True
    assert report["thresholds"] == {
        "median_abs_pct_delta_max": 12.0,
        "p95_abs_pct_delta_max": 100.0,
    }


def test_evaluate_cached_results_gate_uses_unfiltered_when_flag_disabled(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    sample_payload = _gate_realism_sample_payload(cache_dir)

    disabled_config = {
        "median_abs_pct_delta_max": 12.0,
        "p95_abs_pct_delta_max": 100.0,
        "require_distance_sanity_plausible": False,
        "project_snap_bug_ratio": 0.98,
    }
    report = evaluate_cached_results(sample_payload, cache_dir, gate_config=disabled_config)

    gate_metrics = report["gate_metrics"]
    assert gate_metrics["filtered_row_count"] == 3
    assert gate_metrics["filter_excluded_count"] == 0
    assert gate_metrics["median_abs_pct_delta"] == 20.0
    assert gate_metrics["p95_abs_pct_delta"] == 40.571
    assert gate_metrics["require_distance_sanity_plausible"] is False

    # Median of 20 exceeds 12 -> gate fails when the filter is off.
    assert report["gate_passed"] is False
    assert report["ok"] is False


def test_evaluate_cached_results_gate_config_override_thresholds(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    sample_payload = _gate_realism_sample_payload(cache_dir)

    # Tighten thresholds so even the single plausible row (4.762%) fails the median cap.
    tight_config = {
        "median_abs_pct_delta_max": 1.0,
        "p95_abs_pct_delta_max": 100.0,
        "require_distance_sanity_plausible": True,
        "project_snap_bug_ratio": 0.98,
    }
    report = evaluate_cached_results(sample_payload, cache_dir, gate_config=tight_config)

    assert report["gate_metrics"]["filtered_row_count"] == 1
    assert report["gate_passed"] is False
    assert report["thresholds"] == {
        "median_abs_pct_delta_max": 1.0,
        "p95_abs_pct_delta_max": 100.0,
    }
