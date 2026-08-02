import gzip
import json

from pipeline.export import encode_polyline, json_size
from scripts.targeted_bundle_refresh import (
    load_geom_shard,
    postals_from_file,
    rebalance_geom_parents,
    read_json,
    split_oversized_geom_shards,
    unique_postals,
    write_json,
)


def test_targeted_bundle_refresh_reads_gzipped_artifact(tmp_path):
    path = tmp_path / "index.json"
    with gzip.open(path.with_name("index.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"A": ["123456"]}, f)

    assert read_json(path) == {"A": ["123456"]}


def test_targeted_bundle_refresh_updates_existing_gzip_sibling(tmp_path):
    path = tmp_path / "scores.json"
    with gzip.open(path.with_name("scores.json.gz"), "wt", encoding="utf-8") as f:
        json.dump([{"postal": "000000"}], f)

    write_json(path, [{"postal": "123456"}])

    assert read_json(path) == [{"postal": "123456"}]
    with gzip.open(path.with_name("scores.json.gz"), "rt", encoding="utf-8") as f:
        assert json.load(f) == [{"postal": "123456"}]


def test_targeted_bundle_refresh_loads_gzipped_geom_shard(tmp_path):
    shard_dir = tmp_path / "geom" / "h3"
    shard_dir.mkdir(parents=True)
    with gzip.open(shard_dir / "abc.json.gz", "wt", encoding="utf-8") as f:
        json.dump([{"postal": "123456"}], f)

    assert load_geom_shard(tmp_path, "abc") == [{"postal": "123456"}]


def test_targeted_bundle_refresh_reads_postal_file(tmp_path):
    path = tmp_path / "postals.txt"
    path.write_text("123\n# comment\n560234\n123\n\n", encoding="utf-8")

    assert postals_from_file(path) == ["000123", "560234", "000123"]
    assert unique_postals(postals_from_file(path)) == ["000123", "560234"]


def test_targeted_bundle_refresh_rebalances_oversized_geom_parent(tmp_path, monkeypatch):
    def fake_latlng_to_cell(lat: float, _lon: float, resolution: int) -> str:
        if resolution == 9:
            return "child-a" if lat < 1.31 else "child-b"
        return "parent-cell"

    monkeypatch.setattr("pipeline.export.h3.latlng_to_cell", fake_latlng_to_cell)
    geom_dir = tmp_path / "geom" / "h3"
    geom_dir.mkdir(parents=True)
    first = {
        "postal": "123456",
        "shortest": encode_polyline([(1.30, 103.80), (1.3001, 103.8001)]),
        "sheltered": encode_polyline([(1.30, 103.80), (1.3001, 103.8001)]),
        "exposure_gaps": [],
    }
    second = {
        "postal": "654321",
        "shortest": encode_polyline([(1.32, 103.82), (1.3201, 103.8201)]),
        "sheltered": encode_polyline([(1.32, 103.82), (1.3201, 103.8201)]),
        "exposure_gaps": [],
    }
    write_json(tmp_path / "geom" / "index.json", {"parent-cell": []})
    write_json(
        tmp_path / "geom" / "postal-index.json",
        {"123456": "parent-cell", "654321": "parent-cell"},
    )
    write_json(geom_dir / "parent-cell.json", [first, second])

    max_bytes = json_size([first]) + 20
    geom_index = {"parent-cell": []}
    postal_index = {"123456": "parent-cell", "654321": "parent-cell"}
    written = rebalance_geom_parents(
        tmp_path,
        geom_index,
        postal_index,
        {"parent-cell"},
        {},
        {},
        max_bytes=max_bytes,
        max_resolution=9,
    )

    assert sorted(written) == ["child-a", "child-b"]
    assert geom_index == {"parent-cell": ["child-a", "child-b"]}
    assert postal_index == {"123456": "child-a", "654321": "child-b"}
    assert not (geom_dir / "parent-cell.json").exists()
    assert read_json(tmp_path / "geom" / "h3" / "child-a.json") == [first]
    assert read_json(tmp_path / "geom" / "h3" / "child-b.json") == [second]


def test_targeted_bundle_refresh_splits_oversized_shared_geom_shard(tmp_path):
    geom_dir = tmp_path / "geom" / "h3"
    geom_dir.mkdir(parents=True)
    first = {
        "postal": "123456",
        "shortest": encode_polyline([(1.30, 103.80), (1.3001, 103.8001)]),
        "sheltered": encode_polyline([(1.30, 103.80), (1.3001, 103.8001)]),
        "exposure_gaps": [],
        "payload": "x" * 100,
    }
    second = {
        "postal": "654321",
        "shortest": encode_polyline([(1.32, 103.82), (1.3201, 103.8201)]),
        "sheltered": encode_polyline([(1.32, 103.82), (1.3201, 103.8201)]),
        "exposure_gaps": [],
        "payload": "y" * 100,
    }
    geom_index = {
        "parent-a": ["shared-child", "other-child"],
        "parent-b": ["shared-child"],
    }
    postal_index = {"123456": "shared-child", "654321": "shared-child"}
    write_json(geom_dir / "shared-child.json", [first, second])
    write_json(geom_dir / "other-child.json", [])

    max_bytes = json_size([first]) + 20
    report = split_oversized_geom_shards(
        tmp_path,
        geom_index,
        postal_index,
        {"shared-child"},
        max_bytes=max_bytes,
    )

    assert report["geom_oversized_shard_split_count"] == 1
    assert not (geom_dir / "shared-child.json").exists()
    assert geom_index == {
        "parent-a": ["other-child", "shared-child_PART_001", "shared-child_PART_002"],
        "parent-b": ["shared-child_PART_001", "shared-child_PART_002"],
    }
    assert postal_index == {
        "123456": "shared-child_PART_001",
        "654321": "shared-child_PART_002",
    }
