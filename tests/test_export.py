import gzip
import json
from pathlib import Path

from shapely.geometry import LineString, MultiLineString

from pipeline.export import (
    build_transit_poi_collection,
    encode_polyline,
    export_static_artifacts,
    geom_record,
    json_size,
    load_score_batch_records,
    refresh_score_provenance_manifest,
    refresh_transit_manifest,
    route_edge_source_class,
    route_segment_geometries,
    station_code_rows_from_xls_bytes,
    slugify_area,
    validate_export_batch_args,
    validate_static_artifacts,
    write_json,
)
from pipeline.score_batch import json_safe_score_record


def gzip_json_file(path: Path) -> None:
    payload = path.read_bytes()
    with gzip.open(path.with_name(f"{path.name}.gz"), "wb") as f:
        f.write(payload)
    path.unlink()


def test_route_edge_source_class_uses_osm_location_provenance():
    assert (
        route_edge_source_class({"is_covered": True, "location": "underground"})
        == "bridge_underpass"
    )
    assert route_edge_source_class({"is_covered": True, "location": "indoor"}) == "osm_covered"
    assert route_edge_source_class({"is_covered": True, "shelter": "yes"}) == "osm_covered"
    assert (
        route_edge_source_class({"is_covered": True, "weather_protection": "yes"}) == "osm_covered"
    )
    assert (
        route_edge_source_class({"is_covered": True, "source_layer": "osm_native_covered"})
        == "osm_covered"
    )
    assert route_edge_source_class({"is_covered": True, "building:part": "roof"}) == "osm_covered"
    assert route_edge_source_class({"is_covered": True, "man_made": "canopy"}) == "osm_covered"
    assert route_edge_source_class({"is_covered": True, "covered": "building_arcade"}) == (
        "osm_covered"
    )
    assert route_edge_source_class({"is_covered": True, "covered": "shelter"}) == "osm_covered"
    assert route_edge_source_class({"is_covered": True, "covered": "roof"}) == "osm_covered"
    assert (
        route_edge_source_class(
            {"is_covered": True, "public_transport": "platform", "shelter": "yes"}
        )
        == "osm_covered"
    )
    assert route_edge_source_class({"is_covered": True, "shelter_type": "roof"}) == "osm_covered"


def test_route_edge_source_class_labels_direct_bus_fallback():
    assert (
        route_edge_source_class(
            {
                "is_covered": False,
                "source_layer": "direct_bus_fallback",
                "synth_class": "unrouted_straight_line",
            }
        )
        == "direct_unrouted_bus"
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
        "provenance": {
            "source_hashes": {"osm_extract": "a" * 64},
            "subscore_status": {
                "access": "real_routed_shortest_distance",
                "bus": "real_static_datamall_connectivity",
                "rain": "real_routed_covered_length_ratio",
                "heat": "provisional_covered_plus_nparks_shade_proxy_heat_only",
                "crossing": "real_traffic_signals_with_grade_separated_exemption",
            },
        },
        "_area": "Test Area",
        "_origin": {"lat": 1.30001, "lon": 103.80001, "x": 28000.0, "y": 35000.0},
        "_geometry": {
            "shortest": LineString([(28000.0, 35000.0), (28100.0, 35100.0)]),
            "sheltered": LineString([(28000.0, 35000.0), (28120.0, 35100.0)]),
            "shortest_path_edges": [
                {
                    "length_m": 80.0,
                    "is_covered": False,
                    "geometry": LineString([(28000.0, 35000.0), (28040.0, 35040.0)]),
                },
                {
                    "length_m": 120.0,
                    "is_covered": True,
                    "geometry": LineString([(28040.0, 35040.0), (28100.0, 35100.0)]),
                },
            ],
            "sheltered_path_edges": [
                {
                    "length_m": 50.0,
                    "is_covered": False,
                    "geometry": LineString([(28000.0, 35000.0), (28050.0, 35050.0)]),
                },
                {
                    "length_m": 170.0,
                    "is_covered": True,
                    "geometry": LineString([(28050.0, 35050.0), (28120.0, 35100.0)]),
                },
            ],
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
    assert validation["score_prefixes"] == 2
    assert validation["geometry_postals"] == 2
    assert validation["geometry_postals_with_route_segments"] == 2
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provenance"]["source_hashes"]["osm_extract"] == "a" * 64
    assert (
        manifest["provenance"]["subscore_status"]["heat"]
        == "provisional_covered_plus_nparks_shade_proxy_heat_only"
    )
    prefix_index = json.loads((tmp_path / "scores" / "prefix-index.json").read_text())
    assert prefix_index["123"] == ["TEST_AREA"]
    assert prefix_index["654"] == ["TEST_AREA"]
    postal_index = json.loads((tmp_path / "geom" / "postal-index.json").read_text())
    for postal in ["123456", "654321"]:
        shard = postal_index[postal]
        shard_records = json.loads((tmp_path / "geom" / "h3" / f"{shard}.json").read_text())
        assert postal in {record["postal"] for record in shard_records}
        geom_record = next(record for record in shard_records if record["postal"] == postal)
        assert geom_record["route_segments"]["shortest"][0]["is_covered"] is False
        assert geom_record["route_segments"]["shortest"][1]["is_covered"] is True
        assert geom_record["route_segments"]["sheltered"][0]["len_m"] == 50.0


def test_validate_accepts_gzipped_json_artifacts(tmp_path: Path):
    records = [sample_record("123456"), sample_record("654321")]
    export_static_artifacts(records, output_dir=tmp_path)

    gzip_json_file(tmp_path / "scores" / "index.json")
    gzip_json_file(tmp_path / "geom" / "index.json")
    gzip_json_file(tmp_path / "transit" / "pois.json")
    for path in (tmp_path / "scores").glob("TEST_AREA*.json"):
        gzip_json_file(path)
    for path in (tmp_path / "geom" / "h3").glob("*.json"):
        gzip_json_file(path)

    ok, validation = validate_static_artifacts(tmp_path)

    assert ok, validation
    assert validation["indexed_postals"] == 2
    assert validation["geometry_postals"] == 2
    assert validation["geometry_postals_with_route_segments"] == 2


def test_route_segments_split_disjoint_multiline_parts_without_fake_connector():
    segments = route_segment_geometries(
        [
            {
                "length_m": 10.0,
                "is_covered": True,
                "geometry": LineString([(0, 0), (10, 0)]),
            },
            {
                "length_m": 10.0,
                "is_covered": True,
                "geometry": LineString([(100, 0), (110, 0)]),
            },
        ]
    )

    assert len(segments) == 2
    assert all(segment["is_covered"] is True for segment in segments)
    assert all(segment["len_m"] == 10.0 for segment in segments)
    assert segments[0]["geom"] != segments[1]["geom"]


def test_geom_record_emits_multiline_route_parts_for_fallback_rendering():
    record = sample_record("560231")
    record["_geometry"]["shortest"] = MultiLineString(
        [
            LineString([(28000.0, 35000.0), (28010.0, 35000.0)]),
            LineString([(28100.0, 35000.0), (28110.0, 35000.0)]),
        ]
    )

    output = geom_record(record)

    assert output is not None
    assert len(output["shortest_parts"]) == 2


def test_station_code_rows_from_xls_bytes_parses_official_schema(monkeypatch):
    class FakeSheet:
        nrows = 3
        ncols = 5

        values = [
            [
                "stn_code",
                "mrt_station_english",
                "mrt_station_chinese",
                "mrt_line_english",
                "mrt_line_chinese",
            ],
            ["TE6", "Mayflower", "美华", "Thomson-East Coast Line", "汤申-东海岸线"],
            ["", "", "", "", ""],
        ]

        def cell_value(self, row: int, col: int) -> str:
            return str(self.values[row][col])

    class FakeBook:
        def sheets(self):
            return [FakeSheet()]

    def fake_open_workbook(*, file_contents: bytes):
        assert file_contents == b"xls"
        return FakeBook()

    monkeypatch.setattr("pipeline.export.xlrd.open_workbook", fake_open_workbook)

    assert station_code_rows_from_xls_bytes(b"xls") == [
        {
            "stn_code": "TE6",
            "mrt_station_english": "Mayflower",
            "mrt_station_chinese": "美华",
            "mrt_line_english": "Thomson-East Coast Line",
            "mrt_line_chinese": "汤申-东海岸线",
        }
    ]


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
    bus_services_payload = {
        "value": [
            {
                "ServiceNo": "262",
                "Direction": 1,
                "Operator": "SBST",
                "Category": "TRUNK",
                "AM_Peak_Freq": "06-08",
                "PM_Peak_Freq": "07-09",
            }
        ]
    }
    bus_routes_payload = {
        "value": [
            {
                "ServiceNo": "262",
                "Direction": 1,
                "BusStopCode": "55089",
                "WD_FirstBus": "0530",
                "WD_LastBus": "0045",
                "SAT_FirstBus": "0535",
                "SAT_LastBus": "0040",
                "SUN_FirstBus": "0545",
                "SUN_LastBus": "0030",
            }
        ]
    }
    train_station_codes_payload = [
        {
            "stn_code": "TE6",
            "mrt_station_english": "Mayflower",
            "mrt_station_chinese": "美华",
            "mrt_line_english": "Thomson-East Coast Line",
            "mrt_line_chinese": "汤申-东海岸线",
        }
    ]

    collection = build_transit_poi_collection(
        mrt_geojson,
        bus_payload,
        {"source_hashes": {"mrt_lrt_exits": "a" * 64, "bus_stops": "b" * 64}},
        bus_services_payload,
        bus_routes_payload,
        train_station_codes_payload,
    )

    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 3
    kinds = {feature["properties"]["kind"] for feature in collection["features"]}
    assert kinds == {"mrt_station", "mrt_exit", "bus_stop"}
    mrt = next(
        feature for feature in collection["features"] if feature["properties"]["kind"] == "mrt_exit"
    )
    station = next(
        feature
        for feature in collection["features"]
        if feature["properties"]["kind"] == "mrt_station"
    )
    bus = next(
        feature for feature in collection["features"] if feature["properties"]["kind"] == "bus_stop"
    )
    assert mrt["properties"]["name"] == "MAYFLOWER MRT STATION Exit 5"
    assert mrt["properties"]["system"] == "MRT"
    assert mrt["properties"]["station_codes"] == "TE6"
    assert mrt["properties"]["lines"] == "Thomson-East Coast Line"
    assert station["properties"]["label"] == "MAYFLOWER"
    assert station["properties"]["exit_count"] == 1
    assert station["properties"]["station_codes"] == "TE6"
    assert station["properties"]["lines"] == "Thomson-East Coast Line"
    assert bus["properties"]["code"] == "55089"
    assert bus["properties"]["services"] == "262"
    assert bus["properties"]["service_count"] == 1
    assert bus["properties"]["weekday_first_bus"] == "05:30"
    assert bus["properties"]["weekday_last_bus"] == "00:45"
    assert bus["properties"]["am_peak_best_min"] == 7
    assert bus["properties"]["pm_peak_best_min"] == 8


def test_refresh_transit_manifest_updates_only_transit_block(tmp_path: Path):
    write_json(
        tmp_path / "manifest.json",
        {
            "data_as_of": "2026-08-01T00:00:00+00:00",
            "transit": {"source_hashes": {"old": "hash"}},
        },
    )

    updated = refresh_transit_manifest(
        tmp_path,
        {
            "path": "transit/pois.json",
            "feature_count": 3,
            "counts": {"bus_stop": 1, "mrt_exit": 1, "mrt_station": 1},
            "source_hashes": {"train_station_codes": "a" * 64},
        },
    )

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert updated is True
    assert manifest["data_as_of"] == "2026-08-01T00:00:00+00:00"
    assert manifest["transit"]["source_hashes"] == {"train_station_codes": "a" * 64}
    assert manifest["transit"]["refreshed_at"]


def test_refresh_score_provenance_manifest_updates_from_score_shards(tmp_path: Path):
    export_static_artifacts([sample_record("123456"), sample_record("654321")], output_dir=tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance"].pop("source_hashes", None)
    manifest["provenance"].pop("subscore_status", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = refresh_score_provenance_manifest(tmp_path)

    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["manifest_updated"] is True
    assert report["record_count"] == 2
    assert report["source_hash_count"] == 1
    assert refreshed["provenance"]["source_hashes"]["osm_extract"] == "a" * 64
    assert (
        refreshed["provenance"]["subscore_status"]["heat"]
        == "provisional_covered_plus_nparks_shade_proxy_heat_only"
    )
    assert refreshed["provenance"]["score_provenance_refreshed_at"]


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
    records = [
        sample_record("123456"),
        sample_record("123457"),
        sample_record("654321"),
        sample_record("654322"),
    ]
    records[0]["_origin"]["lat"] = 1.30
    records[1]["_origin"]["lat"] = 1.3001
    records[2]["_origin"]["lat"] = 1.32
    records[3]["_origin"]["lat"] = 1.3201
    threshold = json_size([geom_record(records[0])]) + 100

    report = export_static_artifacts(
        records,
        output_dir=tmp_path,
        geom_promotion_threshold_bytes=threshold,
    )
    ok, validation = validate_static_artifacts(tmp_path)

    assert ok, validation
    assert report["geom_shard_count"] == 2
    geom_records = []
    for path in sorted((tmp_path / "geom" / "h3").glob("shared-child_PART_*.json")):
        geom_records.extend(json.loads(path.read_text()))
    assert sorted(record["postal"] for record in geom_records) == [
        "123456",
        "123457",
        "654321",
        "654322",
    ]


def test_export_recursively_promotes_large_geom_shards(tmp_path: Path, monkeypatch):
    def fake_latlng_to_cell(lat: float, _lon: float, resolution: int) -> str:
        if resolution == 8:
            return "parent-cell"
        if resolution == 9:
            return "still-too-large"
        return "deep-a" if lat < 1.31 else "deep-b"

    monkeypatch.setattr("pipeline.export.h3.latlng_to_cell", fake_latlng_to_cell)
    records = [sample_record("123456"), sample_record("654321")]
    records[0]["_origin"]["lat"] = 1.30
    records[1]["_origin"]["lat"] = 1.32
    threshold = json_size([geom_record(records[0])]) + 100

    report = export_static_artifacts(
        records,
        output_dir=tmp_path,
        geom_promotion_threshold_bytes=threshold,
        geom_max_promotion_resolution=10,
    )
    ok, validation = validate_static_artifacts(tmp_path)

    assert ok, validation
    assert report["geom_shard_count"] == 2
    geom_index = json.loads((tmp_path / "geom" / "index.json").read_text())
    assert geom_index["parent-cell"] == ["deep-a", "deep-b"]


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
