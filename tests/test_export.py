import json
from pathlib import Path

from shapely.geometry import LineString

from pipeline.score_batch import json_safe_score_record
from pipeline.export import (
    build_transit_poi_collection,
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


def unscored_record(postal: str) -> dict:
    return {
        "postal": postal,
        "state": "NOT_YET_SCORED",
        "total": None,
        "subscores": None,
        "best_node": None,
        "paths": None,
        "exposure_gaps": None,
        "data_as_of": "2026-07-27T00:00:00+00:00",
        "provenance": {"reason": "test"},
        "_area": "Large Area",
    }


def test_slugify_area():
    assert slugify_area("Downtown Core") == "DOWNTOWN_CORE"
    assert slugify_area(None) == "UNKNOWN"
    assert slugify_area(float("nan")) == "UNKNOWN"


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
    postal_index = json.loads((tmp_path / "geom" / "postal-index.json").read_text())
    for postal in ["123456", "654321"]:
        shard = postal_index[postal]
        shard_records = json.loads((tmp_path / "geom" / "h3" / f"{shard}.json").read_text())
        assert postal in {record["postal"] for record in shard_records}


def test_build_transit_poi_collection_exports_mrt_and_bus_points():
    mrt_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [103.83658, 1.36708]},
                "properties": {
                    "OBJECTID": 1,
                    "STATION_NA": "MAYFLOWER MRT STATION",
                    "EXIT_CODE": "Exit 5",
                },
            }
        ],
    }
    bus_payload = {
        "value": [
            {
                "BusStopCode": "55089",
                "Description": "Mayflower Stn Exit 5",
                "Latitude": 1.367,
                "Longitude": 103.837,
                "RoadName": "Ang Mo Kio Ave 4",
            }
        ]
    }

    collection = build_transit_poi_collection(
        mrt_geojson,
        bus_payload,
        {"source_hashes": {"mrt_lrt_exits": "a" * 64, "bus_stops": "b" * 64}},
    )

    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 2
    kinds = {feature["properties"]["kind"] for feature in collection["features"]}
    assert kinds == {"mrt_exit", "bus_stop"}
    mrt = next(
        feature for feature in collection["features"] if feature["properties"]["kind"] == "mrt_exit"
    )
    bus = next(
        feature for feature in collection["features"] if feature["properties"]["kind"] == "bus_stop"
    )
    assert mrt["properties"]["name"] == "MAYFLOWER MRT STATION Exit 5"
    assert bus["properties"]["code"] == "55089"


def test_export_splits_large_score_files(tmp_path: Path):
    records = [unscored_record(f"1000{i:02d}") for i in range(40)]

    report = export_static_artifacts(records, output_dir=tmp_path, score_shard_max_bytes=1200)
    ok, validation = validate_static_artifacts(tmp_path)

    assert report["score_area_count"] == 1
    assert report["score_shard_count"] > 1
    assert ok, validation
    for path in (tmp_path / "scores").glob("LARGE_AREA_PART_*.json"):
        assert path.stat().st_size <= 1200


def test_export_merges_promoted_geom_shards_with_duplicate_child_id(tmp_path: Path, monkeypatch):
    def fake_latlng_to_cell(lat: float, _lon: float, resolution: int) -> str:
        if resolution == 8:
            return "parent-a" if lat < 1.31 else "parent-b"
        return "shared-child"

    monkeypatch.setattr("pipeline.export.h3.latlng_to_cell", fake_latlng_to_cell)
    records = [sample_record("123456"), sample_record("654321")]
    records[0]["_origin"]["lat"] = 1.30
    records[1]["_origin"]["lat"] = 1.32

    report = export_static_artifacts(
        records,
        output_dir=tmp_path,
        geom_promotion_threshold_bytes=1,
    )
    ok, validation = validate_static_artifacts(tmp_path)

    assert ok, validation
    assert report["geom_shard_count"] == 1
    geom_records = json.loads((tmp_path / "geom" / "h3" / "shared-child.json").read_text())
    assert sorted(record["postal"] for record in geom_records) == ["123456", "654321"]


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
