from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer

from pipeline.scoring_integration import (
    CandidateNode,
    ScoringContext,
    bus_route_should_use_direct_fallback,
    load_postal_universe_points,
    load_scoring_context,
    nearest_graph_node,
    score_postal_row,
    select_bus_stop_candidates,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIORITY_GEOJSON = (
    PROJECT_ROOT / "qa" / "onemap_missing_bus_connector_priority_20260802.geojson"
)
DEFAULT_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "qa" / "bus_connector_diagnostics_priority_20260802.json"
DEFAULT_GEOJSON_OUTPUT = PROJECT_ROOT / "qa" / "bus_connector_diagnostics_priority_20260802.geojson"
DEFAULT_NETWORK = PROJECT_ROOT / "processed" / "network_island.parquet"


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


def normalize_stop_name(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().casefold())


def stop_names_match(left: Any, right: Any) -> bool:
    return normalize_stop_name(left) == normalize_stop_name(right)


def feature_properties(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties")
    return properties if isinstance(properties, dict) else {}


def first_property(props: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = props.get(name)
        if value is not None:
            return value
    return None


def feature_list(collection: Any, path: Path) -> list[dict[str, Any]]:
    raw_features = collection.get("features") if isinstance(collection, dict) else None
    if not isinstance(raw_features, list):
        raise TypeError(f"expected FeatureCollection in {path}")
    return [feature for feature in raw_features if isinstance(feature, dict)]


def component_for_node(context: ScoringContext, node: tuple[float, float]) -> int | None:
    index = context.routing_graph.node_map.get(node)
    if index is None:
        return None
    return int(context.routing_graph.component_membership[index])


def choose_target_bus_candidate(
    candidates: list[CandidateNode],
    *,
    current_name: str | None,
    validation_name: str | None,
    validation_end_xy: tuple[float, float] | None,
) -> tuple[CandidateNode | None, str]:
    if not candidates:
        return None, "no_bus_candidates"
    names = [name for name in [current_name, validation_name] if normalize_stop_name(name)]
    matched = [
        candidate
        for candidate in candidates
        if any(stop_names_match(candidate.name, name) for name in names)
    ]
    pool = matched or candidates

    def sort_key(candidate: CandidateNode) -> tuple[float, float, str]:
        if validation_end_xy is None or candidate.point_xy is None:
            end_distance_m = candidate.straight_line_m
        else:
            end_distance_m = float(
                np.hypot(
                    float(candidate.point_xy[0]) - validation_end_xy[0],
                    float(candidate.point_xy[1]) - validation_end_xy[1],
                )
            )
        return (end_distance_m, candidate.straight_line_m, candidate.name)

    method = "matched_target_name" if matched else "nearest_candidate_to_validation_end"
    return min(pool, key=sort_key), method


def route_to_destination(
    context: ScoringContext,
    origin_node: tuple[float, float],
    destination: tuple[float, float],
) -> dict[str, Any] | None:
    routes = context.routing_graph.route(
        {origin_node: [destination]},
        float(context.params["shelter_lambda"]),
        float(context.params["detour_budget"]),
        include_geometry=False,
    )
    return routes[0] if routes else None


def alternate_snap_routes(
    context: ScoringContext,
    *,
    origin_node: tuple[float, float],
    stop_xy: tuple[float, float] | None,
    search_m: float,
    max_candidates: int,
) -> list[dict[str, Any]]:
    if stop_xy is None:
        return []
    stop_array = np.asarray(stop_xy, dtype=float)
    deltas = context.node_xy - stop_array
    distances = np.sqrt(np.einsum("ij,ij->i", deltas, deltas))
    indices = np.flatnonzero(distances <= search_m)
    if len(indices) == 0:
        return []

    ordered = sorted((float(distances[index]), int(index)) for index in indices)[:max_candidates]
    destinations = [context.nodes[index] for _, index in ordered]
    route_results = context.routing_graph.route(
        {origin_node: destinations},
        float(context.params["shelter_lambda"]),
        float(context.params["detour_budget"]),
        include_geometry=False,
    )
    by_destination = {result["destination"]: result for result in route_results}
    rows: list[dict[str, Any]] = []
    for snap_m, index in ordered:
        node = context.nodes[index]
        result = by_destination.get(node)
        if result is None:
            continue
        routed_m = float(result["shortest_length_m"])
        rows.append(
            {
                "graph_node": [round(float(node[0]), 3), round(float(node[1]), 3)],
                "snap_m": round(snap_m, 1),
                "component": component_for_node(context, node),
                "route_m": round(routed_m, 1),
                "route_plus_snap_m": round(routed_m + snap_m, 1),
            }
        )
    return sorted(rows, key=lambda row: float(row["route_plus_snap_m"]))


def diagnostic_class(row: dict[str, Any]) -> str:
    if row["target_match_method"] == "no_bus_candidates":
        return "no_bus_candidates"
    if row.get("same_validation_and_current_stop_name") and score_recovers_target_bus_stop(row):
        return "scorer_recovered_target_bus_stop"
    if not row["same_validation_and_current_stop_name"]:
        return "changed_stop_between_validation_and_replay"
    if row["current_graph_route_state"] == "routable":
        return "current_routable"
    if row.get("best_alternate_snap") is not None:
        return "alternate_bus_snap_candidate"
    if row["current_graph_route_state"] == "disconnected":
        return "bus_stop_graph_disconnected"
    return str(row["current_graph_route_state"])


def numeric(row: dict[str, Any], key: str) -> float | None:
    try:
        value = row.get(key)
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def nested_numeric(row: dict[str, Any], outer_key: str, inner_key: str) -> float | None:
    nested = row.get(outer_key)
    if not isinstance(nested, dict):
        return None
    try:
        value = nested.get(inner_key)
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def within_onemap_threshold(route_m: float | None, onemap_m: float | None) -> bool:
    if route_m is None or onemap_m is None or onemap_m <= 0:
        return False
    return abs(route_m - onemap_m) / onemap_m * 100 <= 25.0


def compact_action_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "postal": row.get("postal"),
        "diagnostic_class": row.get("diagnostic_class"),
        "direction": row.get("old_direction"),
        "target_bus_stop_name": row.get("target_bus_stop_name"),
        "onemap_walk_m": row.get("old_onemap_walk_m"),
        "validation_project_m": row.get("new_best_shortest_m"),
        "current_score_best_routed_m": row.get("current_score_best_routed_m"),
        "current_score_routing_type": row.get("current_score_routing_type"),
        "current_graph_route_m": row.get("current_graph_route_m"),
        "best_alternate_snap_route_plus_snap_m": nested_numeric(
            row, "best_alternate_snap", "route_plus_snap_m"
        ),
    }


def diagnostic_action_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    class_counts = Counter(str(row.get("diagnostic_class")) for row in rows)
    local_rescore_recovered = [
        row for row in rows if row.get("diagnostic_class") == "scorer_recovered_target_bus_stop"
    ]
    alternate_snap_or_disconnected = [
        row
        for row in rows
        if row.get("diagnostic_class")
        in {"alternate_bus_snap_candidate", "bus_stop_graph_disconnected"}
    ]
    current_routable = [row for row in rows if row.get("diagnostic_class") == "current_routable"]

    def current_score_within_threshold(row: dict[str, Any]) -> bool:
        return within_onemap_threshold(
            numeric(row, "current_score_best_routed_m"),
            numeric(row, "old_onemap_walk_m"),
        )

    def alternate_snap_within_threshold(row: dict[str, Any]) -> bool:
        return within_onemap_threshold(
            nested_numeric(row, "best_alternate_snap", "route_plus_snap_m"),
            numeric(row, "old_onemap_walk_m"),
        )

    return {
        "threshold": "within 25% of OneMap walk distance",
        "needs_rescore_candidate_count": len(local_rescore_recovered),
        "needs_bus_snap_or_connector_model_fix_count": len(alternate_snap_or_disconnected),
        "needs_current_routable_route_review_count": len(current_routable),
        "current_score_within_threshold_count": sum(
            current_score_within_threshold(row) for row in rows
        ),
        "alternate_snap_within_threshold_count": sum(
            alternate_snap_within_threshold(row) for row in rows
        ),
        "class_counts": dict(sorted(class_counts.items())),
        "top_needs_rescore_candidates": [
            compact_action_row(row) for row in local_rescore_recovered[:10]
        ],
        "top_bus_snap_or_connector_model_fix_candidates": [
            compact_action_row(row) for row in alternate_snap_or_disconnected[:10]
        ],
        "top_current_routable_route_review_candidates": [
            compact_action_row(row) for row in current_routable[:10]
        ],
        "recommended_next_actions": [
            "Refresh a targeted score bundle for recovered rows before using them as active validation failures.",
            "Treat alternate-snap rows as bus stop endpoint geometry QA; do not relax trust thresholds globally.",
            "Review current-routable rows for missing pedestrian connectors, barriers, or OneMap endpoint differences.",
        ],
    }


def score_recovers_target_bus_stop(row: dict[str, Any]) -> bool:
    if not stop_names_match(row.get("target_bus_stop_name"), row.get("current_score_best_name")):
        return False
    if row.get("current_score_best_type") != "bus_stop":
        return False
    if row.get("current_score_state") != "SCORED":
        return False
    if row.get("current_score_routing_type") == "direct_bus_fallback_unrouted":
        return False
    try:
        return float(row.get("current_score_best_routed_m") or 0.0) > 0
    except (TypeError, ValueError):
        return False


def feature_endpoint_xy(
    feature: dict[str, Any], transformer: Transformer
) -> tuple[float, float] | None:
    geometry = feature.get("geometry")
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    try:
        end_lon = float(coordinates[-1][0])
        end_lat = float(coordinates[-1][1])
    except (TypeError, ValueError, IndexError):
        return None
    x, y = transformer.transform(end_lon, end_lat)
    return float(x), float(y)


def diagnose_feature(
    feature: dict[str, Any],
    postal_rows: Any,
    context: ScoringContext,
    transformer: Transformer,
    *,
    alternate_snap_search_m: float,
    alternate_snap_max_candidates: int,
) -> dict[str, Any]:
    props = feature_properties(feature)
    postal = str(props.get("postal") or "").zfill(6)
    rows = postal_rows[postal_rows["postal_code"].astype(str).str.zfill(6) == postal]
    if rows.empty:
        return {
            "postal": postal,
            "diagnostic_class": "missing_from_universe",
            "source_properties": props,
        }

    postal_row = rows.iloc[0]
    postal_point = postal_row.geometry
    origin_node, origin_snap_m = nearest_graph_node(postal_point, context.nodes, context.node_xy)
    validation_end_xy = feature_endpoint_xy(feature, transformer)
    bus_radius_m = float(context.params["bus_connectivity"]["straight_line_candidate_m"])
    bus_candidates = select_bus_stop_candidates(postal_point, context.bus_index, bus_radius_m)
    target_candidate, match_method = choose_target_bus_candidate(
        bus_candidates,
        current_name=first_property(props, "new_best_name", "best_node_name"),
        validation_name=first_property(props, "old_validation_best_node", "best_node_name"),
        validation_end_xy=validation_end_xy,
    )

    validation_name = first_property(props, "old_validation_best_node", "best_node_name")
    current_name = first_property(props, "new_best_name", "best_node_name")
    origin_component = component_for_node(context, origin_node)
    result: dict[str, Any] = {
        "postal": postal,
        "priority_rank": props.get("priority_rank"),
        "validation_area": first_property(props, "validation_area", "area"),
        "old_validation_best_node": validation_name,
        "new_best_name": current_name,
        "same_validation_and_current_stop_name": stop_names_match(validation_name, current_name),
        "target_match_method": match_method,
        "origin_snap_m": round(float(origin_snap_m), 1),
        "origin_component": origin_component,
        "bus_candidate_count": len(bus_candidates),
        "validation_direct_distance_m": first_property(
            props, "validation_direct_distance_m", "direct_distance_m"
        ),
        "new_best_shortest_m": first_property(props, "new_best_shortest_m", "project_shortest_m"),
        "old_onemap_walk_m": first_property(props, "old_onemap_walk_m", "onemap_walk_m"),
        "old_project_shortest_m": first_property(
            props, "old_project_shortest_m", "project_shortest_m"
        ),
        "old_abs_pct_delta": first_property(props, "old_abs_pct_delta", "abs_pct_delta"),
        "old_direction": first_property(props, "old_direction", "direction"),
        "validation_route_trust": props.get("route_trust"),
        "validation_routing_type": props.get("routing_type"),
        "validation_distance_sanity": props.get("distance_sanity"),
        "direct_bus_fallback_reason": props.get("direct_bus_fallback_reason"),
    }
    score_record = score_postal_row(
        postal_row,
        context.mrt_exits_gdf,
        context.edges_dict,
        context.routing_graph,
        context.nodes,
        context.node_xy,
        context.params,
        context.weights,
        context.crossing_counter,
        context.bus_index,
        include_geometry=False,
        network_path=context.network_path,
        postal_universe_path=context.postal_universe_path,
        base_provenance=context.base_provenance,
        data_as_of=context.data_as_of,
    )
    current_best = score_record.get("best_node")
    current_paths = score_record.get("paths")
    current_provenance = score_record.get("provenance")
    result.update(
        {
            "current_score_state": score_record.get("state"),
            "current_score_total": score_record.get("total"),
            "current_score_best_name": (
                current_best.get("name") if isinstance(current_best, dict) else None
            ),
            "current_score_best_type": (
                current_best.get("type") if isinstance(current_best, dict) else None
            ),
            "current_score_best_routed_m": (
                current_best.get("routed_m") if isinstance(current_best, dict) else None
            ),
            "current_score_routing_type": (
                current_paths.get("routing_type") if isinstance(current_paths, dict) else None
            ),
            "current_score_bus_stop_access_connector_m": (
                current_paths.get("bus_stop_access_connector_m")
                if isinstance(current_paths, dict)
                else None
            ),
            "current_score_direct_bus_fallback_reason": (
                (current_provenance.get("direct_bus_fallback") or {}).get("reason")
                if isinstance(current_provenance, dict)
                and isinstance(current_provenance.get("direct_bus_fallback"), dict)
                else None
            ),
        }
    )
    if target_candidate is None:
        result["current_graph_route_state"] = "no_target_candidate"
        result["diagnostic_class"] = diagnostic_class(result)
        return result

    target_component = component_for_node(context, target_candidate.graph_node)
    current_route = route_to_destination(context, origin_node, target_candidate.graph_node)
    if current_route is None:
        route_state = "disconnected"
        current_route_m = None
        should_fallback = None
    else:
        current_route_m = round(float(current_route["shortest_length_m"]), 1)
        should_fallback = bus_route_should_use_direct_fallback(
            target_candidate,
            current_route,
            context.params["bus_connectivity"],
        )
        route_state = "implausible_detour" if should_fallback else "routable"

    alternate_routes = alternate_snap_routes(
        context,
        origin_node=origin_node,
        stop_xy=target_candidate.point_xy,
        search_m=alternate_snap_search_m,
        max_candidates=alternate_snap_max_candidates,
    )
    best_alternate = alternate_routes[0] if alternate_routes else None
    result.update(
        {
            "target_bus_stop_code": target_candidate.exit_code,
            "target_bus_stop_name": target_candidate.name,
            "target_direct_m": round(float(target_candidate.straight_line_m), 1),
            "target_snap_m": round(float(target_candidate.snap_distance_m), 1),
            "target_component": target_component,
            "origin_component_reaches_target": origin_component == target_component,
            "current_graph_route_state": route_state,
            "current_graph_route_m": current_route_m,
            "current_graph_route_uses_direct_fallback": should_fallback,
            "alternate_snap_search_m": round(float(alternate_snap_search_m), 1),
            "alternate_snap_reachable_count": len(alternate_routes),
            "best_alternate_snap": best_alternate,
        }
    )
    result["diagnostic_class"] = diagnostic_class(result)
    return result


def build_diagnostics(
    *,
    priority_geojson_path: Path,
    postal_universe_path: Path,
    network_path: Path,
    alternate_snap_search_m: float,
    alternate_snap_max_candidates: int,
) -> dict[str, Any]:
    priority_geojson = read_json(priority_geojson_path)
    features = feature_list(priority_geojson, priority_geojson_path)
    postals = [
        str(feature_properties(feature).get("postal") or "").zfill(6) for feature in features
    ]
    context = load_scoring_context(
        network_path=network_path,
        postal_universe_path=postal_universe_path,
    )
    postal_rows = load_postal_universe_points(postal_universe_path, postal_codes=postals)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)
    rows = [
        diagnose_feature(
            feature,
            postal_rows,
            context,
            transformer,
            alternate_snap_search_m=alternate_snap_search_m,
            alternate_snap_max_candidates=alternate_snap_max_candidates,
        )
        for feature in features
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs": {
            "priority_geojson": display_path(priority_geojson_path),
            "postal_universe": display_path(postal_universe_path),
            "network": display_path(network_path),
            "feature_count": len(features),
            "alternate_snap_search_m": round(float(alternate_snap_search_m), 1),
            "alternate_snap_max_candidates": int(alternate_snap_max_candidates),
        },
        "summary": {
            "diagnostic_class_counts": dict(
                sorted(Counter(str(row.get("diagnostic_class")) for row in rows).items())
            ),
            "current_graph_route_state_counts": dict(
                sorted(Counter(str(row.get("current_graph_route_state")) for row in rows).items())
            ),
            "same_stop_name_counts": dict(
                sorted(
                    Counter(
                        str(row.get("same_validation_and_current_stop_name")) for row in rows
                    ).items()
                )
            ),
            "target_match_method_counts": dict(
                sorted(Counter(str(row.get("target_match_method")) for row in rows).items())
            ),
            "action_summary": diagnostic_action_summary(rows),
        },
        "rows": rows,
    }


def diagnostics_geojson(priority_geojson_path: Path, diagnostics: dict[str, Any]) -> dict[str, Any]:
    priority_geojson = read_json(priority_geojson_path)
    features = feature_list(priority_geojson, priority_geojson_path)
    rows = diagnostics.get("rows")
    diagnostic_rows = rows if isinstance(rows, list) else []
    by_postal: dict[str, dict[str, Any]] = {
        str(row.get("postal") or "").zfill(6): row
        for row in diagnostic_rows
        if isinstance(row, dict)
    }
    output_features: list[dict[str, Any]] = []
    for feature in features:
        props = feature_properties(feature)
        postal = str(props.get("postal") or "").zfill(6)
        diagnostic = by_postal.get(postal) or {}
        output_features.append(
            {
                "type": "Feature",
                "geometry": feature.get("geometry"),
                "properties": {**props, **diagnostic},
            }
        )
    return {"type": "FeatureCollection", "features": output_features}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose priority OneMap missing-bus connector candidates."
    )
    parser.add_argument("--priority-geojson", type=Path, default=DEFAULT_PRIORITY_GEOJSON)
    parser.add_argument("--postal-universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--geojson-output", type=Path, default=DEFAULT_GEOJSON_OUTPUT)
    parser.add_argument("--alternate-snap-search-m", type=float, default=50.0)
    parser.add_argument("--alternate-snap-max-candidates", type=int, default=24)
    args = parser.parse_args()

    diagnostics = build_diagnostics(
        priority_geojson_path=args.priority_geojson,
        postal_universe_path=args.postal_universe,
        network_path=args.network,
        alternate_snap_search_m=args.alternate_snap_search_m,
        alternate_snap_max_candidates=args.alternate_snap_max_candidates,
    )
    write_json(args.output, diagnostics)
    write_json(args.geojson_output, diagnostics_geojson(args.priority_geojson, diagnostics))
    printable = {key: value for key, value in diagnostics.items() if key != "rows"}
    printable["geojson_output"] = display_path(args.geojson_output)
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
