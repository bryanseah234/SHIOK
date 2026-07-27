import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.batch_plan import build_batch_plan, format_duration


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_universe(path: Path, rows: int = 2) -> None:
    table = pa.table(
        {
            "postal_code": [f"{index:06d}" for index in range(1, rows + 1)],
            "status": ["READY_TO_SCORE"] * rows,
        }
    )
    pq.write_table(table, path)


def write_params(path: Path, delay: float = 2.0) -> None:
    path.write_text(f"onemap:\n  client_delay_sec: {delay}\n", encoding="utf-8")


def write_island_qa(path: Path) -> None:
    payload = {
        "nodes": 100,
        "edges": 120,
        "mean_edge_length_m": 18.0,
        "connected_components_count": 1,
        "top_5_component_sizes": [100],
        "residual_components_gt_50_osm_only": [],
        "residual_components_gt_50_final": [],
        "real_disconnection_count_osm_only": 0,
        "real_disconnection_count_final": 0,
        "flags": [],
    }
    write_json(path, payload)


def test_format_duration_compacts_days_hours_minutes_seconds():
    assert format_duration(0) == "0s"
    assert format_duration(972) == "16m 12s"
    assert format_duration(17256) == "4h 47m 36s"
    assert format_duration(90061) == "1d 1h 1m 1s"


def test_batch_plan_reports_bounded_geocoding_and_keeps_gate_closed(tmp_path: Path):
    summary_path = tmp_path / "summary.json"
    universe_path = tmp_path / "universe.parquet"
    params_path = tmp_path / "params.yaml"
    qa_path = tmp_path / "conflation_qa_island.json"
    debug_path = tmp_path / "island_debug.geojson"
    write_json(
        summary_path,
        {
            "generated_at": "2026-07-27T10:00:00+00:00",
            "mode": "candidate_full_registered",
            "total_unique_postals": 2,
            "ready_to_score": 1,
            "needs_geocode": 1,
            "source_stats": [
                {
                    "source_key": "postal_universe_onemap_2020",
                    "raw_records": 2,
                    "valid_unique_postals": 2,
                    "records_with_coordinates": 1,
                    "sha256": "abc",
                    "path": "raw/example",
                    "url": "https://example.test/source",
                }
            ],
            "source_only_counts": {"postal_universe_onemap_2020": 1},
            "warnings": [
                "postal_universe_onemap_2020 is a third-party OneMap-derived 2020 dump and must be human-approved before full-batch use"
            ],
        },
    )
    write_universe(universe_path, rows=2)
    write_params(params_path, delay=2.0)
    write_island_qa(qa_path)
    debug_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    ok, report = build_batch_plan(
        mode="candidate_full_registered",
        summary_path=summary_path,
        universe_path=universe_path,
        params_path=params_path,
        qa_path=qa_path,
        debug_path=debug_path,
    )

    assert ok, report
    assert report["postal_universe"]["total_unique_postals"] == 2
    assert report["bounded_geocoding"]["requests"] == 1
    assert report["bounded_geocoding"]["minimum_wall_clock_seconds"] == 2.0
    assert report["bounded_geocoding"]["will_bruteforce"] is False
    assert report["checkpoint_gates"]["island_network_qa_ok"] is True
    assert report["checkpoint_gates"]["requires_human_approval_for_universe"] is True
    assert report["checkpoint_gates"]["full_batch_allowed_now"] is False


def test_batch_plan_reports_missing_island_qa_as_blocker_not_artifact_error(tmp_path: Path):
    summary_path = tmp_path / "summary.json"
    universe_path = tmp_path / "universe.parquet"
    params_path = tmp_path / "params.yaml"
    write_json(
        summary_path,
        {
            "mode": "official_current",
            "total_unique_postals": 2,
            "ready_to_score": 2,
            "needs_geocode": 0,
            "source_stats": [],
            "source_only_counts": {},
            "warnings": [],
        },
    )
    write_universe(universe_path, rows=2)
    write_params(params_path, delay=2.0)

    ok, report = build_batch_plan(
        mode="official_current",
        summary_path=summary_path,
        universe_path=universe_path,
        params_path=params_path,
        qa_path=tmp_path / "missing_qa.json",
        debug_path=tmp_path / "missing_debug.geojson",
    )

    assert ok, report
    assert report["errors"] == []
    assert report["checkpoint_gates"]["island_network_qa_ok"] is False
    assert "island-wide network QA is not green" in report["checkpoint_gates"]["blockers"]


def test_batch_plan_rejects_missing_or_mismatched_universe_artifact(tmp_path: Path):
    summary_path = tmp_path / "summary.json"
    universe_path = tmp_path / "universe.parquet"
    params_path = tmp_path / "params.yaml"
    write_json(
        summary_path,
        {
            "mode": "official_current",
            "total_unique_postals": 3,
            "ready_to_score": 3,
            "needs_geocode": 0,
            "source_stats": [],
            "source_only_counts": {},
            "warnings": [],
        },
    )
    write_universe(universe_path, rows=2)
    write_params(params_path, delay=2.0)

    ok, report = build_batch_plan(
        mode="official_current",
        summary_path=summary_path,
        universe_path=universe_path,
        params_path=params_path,
        qa_path=tmp_path / "missing_qa.json",
        debug_path=tmp_path / "missing_debug.geojson",
    )

    assert not ok
    assert "postal universe row mismatch: parquet has 2, summary has 3" in report["errors"]
