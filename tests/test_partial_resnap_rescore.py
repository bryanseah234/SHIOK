import gzip
import json

from pipeline.scoring import NO_TRANSIT_IN_RANGE
from scripts.partial_resnap_rescore import read_json, select_no_transit_postals


def test_partial_resnap_rescore_reads_gzipped_bundle_artifact(tmp_path):
    path = tmp_path / "index.json"
    with gzip.open(path.with_name("index.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"ANG_MO_KIO": ["560234"]}, f)

    assert read_json(path) == {"ANG_MO_KIO": ["560234"]}


def test_select_no_transit_postals_can_filter_to_direct_bus_candidates():
    records = {
        "100001": {
            "postal": "100001",
            "state": NO_TRANSIT_IN_RANGE,
            "_area": "AREA_A",
            "provenance": {"transit_node_set": {"bus_stop_candidates_direct": 0}},
        },
        "100002": {
            "postal": "100002",
            "state": NO_TRANSIT_IN_RANGE,
            "_area": "AREA_A",
            "provenance": {"transit_node_set": {"bus_stop_candidates_direct": 2}},
        },
        "100003": {
            "postal": "100003",
            "state": "SCORED",
            "_area": "AREA_A",
            "provenance": {"transit_node_set": {"bus_stop_candidates_direct": 3}},
        },
    }

    assert select_no_transit_postals(
        records,
        areas=["AREA_A"],
        per_area=10,
        extra_postals=[],
        limit=10,
        only_with_direct_bus=True,
    ) == ["100002"]
