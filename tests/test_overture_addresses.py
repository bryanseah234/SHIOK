from pathlib import Path

import pyarrow.parquet as pq

from pipeline.overture_addresses import (
    archive_overture_postcode_rows,
    compare_coordinate_deltas,
    compare_postcode_sets,
    normalize_postcode,
    wgs84_to_xy_transformer,
)


def test_normalize_postcode_accepts_only_six_digits():
    assert normalize_postcode("018895") == "018895"
    assert normalize_postcode(" 718788 ") == "718788"
    assert normalize_postcode("71878") is None
    assert normalize_postcode("718788-0001") is None
    assert normalize_postcode(None) is None


def test_compare_postcode_sets_reports_overlap_and_samples():
    report = compare_postcode_sets(
        overture_postcodes={"100000", "200000", "300000"},
        current_postcodes={"200000", "300000", "400000"},
    )

    assert report == {
        "overture_unique_postcodes": 3,
        "current_unique_postcodes": 3,
        "intersection": 2,
        "new_from_overture": 1,
        "current_missing_from_overture": 1,
        "sample_new_from_overture": ["100000"],
        "sample_current_missing_from_overture": ["400000"],
    }


def test_compare_coordinate_deltas_reports_current_overlap():
    x, y = wgs84_to_xy_transformer().transform(103.8, 1.3)
    report = compare_coordinate_deltas(
        overture_rows=[
            {
                "postcode": "018895",
                "representative_lon": 103.8,
                "representative_lat": 1.3,
                "source_dataset": "OpenAddresses/Singapore Land Authority",
                "address_rows": 1,
            },
            {
                "postcode": "999999",
                "representative_lon": 103.9,
                "representative_lat": 1.4,
            },
        ],
        current_coordinates={
            "018895": {
                "x": x,
                "y": y,
                "coordinate_source": "current",
            }
        },
    )

    assert report["overlap_with_current_coordinates"] == 1
    assert report["delta_m"]["count"] == 1
    assert report["delta_m"]["p50"] < 0.1
    assert report["within_10m"] == 1
    assert report["over_100m"] == 0
    assert report["over_250m"] == 0
    assert report["over_1000m"] == 0
    assert report["largest_deltas"][0]["postcode"] == "018895"


def test_archive_overture_postcode_rows_writes_hashed_parquet(tmp_path: Path):
    rows = [
        {
            "postcode": "018895",
            "address_rows": 2,
            "source_dataset": "OpenAddresses/Singapore Land Authority",
            "representative_lon": 103.8,
            "representative_lat": 1.3,
            "min_lon": 103.8,
            "min_lat": 1.3,
            "max_lon": 103.8,
            "max_lat": 1.3,
        }
    ]

    archive = archive_overture_postcode_rows(rows, raw_dir=tmp_path)

    path = Path(archive["path"])
    assert path.is_file()
    assert path.parent.name == archive["sha256"]
    table = pq.read_table(path)
    assert table.column("postcode").to_pylist() == ["018895"]
    assert table.column("representative_lon").to_pylist() == [103.8]
