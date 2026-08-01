from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from pipeline.network_qa import validate_network_qa

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "processed"
QA_DIR = PROJECT_ROOT / "qa"
PARAMS_PATH = PROJECT_ROOT / "pipeline" / "config" / "params.yaml"
UNIVERSE_MODES = ("official_current", "candidate_full_registered", "candidate_full_all")
DEFAULT_ONEMAP_DELAY_SEC = 2.0
THIRD_PARTY_ONEMAP_WARNING = "third-party OneMap-derived 2020 dump"


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object: {path}")
    return payload


def load_onemap_delay(params_path: Path = PARAMS_PATH) -> tuple[float, list[str]]:
    warnings: list[str] = []
    if not params_path.is_file():
        warnings.append(f"missing params file; using {DEFAULT_ONEMAP_DELAY_SEC:.1f}s OneMap delay")
        return DEFAULT_ONEMAP_DELAY_SEC, warnings

    with open(params_path, "r", encoding="utf-8") as f:
        params = yaml.safe_load(f) or {}

    value = params.get("onemap", {}).get("client_delay_sec") if isinstance(params, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value), warnings

    warnings.append(f"invalid onemap.client_delay_sec; using {DEFAULT_ONEMAP_DELAY_SEC:.1f}s delay")
    return DEFAULT_ONEMAP_DELAY_SEC, warnings


def format_duration(seconds: float) -> str:
    total_seconds = round(seconds)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def compact_source_stats(summary: dict[str, Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for source in summary.get("source_stats", []):
        if not isinstance(source, dict):
            continue
        compact.append(
            {
                "source_key": source.get("source_key"),
                "raw_records": source.get("raw_records"),
                "valid_unique_postals": source.get("valid_unique_postals"),
                "records_with_coordinates": source.get("records_with_coordinates"),
                "sha256": source.get("sha256"),
                "path": source.get("path"),
                "url": source.get("url"),
            }
        )
    return compact


def parquet_row_count(path: Path) -> int:
    return int(pq.read_metadata(path).num_rows)


def default_universe_paths(
    mode: str,
    processed_dir: Path = PROCESSED_DIR,
) -> tuple[Path, Path]:
    base_summary = processed_dir / f"postal_universe_{mode}_summary.json"
    base_universe = processed_dir / f"postal_universe_{mode}.parquet"
    geocoded_summary = processed_dir / f"postal_universe_{mode}_geocoded_summary.json"
    geocoded_universe = processed_dir / f"postal_universe_{mode}_geocoded.parquet"
    if geocoded_summary.is_file() and geocoded_universe.is_file():
        return geocoded_summary, geocoded_universe
    return base_summary, base_universe


def build_batch_plan(
    *,
    mode: str,
    summary_path: Path | None = None,
    universe_path: Path | None = None,
    params_path: Path = PARAMS_PATH,
    qa_path: Path | None = None,
    debug_path: Path | None = None,
    onemap_delay_sec: float | None = None,
) -> tuple[bool, dict[str, Any]]:
    default_summary_path, default_universe_path = default_universe_paths(mode)
    summary_path = summary_path or default_summary_path
    universe_path = universe_path or default_universe_path
    qa_path = qa_path or QA_DIR / "conflation_qa_island.json"
    debug_path = debug_path or QA_DIR / "island_debug.geojson"

    errors: list[str] = []
    warnings: list[str] = []
    summary: dict[str, Any] = {}

    if not summary_path.is_file():
        errors.append(f"missing postal universe summary: {summary_path}")
    else:
        try:
            summary = load_json(summary_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"could not read postal universe summary: {exc}")

    universe_rows = None
    if not universe_path.is_file():
        errors.append(f"missing postal universe parquet: {universe_path}")
    else:
        try:
            universe_rows = parquet_row_count(universe_path)
        except (OSError, pa.ArrowException) as exc:
            errors.append(f"could not read postal universe parquet metadata: {exc}")

    summary_total = summary.get("total_unique_postals")
    if universe_rows is not None and summary_total is not None and universe_rows != summary_total:
        errors.append(
            f"postal universe row mismatch: parquet has {universe_rows}, summary has {summary_total}"
        )

    if onemap_delay_sec is None:
        onemap_delay_sec, delay_warnings = load_onemap_delay(params_path)
        warnings.extend(delay_warnings)
    elif onemap_delay_sec <= 0:
        errors.append(f"onemap delay must be positive, got {onemap_delay_sec!r}")

    needs_geocode = int(summary.get("needs_geocode") or 0)
    ready_to_score = int(summary.get("ready_to_score") or 0)
    geocode_fill = summary.get("geocode_fill") if isinstance(summary, dict) else None
    geocode_fill_complete = (
        isinstance(geocode_fill, dict)
        and geocode_fill.get("ok") is True
        and int(geocode_fill.get("queued_postals") or 0)
        == int(geocode_fill.get("http_requests") or 0)
        + int(geocode_fill.get("cache_successes") or 0)
        + int(geocode_fill.get("cache_failures") or 0)
    )
    remaining_geocode_requests = 0 if geocode_fill_complete else needs_geocode
    wall_clock_seconds = float(remaining_geocode_requests) * float(onemap_delay_sec)

    island_ok, island_summary = validate_network_qa(qa_path, debug_path)
    summary_warnings = [str(item) for item in summary.get("warnings", []) if isinstance(item, str)]
    requires_universe_approval = any(
        THIRD_PARTY_ONEMAP_WARNING in warning for warning in summary_warnings
    )

    blockers: list[str] = [
        "human approval required before full geocode/scoring batch",
        "human approval required before production deploy or mock-to-real frontend cutover",
    ]
    if not island_ok:
        blockers.append("island-wide network QA is not green")
    if requires_universe_approval:
        blockers.append("postal universe uses third-party OneMap-derived 2020 source")
    if geocode_fill_complete and needs_geocode:
        warnings.append(
            f"{needs_geocode} source-derived postals remain unresolved after bounded OneMap geocode"
        )

    full_batch_allowed_now = False

    report: dict[str, Any] = {
        "ok": not errors,
        "mode": mode,
        "paths": {
            "summary": str(summary_path),
            "universe": str(universe_path),
            "params": str(params_path),
            "island_qa": str(qa_path),
            "island_debug": str(debug_path),
        },
        "postal_universe": {
            "generated_at": summary.get("generated_at"),
            "mode": summary.get("mode"),
            "total_unique_postals": summary_total,
            "ready_to_score": ready_to_score,
            "needs_geocode": needs_geocode,
            "parquet_rows": universe_rows,
            "source_stats": compact_source_stats(summary),
            "source_only_counts": summary.get("source_only_counts", {}),
            "warnings": summary_warnings,
        },
        "bounded_geocoding": {
            "consumer": "OneMap search API",
            "scope": (
                "completed bounded fill; remaining NEEDS_GEOCODE rows stay NOT_YET_SCORED"
                if geocode_fill_complete
                else "source-derived postals with NEEDS_GEOCODE only"
            ),
            "will_bruteforce": False,
            "delay_seconds": float(onemap_delay_sec),
            "requests": remaining_geocode_requests,
            "minimum_wall_clock_seconds": wall_clock_seconds,
            "minimum_wall_clock_human": format_duration(wall_clock_seconds),
            "completed_fill": geocode_fill if geocode_fill_complete else None,
            "unresolved_after_bounded_geocode": needs_geocode if geocode_fill_complete else None,
        },
        "scoring_batch": {
            "would_score_without_geocoding": ready_to_score,
            "would_score_after_bounded_geocoding": (
                ready_to_score if geocode_fill_complete else ready_to_score + needs_geocode
            ),
            "would_emit_records": universe_rows,
            "would_emit_not_yet_scored": (
                needs_geocode
                if geocode_fill_complete
                else max((universe_rows or 0) - ready_to_score, 0)
            ),
            "uses_python_igraph": True,
            "uses_project_conflated_graph": True,
            "epsg_internal": "EPSG:3414",
        },
        "checkpoint_gates": {
            "island_network_qa_ok": island_ok,
            "requires_human_approval_for_universe": requires_universe_approval,
            "full_geocode_scoring_batch_requires_human_approval": True,
            "production_deploy_requires_human_approval": True,
            "full_batch_allowed_now": full_batch_allowed_now,
            "blockers": blockers,
        },
        "island_network_qa": island_summary,
        "warnings": warnings,
        "errors": errors,
    }
    return not errors, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan the full postal geocode/scoring batch without running it."
    )
    parser.add_argument("--mode", choices=UNIVERSE_MODES, default="candidate_full_registered")
    parser.add_argument("--summary", type=Path, help="Override postal universe summary JSON.")
    parser.add_argument("--universe", type=Path, help="Override postal universe parquet.")
    parser.add_argument("--params", type=Path, default=PARAMS_PATH)
    parser.add_argument("--qa", type=Path, help="Override island network QA JSON.")
    parser.add_argument("--debug", type=Path, help="Override island debug GeoJSON.")
    parser.add_argument(
        "--onemap-delay-sec", type=float, help="Override OneMap delay for planning."
    )
    args = parser.parse_args()

    ok, report = build_batch_plan(
        mode=args.mode,
        summary_path=args.summary,
        universe_path=args.universe,
        params_path=args.params,
        qa_path=args.qa,
        debug_path=args.debug,
        onemap_delay_sec=args.onemap_delay_sec,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
