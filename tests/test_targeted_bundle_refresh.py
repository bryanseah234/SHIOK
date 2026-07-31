import gzip
import json

from scripts.targeted_bundle_refresh import read_json, write_json


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
