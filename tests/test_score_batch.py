from pathlib import Path

import pandas as pd

from pipeline.score_batch import (
    build_score_batch,
    chunk_path,
    chunk_slices,
    read_chunk_postals,
    validate_full_batch_gate,
)


def write_universe(path: Path) -> None:
    pd.DataFrame(
        [
            {"postal_code": "000003", "status": "READY_TO_SCORE", "x": 3.0, "y": 3.0},
            {"postal_code": "000001", "status": "READY_TO_SCORE", "x": 1.0, "y": 1.0},
            {"postal_code": "000002", "status": "NEEDS_GEOCODE", "x": None, "y": None},
            {"postal_code": "000004", "status": "READY_TO_SCORE", "x": 4.0, "y": 4.0},
        ]
    ).to_parquet(path, index=False)


class FakeContext:
    pass


def fake_context_loader(_network_path: Path, _postal_universe_path: Path | None):
    return FakeContext()


def fake_score_chunker(postal_gdf, _context, _include_geometry, _limit):
    return [
        {
            "postal": str(row["postal_code"]),
            "state": "SCORED",
            "total": 50.0,
            "subscores": {
                "access": 50.0,
                "bus": 50.0,
                "rain": 50.0,
                "heat": 50.0,
                "crossing": 50.0,
            },
            "best_node": {"type": "mrt_lrt_exit", "name": "TEST", "routed_m": 100.0},
            "paths": {"shortest_m": 100.0, "sheltered_m": 100.0, "detour_pct": 0.0},
            "exposure_gaps": [],
            "data_as_of": None,
            "provenance": {},
        }
        for _, row in postal_gdf.iterrows()
    ]


def test_chunk_slices_and_path_are_deterministic(tmp_path: Path):
    assert chunk_slices(5, 2) == [(0, 2), (2, 4), (4, 5)]

    path = chunk_path(tmp_path, 2, ["000003", "000004"])

    assert path.name == "chunk_00002_000003_000004.json"


def test_validate_full_batch_gate_blocks_unconfirmed_non_dry_run():
    ok, _qa, errors = validate_full_batch_gate(
        full_batch=True,
        confirm_full_batch=False,
        dry_run=False,
        postal_universe_path=Path("processed/postal_universe_official_current.parquet"),
        network_path=Path("processed/network.parquet"),
    )

    assert not ok
    assert errors == ["full score batch requires --confirm-full-batch after checkpoint approval"]


def test_validate_full_batch_gate_allows_dry_run_without_confirmation():
    ok, _qa, errors = validate_full_batch_gate(
        full_batch=True,
        confirm_full_batch=False,
        dry_run=True,
        postal_universe_path=Path("processed/postal_universe_official_current.parquet"),
        network_path=Path("processed/network.parquet"),
    )

    assert ok
    assert errors == []


def test_score_batch_writes_chunks_and_manifest_then_resumes(tmp_path: Path):
    universe_path = tmp_path / "postal_universe.parquet"
    output_dir = tmp_path / "scores"
    write_universe(universe_path)

    ok, report = build_score_batch(
        postal_universe_path=universe_path,
        output_dir=output_dir,
        limit=3,
        chunk_size=2,
        context_loader=fake_context_loader,
        score_chunker=fake_score_chunker,
    )

    assert ok, report
    assert report["selected_postals"] == 3
    assert report["chunk_count"] == 2
    assert report["chunks_written"] == 2
    assert report["records_written"] == 3
    assert (output_dir / "batch_manifest.json").is_file()
    assert read_chunk_postals(Path(report["chunks"][0]["path"])) == ["000001", "000003"]

    ok, resumed = build_score_batch(
        postal_universe_path=universe_path,
        output_dir=output_dir,
        limit=3,
        chunk_size=2,
        context_loader=fake_context_loader,
        score_chunker=fake_score_chunker,
    )

    assert ok, resumed
    assert resumed["chunks_written"] == 0
    assert resumed["chunks_skipped_existing"] == 2
    assert resumed["records_written"] == 3


def test_score_batch_dry_run_does_not_create_outputs(tmp_path: Path):
    universe_path = tmp_path / "postal_universe.parquet"
    output_dir = tmp_path / "scores"
    write_universe(universe_path)

    ok, report = build_score_batch(
        postal_universe_path=universe_path,
        output_dir=output_dir,
        limit=2,
        chunk_size=1,
        dry_run=True,
        context_loader=fake_context_loader,
        score_chunker=fake_score_chunker,
    )

    assert ok, report
    assert report["dry_run"] is True
    assert report["chunk_count"] == 2
    assert not output_dir.exists()
