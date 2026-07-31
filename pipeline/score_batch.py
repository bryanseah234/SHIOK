from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from pipeline.network_qa import validate_network_qa
from pipeline.scoring import NOT_YET_SCORED
from pipeline.scoring_integration import (
    NETWORK_PATH,
    PROCESSED_DIR,
    PROJECT_ROOT,
    load_manifest,
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


def json_safe_geometry(value: Any) -> Any:
    if value is None:
        return None
    wkt = getattr(value, "wkt", None)
    return str(wkt) if isinstance(wkt, str) else value


def json_safe_score_record(record: dict[str, Any]) -> dict[str, Any]:
    safe = dict(record)
    geometry_payload = safe.get("_geometry")
    if not isinstance(geometry_payload, dict):
        return safe

    safe_geometry = dict(geometry_payload)
    safe_geometry["shortest"] = json_safe_geometry(safe_geometry.get("shortest"))
    safe_geometry["sheltered"] = json_safe_geometry(safe_geometry.get("sheltered"))
    for edges_key in ["shortest_path_edges", "sheltered_path_edges", "exposure_gap_edges"]:
        edges = []
        for edge in safe_geometry.get(edges_key, []):
            if not isinstance(edge, dict):
                continue
            safe_edge = dict(edge)
            safe_edge["geometry"] = json_safe_geometry(safe_edge.get("geometry"))
            edges.append(safe_edge)
        safe_geometry[edges_key] = edges
    safe["_geometry"] = safe_geometry
    geometry_options = safe.get("_geometry_options")
    if isinstance(geometry_options, dict):
        safe_options: dict[str, Any] = {}
        for key, option_geometry in geometry_options.items():
            if not isinstance(option_geometry, dict):
                continue
            option_safe = dict(option_geometry)
            option_safe["shortest"] = json_safe_geometry(option_safe.get("shortest"))
            option_safe["sheltered"] = json_safe_geometry(option_safe.get("sheltered"))
            for edges_key in ["shortest_path_edges", "sheltered_path_edges", "exposure_gap_edges"]:
                edges = []
                for edge in option_safe.get(edges_key, []):
                    if not isinstance(edge, dict):
                        continue
                    safe_edge = dict(edge)
                    safe_edge["geometry"] = json_safe_geometry(safe_edge.get("geometry"))
                    edges.append(safe_edge)
                option_safe[edges_key] = edges
            safe_options[str(key)] = option_safe
        safe["_geometry_options"] = safe_options
    return safe


def source_list(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "tolist") and not isinstance(value, str):
        value = value.tolist()
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def load_postal_universe_batch_rows(
    universe_path: Path,
    limit: int | None,
    include_unscored: bool,
) -> pd.DataFrame:
    if not universe_path.is_file():
        raise FileNotFoundError(f"postal universe not found: {universe_path}")
    rows = pd.read_parquet(universe_path)
    rows["postal_code"] = rows["postal_code"].astype(str).str.zfill(6)
    if not include_unscored:
        rows = rows[rows["status"] == "READY_TO_SCORE"].copy()
    rows = rows.sort_values("postal_code", kind="stable").reset_index(drop=True)
    if limit is not None:
        rows = rows.head(int(limit)).copy()
    return rows


def ready_rows_to_gdf(rows: pd.DataFrame) -> gpd.GeoDataFrame:
    ready = rows[
        (rows["status"] == "READY_TO_SCORE") & rows["x"].notna() & rows["y"].notna()
    ].copy()
    return gpd.GeoDataFrame(
        ready,
        geometry=gpd.points_from_xy(ready["x"], ready["y"]),
        crs="EPSG:3414",
    )


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def json_nullable(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    return value


def not_yet_scored_record(
    row: pd.Series,
    postal_universe_path: Path,
    data_as_of: str | None,
) -> dict[str, Any]:
    source_status = str(row.get("status") or NOT_YET_SCORED)
    return {
        "postal": str(row["postal_code"]),
        "state": NOT_YET_SCORED,
        "total": None,
        "subscores": None,
        "best_node": None,
        "paths": None,
        "exposure_gaps": None,
        "data_as_of": data_as_of,
        "provenance": {
            "postal_universe": display_path(postal_universe_path),
            "source_status": source_status,
            "coordinate_source": json_nullable(row.get("coordinate_source")),
            "sources": source_list(row.get("sources")),
            "reason": (
                "missing_coordinates_after_bounded_geocode"
                if source_status == "NEEDS_GEOCODE"
                else f"unscorable_source_status:{source_status}"
            ),
        },
    }


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
    postal_rows = load_postal_universe_batch_rows(
        postal_universe_path,
        requested_limit,
        include_unscored=full_batch,
    )

    chunks = chunk_slices(len(postal_rows), chunk_size)
    report: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "full_batch": full_batch,
        "resume": resume,
        "postal_universe": str(postal_universe_path),
        "network": str(network_path),
        "output_dir": str(output_dir),
        "selected_postals": len(postal_rows),
        "ready_postals_selected": int((postal_rows["status"] == "READY_TO_SCORE").sum()),
        "unscored_postals_selected": int((postal_rows["status"] != "READY_TO_SCORE").sum()),
        "chunk_size": chunk_size,
        "chunk_count": len(chunks),
        "chunks_written": 0,
        "chunks_skipped_existing": 0,
        "records_written": 0,
        "not_yet_scored_records_written": 0,
        "generated_at": datetime.now(UTC).isoformat(),
        "island_network_qa": qa_report,
        "errors": [],
        "chunks": [],
    }
    if dry_run:
        return True, report

    context = context_loader(network_path, postal_universe_path)
    data_as_of = load_manifest().get("generated_at")
    for chunk_index, (start, end) in enumerate(chunks, start=1):
        chunk = postal_rows.iloc[start:end].copy()
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

        ready_gdf = ready_rows_to_gdf(chunk)
        scored_records = [
            json_safe_score_record(record)
            for record in score_chunker(ready_gdf, context, include_geometry, None)
        ]
        scored_by_postal = {str(record["postal"]): record for record in scored_records}
        records = []
        for _, row in chunk.iterrows():
            postal = str(row["postal_code"])
            if postal in scored_by_postal:
                records.append(scored_by_postal[postal])
            else:
                records.append(not_yet_scored_record(row, postal_universe_path, data_as_of))
        bytes_written = write_json(path, records)
        report["chunks_written"] += 1
        report["records_written"] += len(records)
        report["not_yet_scored_records_written"] += sum(
            1 for record in records if record.get("state") == NOT_YET_SCORED
        )
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
