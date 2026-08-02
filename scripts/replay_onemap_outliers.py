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
    directional = report.get("top_outliers_by_direction")
    if direction != "any" and isinstance(directional, dict):
        outliers = directional.get(direction, [])
    else:
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


def route_source_profile(edges: Any) -> dict[str, Any] | None:
    if not isinstance(edges, list):
        return None
    source_lengths: dict[str, float] = {}
    synth_lengths: dict[str, float] = {}
    confidence_lengths: dict[str, float] = {}
    total_m = 0.0
    covered_m = 0.0
    exposed_m = 0.0
    inferred_hdb_m = 0.0
    direct_bus_fallback_m = 0.0
    bridge_underpass_m = 0.0
    official_lta_shelter_m = 0.0
    osm_shelter_m = 0.0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        try:
            length_m = float(edge.get("length_m") or 0.0)
        except (TypeError, ValueError):
            continue
        if length_m <= 0:
            continue
        source_layer = str(edge.get("source_layer") or "unknown")
        synth_class = str(edge.get("synth_class") or "none")
        confidence = str(edge.get("confidence") or "unknown")
        source_lengths[source_layer] = source_lengths.get(source_layer, 0.0) + length_m
        synth_lengths[synth_class] = synth_lengths.get(synth_class, 0.0) + length_m
        confidence_lengths[confidence] = confidence_lengths.get(confidence, 0.0) + length_m
        total_m += length_m
        if edge.get("is_covered"):
            covered_m += length_m
        else:
            exposed_m += length_m
        source_text = f"{source_layer}|{synth_class}".lower()
        if "inferred_hdb" in source_text:
            inferred_hdb_m += length_m
        if "direct_bus_fallback" in source_text:
            direct_bus_fallback_m += length_m
        if "overhead_bridge_underpass" in source_text:
            bridge_underpass_m += length_m
        if "covered_linkway" in source_text:
            official_lta_shelter_m += length_m
        if source_layer in {"osm_explicit_shelter", "osm_native_covered", "osm_building_roof"}:
            osm_shelter_m += length_m

    if total_m <= 0:
        return None

    def rounded_lengths(counter: dict[str, float]) -> dict[str, float]:
        return {key: round(value, 1) for key, value in sorted(counter.items())}

    return {
        "edge_count": sum(1 for edge in edges if isinstance(edge, dict)),
        "total_m": round(total_m, 1),
        "covered_m": round(covered_m, 1),
        "covered_ratio": round(covered_m / total_m, 3),
        "exposed_m": round(exposed_m, 1),
        "inferred_hdb_m": round(inferred_hdb_m, 1),
        "direct_bus_fallback_m": round(direct_bus_fallback_m, 1),
        "bridge_underpass_m": round(bridge_underpass_m, 1),
        "official_lta_shelter_m": round(official_lta_shelter_m, 1),
        "osm_shelter_m": round(osm_shelter_m, 1),
        "source_layer_m": rounded_lengths(source_lengths),
        "synth_class_m": rounded_lengths(synth_lengths),
        "confidence_m": rounded_lengths(confidence_lengths),
    }


def geometry_payload(record: dict[str, Any], mode: str | None = None) -> dict[str, Any] | None:
    if mode is not None:
        options = record.get("_geometry_options")
        option = options.get(mode) if isinstance(options, dict) else None
        if isinstance(option, dict):
            return option
        return None
    payload = record.get("_geometry")
    return payload if isinstance(payload, dict) else None


def route_profile(record: dict[str, Any], mode: str | None = None) -> dict[str, Any] | None:
    payload = geometry_payload(record, mode)
    if payload is None:
        return None
    shortest = route_source_profile(payload.get("shortest_path_edges"))
    sheltered = route_source_profile(payload.get("sheltered_path_edges"))
    if shortest is None and sheltered is None:
        return None
    return {"shortest": shortest, "sheltered": sheltered}


def replay_row(
    old: dict[str, Any],
    record: dict[str, Any],
    *,
    include_route_source_profile: bool = False,
) -> dict[str, Any]:
    bus = route_option(record, "bus")
    fallback = None
    provenance = record.get("provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("direct_bus_fallback"), dict):
        fallback = provenance["direct_bus_fallback"]
    row = {
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
    if include_route_source_profile:
        row["new_best_route_profile"] = route_profile(record)
        row["new_bus_route_profile"] = route_profile(record, "bus")
    return row


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
    include_route_source_profile: bool = False,
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
        records = score_postal_gdf(
            postal_gdf,
            context,
            include_geometry=include_route_source_profile,
        )
        for record in records:
            postal = str(record.get("postal") or "").zfill(6)
            old = old_by_postal.get(postal)
            if old is not None:
                rows.append(
                    replay_row(
                        old,
                        record,
                        include_route_source_profile=include_route_source_profile,
                    )
                )

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
            "include_route_source_profile": include_route_source_profile,
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
    parser.add_argument("--route-source-profile", action="store_true")
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
        include_route_source_profile=bool(args.route_source_profile),
    )
    printable = {key: value for key, value in summary.items() if key != "rows"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
