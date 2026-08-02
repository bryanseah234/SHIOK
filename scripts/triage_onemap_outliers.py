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
DEFAULT_MISSING_BUS_PRIORITY_GEOJSON_OUTPUT = (
    PROJECT_ROOT / "qa" / "onemap_missing_bus_connector_priority_20260802.geojson"
)
DEFAULT_OVERPERMISSIVE_PRIORITY_GEOJSON_OUTPUT = (
    PROJECT_ROOT / "qa" / "onemap_overpermissive_priority_20260802.geojson"
)
DEFAULT_VALIDATION_SUBSET_PRIORITY_GEOJSON_OUTPUT = (
    PROJECT_ROOT / "qa" / "onemap_validation_subset_priority_20260802.geojson"
)

DIRECT_BUS_FALLBACK_ROUTING = "direct_bus_fallback_unrouted"
FALLBACK_REASONS = {
    "implausible_graph_route_to_datamall_bus_stop_within_direct_radius",
    "implausibly_short_graph_route_to_datamall_bus_stop_within_direct_radius",
    "multiple_implausible_graph_routes_to_datamall_bus_stops_within_direct_radius",
    "no_graph_routed_transit_candidate_but_datamall_bus_stop_within_direct_radius",
}
MRT_LRT_NAME_MARKERS = (" MRT ", " LRT ", "MRT STATION", "LRT STATION")
SHORT_ONEMAP_WALK_REVIEW_M = 20.0
GRAPH_ROUTED_TRUST = {
    "graph_routed_bus_stop",
    "graph_routed_mrt_lrt",
    "graph_routed_other",
}


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


def profile_source_m(
    row: dict[str, Any],
    source_layer: str,
    key: str = "new_best_route_profile",
) -> float:
    profile = shortest_profile(row, key)
    lengths = profile.get("source_layer_m")
    if not isinstance(lengths, dict):
        return 0.0
    try:
        return float(lengths.get(source_layer) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def untrusted_route_reason_counts(row: dict[str, Any]) -> dict[str, int]:
    counts = row.get("untrusted_bus_route_reason_counts")
    if not isinstance(counts, dict):
        return {}
    parsed: dict[str, int] = {}
    for reason, count in counts.items():
        try:
            parsed[str(reason)] = int(count)
        except (TypeError, ValueError):
            continue
    return parsed


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
    full_results = payload.get("results")
    if isinstance(full_results, list):
        rows.extend(row for row in full_results if isinstance(row, dict))
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


def has_active_direct_bus_fallback_route(row: dict[str, Any]) -> bool:
    return (
        row.get("new_best_routing_type") == DIRECT_BUS_FALLBACK_ROUTING
        or row.get("new_bus_routing_type") == DIRECT_BUS_FALLBACK_ROUTING
        or profile_m(row, "direct_bus_fallback_m") > 0
        or profile_m(row, "direct_bus_fallback_m", "new_bus_route_profile") > 0
    )


def looks_like_mrt_lrt(row: dict[str, Any]) -> bool:
    if row.get("new_best_type") == "mrt_lrt_exit":
        return True
    name = f" {row.get('old_validation_best_node') or ''} ".upper()
    return any(marker in name for marker in MRT_LRT_NAME_MARKERS)


def routed_vs_validation_direct_sanity(row: dict[str, Any], validation: dict[str, Any]) -> str:
    try:
        routed_m = float(row.get("new_best_shortest_m") or 0.0)
        direct_m = float(validation.get("direct_distance_m") or 0.0)
    except (TypeError, ValueError):
        return "unknown"
    if routed_m <= 0 or direct_m <= 0:
        return "unknown"
    if routed_m + 5.0 < direct_m * 0.8:
        return "current_route_materially_shorter_than_validation_direct"
    if routed_m + 5.0 < direct_m:
        return "current_route_slightly_shorter_than_validation_direct"
    return "plausible"


def is_unscored_or_no_best(row: dict[str, Any]) -> bool:
    state = str(row.get("new_state") or "")
    return (
        state in {"NO_TRANSIT_IN_RANGE", "NOT_YET_SCORED", "ERROR"}
        or not row.get("new_best_type")
        or row.get("new_best_type") == "none"
    )


def has_untrusted_bus_route(row: dict[str, Any]) -> bool:
    return bool(untrusted_route_reason_counts(row))


def likely_access_barrier_divergence(row: dict[str, Any]) -> bool:
    if row.get("old_direction") != "project_shorter_than_onemap":
        return False
    if looks_like_mrt_lrt(row) or is_unscored_or_no_best(row):
        return False
    unknown_m = max(
        profile_source_m(row, "unknown"),
        profile_source_m(row, "unknown", "new_bus_route_profile"),
    )
    if unknown_m < 100.0:
        return False
    return (
        profile_m(row, "inferred_hdb_m") <= 0
        and profile_m(row, "bridge_underpass_m") <= 0
        and profile_m(row, "direct_bus_fallback_m") <= 0
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
        "best_bus_stop_access_connector_m": metric("bus_stop_access_connector_m"),
        "best_unknown_source_m": round(profile_source_m(row, "unknown"), 1),
        "best_top_source_layer_m": top_lengths(best.get("source_layer_m")),
        "bus_direct_bus_fallback_m": bus_metric("direct_bus_fallback_m"),
        "bus_bus_stop_access_connector_m": bus_metric("bus_stop_access_connector_m"),
        "bus_unknown_source_m": round(profile_source_m(row, "unknown", "new_bus_route_profile"), 1),
        "bus_top_source_layer_m": top_lengths(bus.get("source_layer_m")),
        "untrusted_bus_route_reason_counts": untrusted_route_reason_counts(row),
    }


def classify_row(row: dict[str, Any]) -> list[str]:
    queues: list[str] = []
    direction = row.get("old_direction")

    if has_direct_bus_fallback(row):
        queues.append("direct_bus_fallback_review")
        if direction == "project_longer_than_onemap" and has_active_direct_bus_fallback_route(row):
            queues.append("missing_bus_connector")

    if direction == "project_shorter_than_onemap":
        queues.append("possible_overpermissive_project_path")

    if likely_access_barrier_divergence(row):
        queues.append("access_barrier_review")

    if looks_like_mrt_lrt(row):
        queues.append("mrt_lrt_outlier")

    if profile_m(row, "inferred_hdb_m") > 0 or profile_m(row, "bridge_underpass_m") > 0:
        queues.append("hdb_bridge_connector_review")

    if is_unscored_or_no_best(row):
        queues.append("still_unscored_or_no_best")

    if has_untrusted_bus_route(row):
        queues.append("untrusted_bus_route_review")

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
        "validation_onemap_walk_m": validation.get("onemap_walk_m"),
        "validation_abs_delta_m": validation.get("abs_delta_m"),
        "validation_onemap_vs_direct_delta_m": validation.get("onemap_vs_direct_delta_m"),
        "validation_distance_sanity": validation.get("distance_sanity"),
        "current_route_vs_validation_direct_sanity": routed_vs_validation_direct_sanity(
            row, validation
        ),
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


def is_short_onemap_walk_review(row: dict[str, Any]) -> bool:
    try:
        onemap_m = float(row.get("validation_onemap_walk_m") or 0.0)
    except (TypeError, ValueError):
        return False
    return 0 < onemap_m <= SHORT_ONEMAP_WALK_REVIEW_M


def queue_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    directions = Counter(str(row.get("old_direction") or "unknown") for row in rows)
    best_types = Counter(str(row.get("new_best_type") or "none") for row in rows)
    fallback_reasons = Counter(str(row.get("direct_bus_fallback_reason") or "none") for row in rows)
    sanity = Counter(str(row.get("validation_distance_sanity") or "unknown") for row in rows)
    route_sanity = Counter(
        str(row.get("current_route_vs_validation_direct_sanity") or "unknown") for row in rows
    )
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
        "current_route_vs_validation_direct_sanity_counts": dict(sorted(route_sanity.items())),
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


def validation_subset_rows(report: dict[str, Any], subset_name: str) -> list[dict[str, Any]]:
    results = report.get("results")
    if not isinstance(results, list):
        return []
    rows = [row for row in results if isinstance(row, dict)]

    if subset_name == "all_valid_cached":
        return rows
    if subset_name == "graph_routed_without_endpoint_connector":
        return [row for row in rows if row.get("route_trust") in GRAPH_ROUTED_TRUST]
    if subset_name == "graph_routed_bus_stop":
        return [row for row in rows if row.get("route_trust") == "graph_routed_bus_stop"]
    if subset_name == "graph_routed_mrt_lrt":
        return [row for row in rows if row.get("route_trust") == "graph_routed_mrt_lrt"]
    if subset_name == "endpoint_connector":
        return [
            row for row in rows if row.get("route_trust") == "graph_route_with_endpoint_connector"
        ]
    if subset_name == "plausible_onemap_distance":
        return [row for row in rows if row.get("distance_sanity") == "plausible"]
    if subset_name == "non_tiny_onemap_walk_gt_20m":
        return [row for row in rows if row.get("onemap_walk_bucket") != "le_20m"]
    raise ValueError(f"unknown validation subset: {subset_name}")


def validation_row_sort_value(row: dict[str, Any]) -> tuple[float, float]:
    try:
        pct = float(row.get("abs_pct_delta") or 0.0)
    except (TypeError, ValueError):
        pct = 0.0
    try:
        metres = float(row.get("abs_delta_m") or 0.0)
    except (TypeError, ValueError):
        metres = 0.0
    return (-pct, -metres)


def compact_validation_row(
    row: dict[str, Any],
    *,
    subset_name: str,
    priority_rank: int,
) -> dict[str, Any]:
    return {
        "postal": str(row.get("postal") or "").zfill(6),
        "subset": subset_name,
        "priority_rank": priority_rank,
        "area": row.get("area"),
        "state": row.get("state"),
        "route_trust": row.get("route_trust"),
        "routing_type": row.get("routing_type"),
        "best_node_type": row.get("best_node_type"),
        "best_node_name": row.get("best_node_name"),
        "endpoint_source": row.get("endpoint_source"),
        "project_shortest_m": row.get("project_shortest_m"),
        "onemap_walk_m": row.get("onemap_walk_m"),
        "direct_distance_m": row.get("direct_distance_m"),
        "abs_delta_m": row.get("abs_delta_m"),
        "abs_pct_delta": row.get("abs_pct_delta"),
        "signed_delta_m": row.get("signed_delta_m"),
        "signed_pct_delta": row.get("signed_pct_delta"),
        "direction": row.get("direction"),
        "distance_sanity": row.get("distance_sanity"),
        "onemap_walk_bucket": row.get("onemap_walk_bucket"),
        "cache_key": row.get("cache_key"),
        "start": row.get("start"),
        "end": row.get("end"),
    }


def validation_subset_priority_geojson(
    report: dict[str, Any],
    *,
    subset_name: str,
    limit: int = 50,
) -> dict[str, Any]:
    rows = validation_subset_rows(report, subset_name)
    features: list[dict[str, Any]] = []
    for rank, row in enumerate(sorted(rows, key=validation_row_sort_value)[:limit], start=1):
        feature = feature_for_row(
            "validation_subset_priority",
            compact_validation_row(row, subset_name=subset_name, priority_rank=rank),
        )
        if feature is not None:
            features.append(feature)
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def validation_subset_priority_summary(
    report: dict[str, Any],
    *,
    subset_name: str,
    limit: int = 10,
) -> dict[str, Any]:
    rows = validation_subset_rows(report, subset_name)
    top_rows = sorted(rows, key=validation_row_sort_value)[:limit]
    direction_counts = Counter(str(row.get("direction") or "unknown") for row in rows)
    best_type_counts = Counter(str(row.get("best_node_type") or "unknown") for row in rows)
    return {
        "subset": subset_name,
        "count": len(rows),
        "direction_counts": dict(sorted(direction_counts.items())),
        "best_node_type_counts": dict(sorted(best_type_counts.items())),
        "top_review_rows": [
            compact_validation_row(row, subset_name=subset_name, priority_rank=rank)
            for rank, row in enumerate(top_rows, start=1)
        ],
        "recommended_next_actions": [
            "Open the priority GeoJSON and inspect the largest percent deltas first.",
            "Separate missing connector evidence from overpermissive project paths before rescoring.",
            "Do not relax route trust thresholds unless audited cases prove the connector class is safe.",
        ],
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


def missing_bus_connector_priority_geojson(
    queues: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    rows = [
        row
        for row in queues.get("missing_bus_connector", [])
        if row.get("validation_distance_sanity") == "plausible"
        and row.get("current_route_vs_validation_direct_sanity") == "plausible"
    ]

    def priority_value(row: dict[str, Any]) -> float:
        try:
            return float(row.get("old_abs_pct_delta") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    features: list[dict[str, Any]] = []
    for rank, row in enumerate(sorted(rows, key=priority_value, reverse=True), start=1):
        ranked_row = {**row, "priority_rank": rank}
        feature = feature_for_row("missing_bus_connector_priority", ranked_row)
        if feature is not None:
            features.append(feature)
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def overpermissive_priority_geojson(
    queues: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    rows = queues.get("possible_overpermissive_project_path", [])

    def priority_class(row: dict[str, Any]) -> str | None:
        route_sanity = str(row.get("current_route_vs_validation_direct_sanity") or "")
        if route_sanity in {
            "current_route_materially_shorter_than_validation_direct",
            "current_route_slightly_shorter_than_validation_direct",
        }:
            return "shorter_than_validation_direct"
        if numeric_source_flag(row, "best_unknown_source_m") >= 100.0 and not row.get(
            "direct_bus_fallback_reason"
        ):
            return "unknown_dominant_non_fallback"
        return None

    def priority_sort(row: dict[str, Any]) -> tuple[int, float]:
        klass = priority_class(row)
        try:
            delta = float(row.get("old_abs_pct_delta") or 0.0)
        except (TypeError, ValueError):
            delta = 0.0
        return (0 if klass == "shorter_than_validation_direct" else 1, -delta)

    features: list[dict[str, Any]] = []
    priority_rows = [row for row in rows if priority_class(row)]
    for rank, row in enumerate(sorted(priority_rows, key=priority_sort), start=1):
        ranked_row = {
            **row,
            "priority_rank": rank,
            "priority_class": priority_class(row),
        }
        feature = feature_for_row("overpermissive_priority", ranked_row)
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
        "short_onemap_walk_review": [],
        "direct_bus_fallback_review": [],
        "possible_overpermissive_project_path": [],
        "access_barrier_review": [],
        "mrt_lrt_outlier": [],
        "hdb_bridge_connector_review": [],
        "still_unscored_or_no_best": [],
        "untrusted_bus_route_review": [],
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
            queue_names = classify_row(row)
            if is_short_onemap_walk_review(compact):
                queue_names = [name for name in queue_names if name != "missing_bus_connector"]
                queue_names.append("short_onemap_walk_review")
            for queue_name in queue_names:
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


def priority_queue_count(queues: dict[str, list[dict[str, Any]]]) -> int:
    return len(
        [
            row
            for row in queues.get("missing_bus_connector", [])
            if row.get("validation_distance_sanity") == "plausible"
            and row.get("current_route_vs_validation_direct_sanity") == "plausible"
        ]
    )


def validation_failure_summary(payload: dict[str, Any]) -> dict[str, Any]:
    queues = payload.get("queues")
    if not isinstance(queues, dict):
        queues = {}
    typed_queues = {
        str(name): rows if isinstance(rows, list) else [] for name, rows in queues.items()
    }
    queue_counts = {
        name: len(rows) for name, rows in sorted(typed_queues.items(), key=lambda item: item[0])
    }
    priority_order = [
        {
            "rank": 1,
            "queue": "missing_bus_connector",
            "count": queue_counts.get("missing_bus_connector", 0),
            "strict_priority_count": priority_queue_count(typed_queues),
            "next_action": (
                "audit bus-stop endpoint connectors and side-of-road access before "
                "widening route trust thresholds"
            ),
        },
        {
            "rank": 2,
            "queue": "untrusted_bus_route_review",
            "count": queue_counts.get("untrusted_bus_route_review", 0),
            "next_action": "inspect rejected bus routes before promoting any guarded rescore rows",
        },
        {
            "rank": 3,
            "queue": "possible_overpermissive_project_path",
            "count": queue_counts.get("possible_overpermissive_project_path", 0),
            "next_action": "check whether project paths cut through barriers or unsupported access",
        },
        {
            "rank": 4,
            "queue": "hdb_bridge_connector_review",
            "count": queue_counts.get("hdb_bridge_connector_review", 0),
            "next_action": "review inferred HDB, bridge, and underpass connectors against source evidence",
        },
        {
            "rank": 5,
            "queue": "mrt_lrt_outlier",
            "count": queue_counts.get("mrt_lrt_outlier", 0),
            "next_action": "separately audit MRT/LRT exit snapping and station-side walk geometry",
        },
        {
            "rank": 6,
            "queue": "access_barrier_review",
            "count": queue_counts.get("access_barrier_review", 0),
            "next_action": "look for barriers, gates, private access, or missing crossings",
        },
        {
            "rank": 7,
            "queue": "short_onemap_walk_review",
            "count": queue_counts.get("short_onemap_walk_review", 0),
            "next_action": "treat very short OneMap walks as a separate percent-delta review bucket",
        },
    ]
    unresolved_count = 0
    for item in priority_order:
        count = item.get("count")
        if isinstance(count, int):
            unresolved_count += count
    return {
        "input_rows": payload.get("inputs", {}).get("input_rows"),
        "queue_counts": queue_counts,
        "strict_missing_bus_connector_priority_count": priority_queue_count(typed_queues),
        "overpermissive_path_summary": overpermissive_path_summary(typed_queues),
        "priority_order": priority_order,
        "unresolved_review_assignments": unresolved_count,
        "notes": [
            "Queues are review assignments, not mutually exclusive postal counts.",
            "This report uses cached OneMap validation and local replay artifacts only.",
            "Do not mark the OneMap launch gate passed until the 2,000-postal evaluator passes.",
        ],
    }


def numeric_source_flag(row: dict[str, Any], key: str) -> float:
    flags = row.get("source_flags")
    if not isinstance(flags, dict):
        return 0.0
    try:
        return float(flags.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def overpermissive_path_summary(queues: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rows = queues.get("possible_overpermissive_project_path", [])

    def has_direct_fallback(row: dict[str, Any]) -> bool:
        return (
            bool(row.get("direct_bus_fallback_reason"))
            or numeric_source_flag(row, "best_direct_bus_fallback_m") > 0
        )

    def has_endpoint_connector(row: dict[str, Any]) -> bool:
        return numeric_source_flag(row, "best_bus_stop_access_connector_m") > 0

    def has_hdb_or_bridge(row: dict[str, Any]) -> bool:
        return (
            numeric_source_flag(row, "best_inferred_hdb_m") > 0
            or numeric_source_flag(row, "best_bridge_underpass_m") > 0
        )

    def has_unknown_dominant(row: dict[str, Any]) -> bool:
        return numeric_source_flag(row, "best_unknown_source_m") >= 100.0

    route_sanity = Counter(
        str(row.get("current_route_vs_validation_direct_sanity") or "unknown") for row in rows
    )
    category_counts = {
        "current_direct_bus_fallback": sum(has_direct_fallback(row) for row in rows),
        "endpoint_connector_present": sum(has_endpoint_connector(row) for row in rows),
        "hdb_or_bridge_present": sum(has_hdb_or_bridge(row) for row in rows),
        "unknown_source_ge_100m": sum(has_unknown_dominant(row) for row in rows),
        "current_route_shorter_than_validation_direct": sum(
            str(row.get("current_route_vs_validation_direct_sanity") or "")
            in {
                "current_route_materially_shorter_than_validation_direct",
                "current_route_slightly_shorter_than_validation_direct",
            }
            for row in rows
        ),
    }
    top_rows = sorted(
        rows,
        key=lambda row: float(row.get("old_abs_pct_delta") or 0.0),
        reverse=True,
    )[:10]
    return {
        "count": len(rows),
        "category_counts": category_counts,
        "route_sanity_counts": dict(sorted(route_sanity.items())),
        "top_review_postals": [
            {
                "postal": row.get("postal"),
                "abs_pct_delta": row.get("old_abs_pct_delta"),
                "best_node": row.get("new_best_name"),
                "validation_onemap_walk_m": row.get("validation_onemap_walk_m"),
                "current_shortest_m": row.get("new_best_shortest_m"),
                "direct_bus_fallback_reason": row.get("direct_bus_fallback_reason"),
                "unknown_source_m": numeric_source_flag(row, "best_unknown_source_m"),
                "hdb_m": numeric_source_flag(row, "best_inferred_hdb_m"),
                "bridge_m": numeric_source_flag(row, "best_bridge_underpass_m"),
                "endpoint_connector_m": numeric_source_flag(
                    row, "best_bus_stop_access_connector_m"
                ),
            }
            for row in top_rows
        ],
        "recommended_next_actions": [
            "Treat direct-bus fallback rows as partial-route QA, not proof of a valid shorter walk.",
            "Inspect unknown-source >=100 m rows for barriers, private access, missing crossings, or OneMap detours.",
            "Audit HDB/bridge rows against source geometry before weakening connector guards.",
        ],
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
    parser.add_argument(
        "--missing-bus-priority-geojson-output",
        type=Path,
        default=DEFAULT_MISSING_BUS_PRIORITY_GEOJSON_OUTPUT,
    )
    parser.add_argument(
        "--overpermissive-priority-geojson-output",
        type=Path,
        default=DEFAULT_OVERPERMISSIVE_PRIORITY_GEOJSON_OUTPUT,
    )
    parser.add_argument(
        "--validation-subset-priority-geojson-output",
        type=Path,
        default=DEFAULT_VALIDATION_SUBSET_PRIORITY_GEOJSON_OUTPUT,
    )
    parser.add_argument(
        "--validation-subset-priority-subset",
        default="endpoint_connector",
        choices=[
            "all_valid_cached",
            "endpoint_connector",
            "graph_routed_bus_stop",
            "graph_routed_mrt_lrt",
            "graph_routed_without_endpoint_connector",
            "non_tiny_onemap_walk_gt_20m",
            "plausible_onemap_distance",
        ],
    )
    parser.add_argument("--validation-subset-priority-limit", type=int, default=50)
    parser.add_argument("--summary-output", type=Path, default=None)
    args = parser.parse_args()

    payload = build_triage_queues(
        longer_profile_path=args.longer_profile,
        shorter_profile_path=args.shorter_profile,
        validation_report_path=args.validation_report,
    )
    write_json(args.output, payload)
    write_json(args.geojson_output, triage_geojson(payload["queues"]))
    write_json(
        args.missing_bus_priority_geojson_output,
        missing_bus_connector_priority_geojson(payload["queues"]),
    )
    write_json(
        args.overpermissive_priority_geojson_output,
        overpermissive_priority_geojson(payload["queues"]),
    )
    validation_report = read_json(args.validation_report)
    validation_subset_summary = validation_subset_priority_summary(
        validation_report,
        subset_name=args.validation_subset_priority_subset,
        limit=10,
    )
    write_json(
        args.validation_subset_priority_geojson_output,
        validation_subset_priority_geojson(
            validation_report,
            subset_name=args.validation_subset_priority_subset,
            limit=args.validation_subset_priority_limit,
        ),
    )
    summary = validation_failure_summary(payload)
    summary["validation_subset_priority_summary"] = validation_subset_summary
    if args.summary_output is not None:
        write_json(args.summary_output, summary)
    printable = {key: value for key, value in payload.items() if key != "queues"}
    printable["validation_failure_summary"] = summary
    printable["geojson_output"] = display_path(args.geojson_output)
    printable["missing_bus_priority_geojson_output"] = display_path(
        args.missing_bus_priority_geojson_output
    )
    printable["overpermissive_priority_geojson_output"] = display_path(
        args.overpermissive_priority_geojson_output
    )
    printable["validation_subset_priority_geojson_output"] = display_path(
        args.validation_subset_priority_geojson_output
    )
    printable["summary_output"] = display_path(args.summary_output) if args.summary_output else None
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
