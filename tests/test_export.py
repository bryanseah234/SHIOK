from pathlib import Path

from shapely.geometry import LineString

from pipeline.score_batch import json_safe_score_record
from pipeline.export import (
    encode_polyline,
    export_static_artifacts,
    load_score_batch_records,
    slugify_area,
    validate_export_batch_args,
    validate_static_artifacts,
    write_json,
)


def sample_record(postal: str = "123456") -> dict:
    return {
        "postal": postal,
        "state": "SCORED",
        "total": 88.8,
        "subscores": {
            "access": 100.0,
            "bus": 90.0,
            "rain": 80.0,
            "heat": 80.0,
            "crossing": 100.0,
        },
        "best_node": {"type": "mrt_lrt_exit", "name": "TEST MRT Exit 1", "routed_m": 200.0},
        "paths": {"shortest_m": 200.0, "sheltered_m": 220.0, "detour_pct": 10.0},
        "exposure_gaps": [{"len_m": 50.0, "label": "open test gap"}],
        "data_as_of": "2026-07-27T00:00:00+00:00",
        "provenance": {"source_hashes": {"osm_extract": "a" * 64}},
        "_area": "Test Area",
        "_origin": {"lat": 1.30001, "lon": 103.80001, "x": 28000.0, "y": 35000.0},
        "_geometry": {
            "shortest": LineString([(28000.0, 35000.0), (28100.0, 35100.0)]),
            "sheltered": LineString([(28000.0, 35000.0), (28120.0, 35100.0)]),
            "exposure_gap_edges": [
                {
                    "length_m": 50.0,
                    "is_covered": False,
                    "geometry": LineString([(28000.0, 35000.0), (28050.0, 35050.0)]),
                }
            ],
        },
    }


def test_slugify_area():
    assert slugify_area("Downtown Core") == "DOWNTOWN_CORE"
    assert slugify_area(None) == "UNKNOWN"


def test_encode_polyline_known_google_example():
    points = [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]

    assert encode_polyline(points) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def test_export_and_validate_static_artifacts(tmp_path: Path):
    records = [sample_record("123456"), sample_record("654321")]

    report = export_static_artifacts(records, output_dir=tmp_path)
    ok, validation = validate_static_artifacts(tmp_path)

    assert report["record_count"] == 2
    assert report["score_area_count"] == 1
    assert report["geom_shard_count"] >= 1
    assert ok, validation
    assert validation["indexed_postals"] == 2
    assert validation["geometry_postals"] == 2


def test_load_score_batch_records_reads_chunks_in_order_and_rejects_duplicates(tmp_path: Path):
    records_dir = tmp_path / "batch"
    chunks_dir = records_dir / "chunks"
    write_json(
        chunks_dir / "chunk_00002_654321_654321.json",
        [json_safe_score_record(sample_record("654321"))],
    )
    write_json(
        chunks_dir / "chunk_00001_123456_123456.json",
        [json_safe_score_record(sample_record("123456"))],
    )

    records = load_score_batch_records(records_dir)

    assert [record["postal"] for record in records] == ["123456", "654321"]

    write_json(
        chunks_dir / "chunk_00003_123456_123456.json",
        [json_safe_score_record(sample_record("123456"))],
    )
    try:
        load_score_batch_records(records_dir)
    except ValueError as exc:
        assert str(exc) == "duplicate postal across score batch chunks: 123456"
    else:
        raise AssertionError("duplicate postal was accepted")


def test_load_score_batch_records_requires_chunks_directory(tmp_path: Path):
    try:
        load_score_batch_records(tmp_path / "missing")
    except FileNotFoundError as exc:
        assert "score batch chunks directory not found" in str(exc)
    else:
        raise AssertionError("missing chunks directory was accepted")


def test_validate_rejects_missing_required_artifacts(tmp_path: Path):
    ok, report = validate_static_artifacts(tmp_path)

    assert not ok
    assert "missing required file: manifest.json" in report["errors"]


def test_validate_export_batch_args_blocks_full_batch_without_checkpoint_confirmation():
    errors = validate_export_batch_args(
        full_batch=True,
        confirm_full_batch=False,
        postal_universe_path=Path("processed/postal_universe_official_current.parquet"),
    )

    assert errors == [
        "full export batch requires --confirm-full-batch after checkpoint approval",
    ]


def test_validate_export_batch_args_requires_postal_universe_for_full_batch():
    errors = validate_export_batch_args(
        full_batch=True,
        confirm_full_batch=True,
        postal_universe_path=None,
    )

    assert errors == ["--full-batch requires --postal-universe"]


def test_validate_export_batch_args_accepts_non_batch_default():
    errors = validate_export_batch_args(
        full_batch=False,
        confirm_full_batch=False,
        postal_universe_path=None,
    )

    assert errors == []
