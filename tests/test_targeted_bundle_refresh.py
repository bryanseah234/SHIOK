import gzip
import json

from scripts.targeted_bundle_refresh import (
    load_geom_shard,
    postals_from_file,
    read_json,
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
