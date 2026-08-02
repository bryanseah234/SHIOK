from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LONGER_PROFILE = (
    PROJECT_ROOT / "qa" / "onemap_outlier_replay_bus_longer_profile_100_20260802.json"
)
DEFAULT_SHORTER_PROFILE = (
    PROJECT_ROOT / "qa" / "onemap_outlier_replay_shorter_profile_100_20260802.json"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "qa" / "onemap_outlier_triage_queues_20260802.json"
DEFAULT_VALIDATION_REPORT = PROJECT_ROOT / "qa" / "onemap_validation_cached_report_20260802.json"
DEFAULT_GEOJSON_OUTPUT = PROJECT_ROOT / "qa" / "onemap_outlier_triage_queues_20260802.geojson"

DIRECT_BUS_FALLBACK_ROUTING = "direct_bus_fallback_unrouted"
FALLBACK_REASONS = {
    "implausible_graph_route_to_datamall_bus_stop_within_direct_radius",
    "no_graph_routed_transit_candidate_but_datamall_bus_stop_within_direct_radius",
}
MRT_LRT_NAME_MARKERS = (" MRT ", " LRT ", "MRT STATION", "LRT STATION")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def shortest_profile(row: dict[str, Any], key: str = "new_best_route_profile") -> dict[str, Any]:
    profile = row.get(key)
    if not isinstance(profile, dict):
        return {}
    shortest = profile.get("shortest")
    return shortest if isinstance(shortest, dict) else {}


def profile_m(row: dict[str, Any], metric: str, key: str = "new_best_route_profile") -> float:
    profile = shortest_profile(row, key)
    try:
        return float(profile.get(metric) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def top_lengths(lengths: Any, *, limit: int = 5) -> dict[str, float]:
    if not isinstance(lengths, dict):
        return {}
    rows: list[tuple[str, float]] = []
    for key, value in lengths.items():
        try:
            length_m = float(value)
        except (TypeError, ValueError):
            continue
        if length_m > 0:
            rows.append((str(key), length_m))
    return {
        key: round(length_m, 1)
        for key, length_m in sorted(rows, key=lambda item: item[1], reverse=True)[:limit]
    }


def validation_lookup(report_path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if report_path is None:
        return {}
    payload = read_json(report_path)
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object in {report_path}")

    rows: list[dict[str, Any]] = []
    directional = payload.get("top_outliers_by_direction")
    if isinstance(directional, dict):
        for group in directional.values():
            if isinstance(group, list):
                rows.extend(row for row in group if isinstance(row, dict))
    preview = payload.get("top_outliers_preview")
    if isinstance(preview, list):
        rows.extend(row for row in preview if isinstance(row, dict))

    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        postal = str(row.get("postal") or "").zfill(6)
        direction = str(row.get("direction") or "")
        if not postal or not direction:
            continue
        key = (postal, direction)
        old_delta = float(lookup.get(key, {}).get("abs_pct_delta") or -1.0)
        try:
            new_delta = float(row.get("abs_pct_delta") or 0.0)
        except (TypeError, ValueError):
            new_delta = 0.0
        if key not in lookup or new_delta >= old_delta:
            lookup[key] = row
    return lookup


def has_direct_bus_fallback(row: dict[str, Any]) -> bool:
    reason = row.get("direct_bus_fallback_reason")
    return (
        reason in FALLBACK_REASONS
        or row.get("new_best_routing_type") == DIRECT_BUS_FALLBACK_ROUTING
        or row.get("new_bus_routing_type") == DIRECT_BUS_FALLBACK_ROUTING
        or profile_m(row, "direct_bus_fallback_m") > 0
        or profile_m(row, "direct_bus_fallback_m", "new_bus_route_profile") > 0
    )


def looks_like_mrt_lrt(row: dict[str, Any]) -> bool:
    if row.get("new_best_type") == "mrt_lrt_exit":
        return True
    name = f" {row.get('old_validation_best_node') or ''} ".upper()
    return any(marker in name for marker in MRT_LRT_NAME_MARKERS)


def is_unscored_or_no_best(row: dict[str, Any]) -> bool:
    state = str(row.get("new_state") or "")
    return (
        state in {"NO_TRANSIT_IN_RANGE", "NOT_YET_SCORED", "ERROR"}
        or not row.get("new_best_type")
        or row.get("new_best_type") == "none"
    )


def source_flags(row: dict[str, Any]) -> dict[str, Any]:
    best = shortest_profile(row, "new_best_route_profile")
    bus = shortest_profile(row, "new_bus_route_profile")

    def metric(name: str) -> float:
        return round(profile_m(row, name), 1)

    def bus_metric(name: str) -> float:
        return round(profile_m(row, name, "new_bus_route_profile"), 1)

    return {
        "best_inferred_hdb_m": metric("inferred_hdb_m"),
        "best_direct_bus_fallback_m": metric("direct_bus_fallback_m"),
        "best_bridge_underpass_m": metric("bridge_underpass_m"),
        "best_official_lta_shelter_m": metric("official_lta_shelter_m"),
        "best_osm_shelter_m": metric("osm_shelter_m"),
        "best_top_source_layer_m": top_lengths(best.get("source_layer_m")),
        "bus_direct_bus_fallback_m": bus_metric("direct_bus_fallback_m"),
        "bus_top_source_layer_m": top_lengths(bus.get("source_layer_m")),
    }


def classify_row(row: dict[str, Any]) -> list[str]:
    queues: list[str] = []
    direction = row.get("old_direction")

    if has_direct_bus_fallback(row):
        queues.append("direct_bus_fallback_review")
        if direction == "project_longer_than_onemap":
            queues.append("missing_bus_connector")

    if direction == "project_shorter_than_onemap":
        queues.append("possible_overpermissive_project_path")

    if looks_like_mrt_lrt(row):
        queues.append("mrt_lrt_outlier")

    if profile_m(row, "inferred_hdb_m") > 0 or profile_m(row, "bridge_underpass_m") > 0:
        queues.append("hdb_bridge_connector_review")

    if is_unscored_or_no_best(row):
        queues.append("still_unscored_or_no_best")

    return queues


def compact_row(
    row: dict[str, Any],
    *,
    source_artifact: str,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    flags = source_flags(row)
    validation = validation or {}
    compact = {
        "postal": str(row.get("postal") or "").zfill(6),
        "source_artifact": source_artifact,
        "validation_area": validation.get("area"),
        "validation_best_node_type": validation.get("best_node_type"),
        "validation_direct_distance_m": validation.get("direct_distance_m"),
        "validation_onemap_vs_direct_delta_m": validation.get("onemap_vs_direct_delta_m"),
        "validation_distance_sanity": validation.get("distance_sanity"),
        "endpoint_source": validation.get("endpoint_source"),
        "start": validation.get("start"),
        "end": validation.get("end"),
        "old_validation_best_node": row.get("old_validation_best_node"),
        "old_project_shortest_m": row.get("old_project_shortest_m"),
        "old_onemap_walk_m": row.get("old_onemap_walk_m"),
        "old_abs_pct_delta": row.get("old_abs_pct_delta"),
        "old_direction": row.get("old_direction"),
        "new_state": row.get("new_state"),
        "new_total": row.get("new_total"),
        "new_best_type": row.get("new_best_type"),
        "new_best_name": row.get("new_best_name"),
        "new_best_shortest_m": row.get("new_best_shortest_m"),
        "new_best_routing_type": row.get("new_best_routing_type"),
        "new_bus_state": row.get("new_bus_state"),
        "new_bus_shortest_m": row.get("new_bus_shortest_m"),
        "new_bus_routing_type": row.get("new_bus_routing_type"),
        "direct_bus_fallback_reason": row.get("direct_bus_fallback_reason"),
        "source_flags": flags,
    }
    return compact


def queue_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    directions = Counter(str(row.get("old_direction") or "unknown") for row in rows)
    best_types = Counter(str(row.get("new_best_type") or "none") for row in rows)
    fallback_reasons = Counter(str(row.get("direct_bus_fallback_reason") or "none") for row in rows)
    sanity = Counter(str(row.get("validation_distance_sanity") or "unknown") for row in rows)
    source_layer_m: dict[str, float] = {}

    for row in rows:
        flags = row.get("source_flags")
        if not isinstance(flags, dict):
            continue
        for key, value in flags.get("best_top_source_layer_m", {}).items():
            try:
                source_layer_m[str(key)] = source_layer_m.get(str(key), 0.0) + float(value)
            except (TypeError, ValueError):
                continue

    return {
        "count": len(rows),
        "direction_counts": dict(sorted(directions.items())),
        "new_best_type_counts": dict(sorted(best_types.items())),
        "fallback_reason_counts": dict(sorted(fallback_reasons.items())),
        "validation_distance_sanity_counts": dict(sorted(sanity.items())),
        "top_best_source_layer_m": top_lengths(source_layer_m),
    }


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object in {path}")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise TypeError(f"expected rows array in {path}")
    return [row for row in rows if isinstance(row, dict)]


def feature_for_row(queue_name: str, row: dict[str, Any]) -> dict[str, Any] | None:
    start = row.get("start")
    end = row.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    try:
        start_lon = float(start["lon"])
        start_lat = float(start["lat"])
        end_lon = float(end["lon"])
        end_lat = float(end["lat"])
    except (KeyError, TypeError, ValueError):
        return None

    properties = {key: value for key, value in row.items() if key not in {"start", "end"}}
    properties["queue"] = queue_name
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[start_lon, start_lat], [end_lon, end_lat]],
        },
        "properties": properties,
    }


def triage_geojson(queues: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for queue_name, rows in queues.items():
        for row in rows:
            feature = feature_for_row(queue_name, row)
            if feature is not None:
                features.append(feature)
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def build_triage_queues(
    *,
    longer_profile_path: Path,
    shorter_profile_path: Path,
    validation_report_path: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    source_rows = [
        (display_path(longer_profile_path), load_rows(longer_profile_path)),
        (display_path(shorter_profile_path), load_rows(shorter_profile_path)),
    ]
    queues: dict[str, list[dict[str, Any]]] = {
        "missing_bus_connector": [],
        "direct_bus_fallback_review": [],
        "possible_overpermissive_project_path": [],
        "mrt_lrt_outlier": [],
        "hdb_bridge_connector_review": [],
        "still_unscored_or_no_best": [],
    }
    seen_by_queue: dict[str, set[str]] = {name: set() for name in queues}
    input_row_count = 0
    validation_by_postal_direction = validation_lookup(validation_report_path)

    for source_artifact, rows in source_rows:
        input_row_count += len(rows)
        for row in rows:
            postal = str(row.get("postal") or "").zfill(6)
            direction = str(row.get("old_direction") or "")
            compact = compact_row(
                row,
                source_artifact=source_artifact,
                validation=validation_by_postal_direction.get((postal, direction)),
            )
            for queue_name in classify_row(row):
                key = f"{postal}|{source_artifact}"
                if key in seen_by_queue[queue_name]:
                    continue
                queues[queue_name].append(compact)
                seen_by_queue[queue_name].add(key)

    summaries = {name: queue_summary(rows) for name, rows in queues.items()}
    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "inputs": {
            "project_longer_profile": display_path(longer_profile_path),
            "project_shorter_profile": display_path(shorter_profile_path),
            "validation_report": (
                display_path(validation_report_path) if validation_report_path is not None else None
            ),
            "input_rows": input_row_count,
        },
        "queue_summaries": summaries,
        "queues": queues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build concrete QA queues from profiled OneMap validation outlier replays."
    )
    parser.add_argument("--longer-profile", type=Path, default=DEFAULT_LONGER_PROFILE)
    parser.add_argument("--shorter-profile", type=Path, default=DEFAULT_SHORTER_PROFILE)
    parser.add_argument("--validation-report", type=Path, default=DEFAULT_VALIDATION_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--geojson-output", type=Path, default=DEFAULT_GEOJSON_OUTPUT)
    args = parser.parse_args()

    payload = build_triage_queues(
        longer_profile_path=args.longer_profile,
        shorter_profile_path=args.shorter_profile,
        validation_report_path=args.validation_report,
    )
    write_json(args.output, payload)
    write_json(args.geojson_output, triage_geojson(payload["queues"]))
    printable = {key: value for key, value in payload.items() if key != "queues"}
    printable["geojson_output"] = display_path(args.geojson_output)
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
