from pathlib import Path

import pyarrow.parquet as pq

from pipeline.overture_addresses import (
    archive_overture_postcode_rows,
    compare_postcode_sets,
    normalize_postcode,
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
