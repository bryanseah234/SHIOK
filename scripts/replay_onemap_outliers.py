from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.scoring_integration import (
    NETWORK_PATH,
    load_postal_universe_points,
    load_scoring_context,
    score_postal_gdf,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = PROJECT_ROOT / "qa" / "onemap_validation_cached_report_20260802.json"
DEFAULT_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "qa" / "onemap_outlier_replay_20260802.json"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_outliers(
    report: dict[str, Any],
    *,
    limit: int,
    node_type: str,
    direction: str,
    min_abs_pct_delta: float,
) -> list[dict[str, Any]]:
    outliers = report.get("top_outliers_preview", [])
    if not isinstance(outliers, list):
        return []

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in outliers:
        if not isinstance(row, dict):
            continue
        postal = str(row.get("postal") or "").zfill(6)
        if not postal or postal in seen:
            continue
        if node_type != "any" and row.get("best_node_type") != node_type:
            continue
        if direction != "any" and row.get("direction") != direction:
            continue
        try:
            abs_pct_delta = float(row.get("abs_pct_delta") or 0.0)
        except (TypeError, ValueError):
            continue
        if abs_pct_delta < min_abs_pct_delta:
            continue
        selected.append(row)
        seen.add(postal)
        if len(selected) >= limit:
            break
    return selected


def path_value(record: dict[str, Any], key: str) -> Any:
    paths = record.get("paths")
    return paths.get(key) if isinstance(paths, dict) else None


def route_option(record: dict[str, Any], name: str) -> dict[str, Any]:
    route_options = record.get("route_options")
    if not isinstance(route_options, dict):
        return {}
    option = route_options.get(name)
    return option if isinstance(option, dict) else {}


def route_option_path_value(option: dict[str, Any], key: str) -> Any:
    paths = option.get("paths")
    return paths.get(key) if isinstance(paths, dict) else None


def best_node_value(record: dict[str, Any], key: str) -> Any:
    best_node = record.get("best_node")
    return best_node.get(key) if isinstance(best_node, dict) else None


def replay_row(old: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    bus = route_option(record, "bus")
    fallback = None
    provenance = record.get("provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("direct_bus_fallback"), dict):
        fallback = provenance["direct_bus_fallback"]
    return {
        "postal": str(record["postal"]).zfill(6),
        "old_validation_best_node": old.get("best_node_name"),
        "old_project_shortest_m": old.get("project_shortest_m"),
        "old_onemap_walk_m": old.get("onemap_walk_m"),
        "old_abs_pct_delta": old.get("abs_pct_delta"),
        "old_direction": old.get("direction"),
        "new_state": record.get("state"),
        "new_total": record.get("total"),
        "new_best_type": best_node_value(record, "type"),
        "new_best_name": best_node_value(record, "name"),
        "new_best_shortest_m": path_value(record, "shortest_m"),
        "new_best_routing_type": path_value(record, "routing_type"),
        "new_bus_state": bus.get("state"),
        "new_bus_shortest_m": route_option_path_value(bus, "shortest_m"),
        "new_bus_routing_type": route_option_path_value(bus, "routing_type"),
        "direct_bus_fallback_reason": fallback.get("reason") if fallback else None,
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fallback_reasons = Counter(str(row.get("direct_bus_fallback_reason") or "none") for row in rows)
    best_types = Counter(str(row.get("new_best_type") or "none") for row in rows)
    return {
        "sample_size": len(rows),
        "new_best_direct_bus_fallback_count": sum(
            row.get("new_best_routing_type") == "direct_bus_fallback_unrouted" for row in rows
        ),
        "new_bus_direct_bus_fallback_count": sum(
            row.get("new_bus_routing_type") == "direct_bus_fallback_unrouted" for row in rows
        ),
        "new_best_type_counts": dict(sorted(best_types.items())),
        "fallback_reason_counts": dict(sorted(fallback_reasons.items())),
    }


def replay_outliers(
    *,
    report_path: Path,
    postal_universe_path: Path,
    network_path: Path,
    output_path: Path,
    limit: int,
    node_type: str,
    direction: str,
    min_abs_pct_delta: float,
) -> dict[str, Any]:
    report = read_json(report_path)
    if not isinstance(report, dict):
        raise TypeError(f"expected JSON object in {report_path}")
    selected = select_outliers(
        report,
        limit=limit,
        node_type=node_type,
        direction=direction,
        min_abs_pct_delta=min_abs_pct_delta,
    )
    postals = [str(row["postal"]).zfill(6) for row in selected]
    old_by_postal = {str(row["postal"]).zfill(6): row for row in selected}

    rows: list[dict[str, Any]] = []
    if postals:
        context = load_scoring_context(
            network_path=network_path,
            postal_universe_path=postal_universe_path,
        )
        postal_gdf = load_postal_universe_points(postal_universe_path, postal_codes=postals)
        records = score_postal_gdf(postal_gdf, context, include_geometry=False)
        for record in records:
            postal = str(record.get("postal") or "").zfill(6)
            old = old_by_postal.get(postal)
            if old is not None:
                rows.append(replay_row(old, record))

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_report": str(report_path.relative_to(PROJECT_ROOT)),
        "postal_universe": str(postal_universe_path.relative_to(PROJECT_ROOT)),
        "network": str(network_path.relative_to(PROJECT_ROOT)),
        "selection": {
            "limit": int(limit),
            "node_type": node_type,
            "direction": direction,
            "min_abs_pct_delta": float(min_abs_pct_delta),
            "selected_postals": len(postals),
            "scored_postals": len(rows),
        },
        **summarize_rows(rows),
        "rows": rows,
    }
    write_json(output_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay OneMap validation outliers through current local scoring."
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--postal-universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--network", type=Path, default=NETWORK_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--node-type", default="bus_stop")
    parser.add_argument("--direction", default="project_longer_than_onemap")
    parser.add_argument("--min-abs-pct-delta", type=float, default=25.0)
    args = parser.parse_args()

    summary = replay_outliers(
        report_path=args.report,
        postal_universe_path=args.postal_universe,
        network_path=args.network,
        output_path=args.output,
        limit=args.limit,
        node_type=args.node_type,
        direction=args.direction,
        min_abs_pct_delta=args.min_abs_pct_delta,
    )
    printable = {key: value for key, value in summary.items() if key != "rows"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
