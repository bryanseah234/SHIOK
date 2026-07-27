from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import geopandas as gpd

from pipeline.network_qa import validate_network_qa
from pipeline.scoring_integration import (
    NETWORK_PATH,
    PROJECT_ROOT,
    PROCESSED_DIR,
    load_postal_universe_points,
    load_scoring_context,
    score_postal_gdf,
)

DEFAULT_OUTPUT_DIR = PROCESSED_DIR / "score_batches"
DEFAULT_ISLAND_NETWORK_PATH = PROCESSED_DIR / "network_island.parquet"
DEFAULT_ISLAND_QA_PATH = PROJECT_ROOT / "qa" / "conflation_qa_island.json"
DEFAULT_ISLAND_DEBUG_PATH = PROJECT_ROOT / "qa" / "island_debug.geojson"
ScoreChunker = Callable[[gpd.GeoDataFrame, Any, bool, int | None], list[dict[str, Any]]]


def chunk_slices(total: int, chunk_size: int) -> list[tuple[int, int]]:
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    return [(start, min(start + chunk_size, total)) for start in range(0, total, chunk_size)]


def chunk_path(output_dir: Path, chunk_index: int, postals: list[str]) -> Path:
    first = postals[0] if postals else "empty"
    last = postals[-1] if postals else "empty"
    return output_dir / "chunks" / f"chunk_{chunk_index:05d}_{first}_{last}.json"


def read_chunk_postals(path: Path) -> list[str] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload: Any = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list):
        return None
    postals = []
    for record in payload:
        if not isinstance(record, dict) or "postal" not in record:
            return None
        postals.append(str(record["postal"]))
    return postals


def write_json(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(content)
    return len(content)


def validate_full_batch_gate(
    *,
    full_batch: bool,
    confirm_full_batch: bool,
    dry_run: bool,
    postal_universe_path: Path | None,
    network_path: Path,
    qa_path: Path = DEFAULT_ISLAND_QA_PATH,
    debug_path: Path = DEFAULT_ISLAND_DEBUG_PATH,
) -> tuple[bool, dict[str, Any], list[str]]:
    qa_ok, qa_summary = validate_network_qa(qa_path, debug_path)
    errors: list[str] = []
    if full_batch and not dry_run:
        if not confirm_full_batch:
            errors.append(
                "full score batch requires --confirm-full-batch after checkpoint approval"
            )
        if postal_universe_path is None:
            errors.append("--full-batch requires --postal-universe")
        if network_path.name == DEFAULT_ISLAND_NETWORK_PATH.name and not qa_ok:
            errors.append("full island score batch requires green island network QA")

    return not errors, {"ok": qa_ok, "summary": qa_summary}, errors


def build_score_batch(
    *,
    postal_universe_path: Path,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    network_path: Path = NETWORK_PATH,
    limit: int | None = 5,
    chunk_size: int = 500,
    include_geometry: bool = True,
    full_batch: bool = False,
    confirm_full_batch: bool = False,
    dry_run: bool = False,
    resume: bool = True,
    context_loader: Callable[[Path, Path | None], Any] = load_scoring_context,
    score_chunker: ScoreChunker = score_postal_gdf,
) -> tuple[bool, dict[str, Any]]:
    if limit is not None and limit < 0:
        return False, {"ok": False, "errors": ["limit must be >= 0"]}
    if chunk_size <= 0:
        return False, {"ok": False, "errors": ["chunk_size must be positive"]}

    gate_ok, qa_report, gate_errors = validate_full_batch_gate(
        full_batch=full_batch,
        confirm_full_batch=confirm_full_batch,
        dry_run=dry_run,
        postal_universe_path=postal_universe_path,
        network_path=network_path,
    )
    if not gate_ok:
        return False, {"ok": False, "errors": gate_errors, "island_network_qa": qa_report}

    requested_limit = None if full_batch else limit
    postal_gdf = load_postal_universe_points(
        postal_universe_path,
        limit=requested_limit,
    ).sort_values("postal_code", kind="stable")
    postal_gdf = postal_gdf.reset_index(drop=True)

    chunks = chunk_slices(len(postal_gdf), chunk_size)
    report: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "full_batch": full_batch,
        "resume": resume,
        "postal_universe": str(postal_universe_path),
        "network": str(network_path),
        "output_dir": str(output_dir),
        "selected_postals": int(len(postal_gdf)),
        "chunk_size": chunk_size,
        "chunk_count": len(chunks),
        "chunks_written": 0,
        "chunks_skipped_existing": 0,
        "records_written": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "island_network_qa": qa_report,
        "errors": [],
        "chunks": [],
    }
    if dry_run:
        return True, report

    context = context_loader(network_path, postal_universe_path)
    for chunk_index, (start, end) in enumerate(chunks, start=1):
        chunk = postal_gdf.iloc[start:end].copy()
        postals = [str(item) for item in chunk["postal_code"].tolist()]
        path = chunk_path(output_dir, chunk_index, postals)
        expected_postals = postals
        if resume and path.is_file() and read_chunk_postals(path) == expected_postals:
            report["chunks_skipped_existing"] += 1
            report["records_written"] += len(expected_postals)
            report["chunks"].append(
                {
                    "index": chunk_index,
                    "path": str(path),
                    "records": len(expected_postals),
                    "status": "skipped_existing",
                }
            )
            continue

        records = score_chunker(chunk, context, include_geometry, None)
        bytes_written = write_json(path, records)
        report["chunks_written"] += 1
        report["records_written"] += len(records)
        report["chunks"].append(
            {
                "index": chunk_index,
                "path": str(path),
                "records": len(records),
                "bytes": bytes_written,
                "status": "written",
            }
        )

    manifest_path = output_dir / "batch_manifest.json"
    report["manifest_path"] = str(manifest_path)
    write_json(manifest_path, report)
    return True, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a resumable postal scoring batch.")
    parser.add_argument("--postal-universe", type=Path, required=True)
    parser.add_argument("--network", type=Path, default=NETWORK_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--no-geometry", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--full-batch",
        action="store_true",
        help="Score all eligible rows from --postal-universe; requires --confirm-full-batch.",
    )
    parser.add_argument(
        "--confirm-full-batch",
        action="store_true",
        help="Required with --full-batch after human checkpoint approval.",
    )
    args = parser.parse_args()

    ok, report = build_score_batch(
        postal_universe_path=args.postal_universe,
        output_dir=args.output_dir,
        network_path=args.network,
        limit=None if args.full_batch else args.limit,
        chunk_size=args.chunk_size,
        include_geometry=not args.no_geometry,
        full_batch=bool(args.full_batch),
        confirm_full_batch=bool(args.confirm_full_batch),
        dry_run=bool(args.dry_run),
        resume=not args.no_resume,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
