import csv
import gzip
import json
from pathlib import Path

from pipeline.postal_universe import (
    ACRA_SOURCE_KEY,
    ONEMAP_2020_SOURCE_KEY,
    OTHER_UEN_SOURCE_KEY,
    SLA_DWELLING_SOURCE_KEY,
    SourceRow,
    iter_acra_rows,
    iter_onemap_2020_rows,
    iter_other_uen_rows,
    iter_sla_dwelling_rows,
    merge_source_rows,
    normalize_postal_code,
)


def test_normalize_postal_code_pads_leading_zeroes_and_rejects_invalid_values():
    assert normalize_postal_code("18906") == "018906"
    assert normalize_postal_code(310071) == "310071"
    assert normalize_postal_code("000000") is None
    assert normalize_postal_code("1234567") is None
    assert normalize_postal_code("ABC123") is None
    assert normalize_postal_code("") is None


def test_iter_onemap_2020_rows_normalizes_postals_and_keeps_coordinates(tmp_path: Path):
    path = tmp_path / "singpostcode.json.gz"
    payload = [
        {
            "POSTAL": "18906",
            "LATITUDE": "1.275804635",
            "LONGITUDE": "103.849615",
            "ADDRESS": "1 STRAITS BOULEVARD SINGAPORE 018906",
            "BUILDING": "SINGAPORE CHINESE CULTURAL CENTRE",
            "ROAD_NAME": "STRAITS BOULEVARD",
        },
        {
            "POSTAL": "bad",
            "LATITUDE": "1.275804635",
            "LONGITUDE": "103.849615",
        },
    ]
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f)

    rows, stats = iter_onemap_2020_rows(path)

    assert stats.source_key == ONEMAP_2020_SOURCE_KEY
    assert stats.raw_records == 2
    assert stats.valid_unique_postals == 1
    assert stats.records_with_coordinates == 1
    assert rows[0].postal_code == "018906"
    assert rows[0].lat == 1.275804635
    assert rows[0].lon == 103.849615


def test_iter_acra_rows_filters_registered_policy(tmp_path: Path):
    path = tmp_path / "acra.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["uen_status_desc", "reg_street_name", "reg_postal_code"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "uen_status_desc": "Registered",
                "reg_street_name": "BENCOOLEN STREET",
                "reg_postal_code": "189648",
            }
        )
        writer.writerow(
            {
                "uen_status_desc": "Deregistered",
                "reg_street_name": "SEGAR ROAD",
                "reg_postal_code": "670481",
            }
        )

    registered_rows, registered_stats = iter_acra_rows(path, "registered")
    all_rows, all_stats = iter_acra_rows(path, "all")

    assert registered_stats.source_key == ACRA_SOURCE_KEY
    assert [row.postal_code for row in registered_rows] == ["189648"]
    assert registered_stats.valid_unique_postals == 1
    assert {row.postal_code for row in all_rows} == {"189648", "670481"}
    assert all_stats.valid_unique_postals == 2


def test_iter_other_uen_rows_filters_registered_policy(tmp_path: Path):
    path = tmp_path / "other_uen.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["uen_status_desc", "reg_street_name", "reg_postal_code"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "uen_status_desc": "Registered",
                "reg_street_name": "NORTH BRIDGE ROAD",
                "reg_postal_code": "188778",
            }
        )
        writer.writerow(
            {
                "uen_status_desc": "Deregistered",
                "reg_street_name": "YISHUN AVENUE 2",
                "reg_postal_code": "769098",
            }
        )

    registered_rows, registered_stats = iter_other_uen_rows(path, "registered")
    all_rows, all_stats = iter_other_uen_rows(path, "all")

    assert registered_stats.source_key == OTHER_UEN_SOURCE_KEY
    assert [row.postal_code for row in registered_rows] == ["188778"]
    assert registered_stats.valid_unique_postals == 1
    assert {row.postal_code for row in all_rows} == {"188778", "769098"}
    assert all_stats.valid_unique_postals == 2


def test_iter_sla_dwelling_rows_extracts_postal_coordinates(tmp_path: Path):
    path = tmp_path / "sla_dwelling_information.geojson"
    payload = {
        "type": "FeatureCollection",
        "name": "SLA_DWELLING_INFORMATION_PUB",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [103.870820413, 1.408262367]},
                "properties": {
                    "POSTAL_CODE": "798409",
                    "HOUSE_BLK_NO": "6",
                    "STREET_NAME": "OXFORD STREET",
                    "D_TYPE": "Terrace House",
                    "NO_OF_UNITS": 1,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [103.849615, 1.275804635]},
                "properties": {"POSTAL_CODE": "bad"},
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    rows, stats = iter_sla_dwelling_rows(path)

    assert stats.source_key == SLA_DWELLING_SOURCE_KEY
    assert stats.raw_records == 2
    assert stats.valid_unique_postals == 1
    assert stats.records_with_coordinates == 1
    assert rows[0].postal_code == "798409"
    assert rows[0].source_key == SLA_DWELLING_SOURCE_KEY
    assert rows[0].road_name == "OXFORD STREET"
    assert rows[0].address == "6 OXFORD STREET"


def test_merge_source_rows_prefers_current_coordinates_and_keeps_source_membership():
    rows = [
        SourceRow(
            postal_code="123456",
            source_key=ONEMAP_2020_SOURCE_KEY,
            priority=30,
            lat=1.30,
            lon=103.80,
            x=100.0,
            y=200.0,
            building="OLD",
        ),
        SourceRow(
            postal_code="123456",
            source_key="hdb_existing_building",
            priority=10,
            lat=1.31,
            lon=103.81,
            x=110.0,
            y=210.0,
        ),
        SourceRow(
            postal_code="654321",
            source_key=ACRA_SOURCE_KEY,
            priority=90,
            road_name="NO COORD ROAD",
        ),
    ]

    merged = merge_source_rows(rows)

    assert [record.postal_code for record in merged] == ["123456", "654321"]
    assert merged[0].coordinate_source == "hdb_existing_building"
    assert merged[0].lat == 1.31
    assert merged[0].sources == {ONEMAP_2020_SOURCE_KEY, "hdb_existing_building"}
    assert merged[0].status == "READY_TO_SCORE"
    assert merged[1].status == "NEEDS_GEOCODE"
