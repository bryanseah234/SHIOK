import gzip
import json

from scripts.partial_resnap_rescore import read_json


def test_partial_resnap_rescore_reads_gzipped_bundle_artifact(tmp_path):
    path = tmp_path / "index.json"
    with gzip.open(path.with_name("index.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"ANG_MO_KIO": ["560234"]}, f)

    assert read_json(path) == {"ANG_MO_KIO": ["560234"]}
