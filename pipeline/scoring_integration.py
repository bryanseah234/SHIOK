"""Integration layer that scores real routed postal-to-transit paths."""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, unary_union

from pipeline.bus import (
    BusConnectivityIndex,
    BusConnectivityResult,
    BusStopCandidate,
    combined_expected_wait_min,
)
from pipeline.routing import EDGE_METADATA_COLUMNS, RoutingGraph, prepare_edges_for_routing
from pipeline.scoring import (
    NO_TRANSIT_IN_RANGE,
    NOT_YET_SCORED,
    calculate_composite_score,
    score_bus_connectivity,
    score_crossing_friction,
    score_heat_comfort,
    score_rain_shelter,
    score_transit_access,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "processed"
RAW_DIR = PROJECT_ROOT / "raw"
PARAMS_PATH = PROJECT_ROOT / "pipeline" / "config" / "params.yaml"
WEIGHTS_PATH = PROJECT_ROOT / "pipeline" / "config" / "weights.yaml"
NETWORK_PATH = PROCESSED_DIR / "network_island.parquet"
GEOCODE_DB_PATH = RAW_DIR / "geocode_cache.db"
MANIFEST_PATH = RAW_DIR / "manifest.json"
SubscoreValue = float | str

LOW_TRUST_BUS_ROAD_HIGHWAYS = {
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "residential",
    "service",
    "unclassified",
}
PEDESTRIAN_EVIDENCE_HIGHWAYS = {
    "corridor",
    "crossing",
    "footway",
    "living_street",
    "path",
    "pedestrian",
    "platform",
    "steps",
}
PEDESTRIAN_FOOT_VALUES = {"yes", "designated", "official", "permissive"}


@dataclass(frozen=True)
class CandidateNode:
    node_type: str
    name: str
    station_name: str
    exit_code: str
    graph_node: tuple[float, float]
    straight_line_m: float
    snap_distance_m: float
    service_headways_min: dict[tuple[str, int], float] | None = None
    expected_wait_min: float | None = None
    point_xy: tuple[float, float] | None = None


@dataclass(frozen=True)
class ScoringContext:
    params: dict[str, Any]
    weights: dict[str, float]
    edges_dict: dict[str, list[Any]]
    routing_graph: RoutingGraph
    nodes: list[tuple[float, float]]
    node_xy: np.ndarray
    mrt_exits_gdf: gpd.GeoDataFrame
    crossing_counter: CrossingCounter
    bus_index: BusConnectivityIndex | None
    network_path: Path
    postal_universe_path: Path | None = None
    base_provenance: dict[str, Any] | None = None
    data_as_of: str | None = None


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data: Any = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise TypeError(f"expected YAML mapping in {path}")
    return cast(dict[str, Any], data)


def load_params_and_weights() -> tuple[dict[str, Any], dict[str, float]]:
    params = load_yaml(PARAMS_PATH)
    weights = load_yaml(WEIGHTS_PATH)["weights"]
    return params, weights


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {"generated_at": None, "sources": {}}
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, dict):
        raise TypeError(f"expected JSON object in {MANIFEST_PATH}")
    return cast(dict[str, Any], data)


def raw_file_from_manifest(source_key: str, filename: str) -> Path | None:
    manifest = load_manifest()
    source = manifest.get("sources", {}).get(source_key, {})
    sha = source.get("sha256")
    if isinstance(sha, str) and sha:
        path = RAW_DIR / sha / filename
        if path.is_file():
            return path

    matches = list(RAW_DIR.glob(f"*/{filename}"))
    return matches[0] if matches else None


def load_network_inputs(
    network_path: Path = NETWORK_PATH,
) -> tuple[pd.DataFrame, dict[str, list[Any]], list[tuple[float, float]], np.ndarray]:
    edges_df = prepare_edges_for_routing(pd.read_parquet(network_path))
    cols = ["u", "v", "length_m", "is_covered"]
    if "geometry" in edges_df.columns:
        cols.append("geometry")
    cols.extend(column for column in EDGE_METADATA_COLUMNS if column in edges_df.columns)

    nodes = (
        pd.concat([edges_df["u"], edges_df["v"]], ignore_index=True)
        .dropna()
        .drop_duplicates()
        .tolist()
    )
    node_xy = np.asarray(nodes, dtype=float)
    return edges_df, edges_df[cols].to_dict("list"), nodes, node_xy


def nearest_graph_node(
    point: Any, nodes: list[tuple[float, float]], node_xy: np.ndarray
) -> tuple[tuple[float, float], float]:
    xy = np.asarray([point.x, point.y], dtype=float)
    deltas = node_xy - xy
    squared = np.einsum("ij,ij->i", deltas, deltas)
    index = int(np.argmin(squared))
    return nodes[index], float(squared[index] ** 0.5)


def nearest_graph_node_in_components(
    point: Any,
    nodes: list[tuple[float, float]],
    node_xy: np.ndarray,
    routing_graph: RoutingGraph,
    allowed_components: set[int],
    max_distance_m: float,
) -> tuple[tuple[float, float], float] | None:
    if not allowed_components:
        return None

    allowed_indexes = [
        index
        for index, node in enumerate(nodes)
        if node in routing_graph.node_map
        and routing_graph.component_membership[routing_graph.node_map[node]] in allowed_components
    ]
    if not allowed_indexes:
        return None

    filtered_nodes = [nodes[index] for index in allowed_indexes]
    filtered_xy = node_xy[np.asarray(allowed_indexes, dtype=int)]
    xy = np.asarray([point.x, point.y], dtype=float)
    deltas = filtered_xy - xy
    squared = np.einsum("ij,ij->i", deltas, deltas)
    index = int(np.argmin(squared))
    distance_m = float(squared[index] ** 0.5)
    if distance_m > max_distance_m:
        return None
    return filtered_nodes[index], distance_m


def load_postal_points(
    postal_codes: list[str] | None = None,
    limit: int | None = None,
    db_path: Path = GEOCODE_DB_PATH,
) -> gpd.GeoDataFrame:
    if not db_path.is_file():
        raise FileNotFoundError(f"geocode cache not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        if postal_codes:
            placeholders = ",".join("?" for _ in postal_codes)
            sql = (
                "SELECT postal_code, lat, lon, response FROM postcodes "
                f"WHERE status='SUCCESS' AND postal_code IN ({placeholders})"
            )
            rows = pd.read_sql(sql, conn, params=postal_codes)
            order = {postal: index for index, postal in enumerate(postal_codes)}
            rows["order"] = rows["postal_code"].map(order)
            rows = rows.sort_values("order").drop(columns=["order"])
        else:
            sql = "SELECT postal_code, lat, lon, response FROM postcodes WHERE status='SUCCESS'"
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            rows = pd.read_sql(sql, conn)
    finally:
        conn.close()

    gdf = gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy(rows["lon"], rows["lat"]),
        crs="EPSG:4326",
    )
    return gdf.to_crs("EPSG:3414")


def load_postal_universe_points(
    universe_path: Path,
    postal_codes: list[str] | None = None,
    limit: int | None = None,
) -> gpd.GeoDataFrame:
    if not universe_path.is_file():
        raise FileNotFoundError(f"postal universe not found: {universe_path}")

    rows = pd.read_parquet(universe_path)
    rows["postal_code"] = rows["postal_code"].astype(str).str.zfill(6)
    rows = rows[rows["status"] == "READY_TO_SCORE"].copy()

    if postal_codes:
        normalized = [str(postal).zfill(6) for postal in postal_codes]
        order = {postal: index for index, postal in enumerate(normalized)}
        rows = rows[rows["postal_code"].isin(order)].copy()
        rows["order"] = rows["postal_code"].map(order)
        rows = rows.sort_values("order", kind="stable").drop(columns=["order"])
    elif limit is not None:
        rows = rows.sort_values("postal_code", kind="stable").head(int(limit))

    rows = rows.dropna(subset=["x", "y"]).copy()
    return gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy(rows["x"], rows["y"]),
        crs="EPSG:3414",
    )


def load_mrt_exits() -> gpd.GeoDataFrame:
    path = raw_file_from_manifest("mrt_lrt_exits", "mrt_lrt_exits.geojson")
    if path is None:
        raise FileNotFoundError("MRT/LRT exits file not found under raw/")
    return gpd.read_file(path).to_crs("EPSG:3414")


def select_mrt_exit_candidates(
    postal_point: Any,
    mrt_exits_gdf: gpd.GeoDataFrame,
    nodes: list[tuple[float, float]],
    node_xy: np.ndarray,
    second_station_ratio: float = 1.2,
) -> list[CandidateNode]:
    station_distances: list[tuple[str, float]] = []
    for station_name, group in mrt_exits_gdf.groupby("STATION_NA"):
        station_distances.append((station_name, float(group.geometry.distance(postal_point).min())))

    station_distances.sort(key=lambda item: (item[1], item[0]))
    if not station_distances:
        return []

    selected_stations = [station_distances[0][0]]
    if len(station_distances) > 1:
        first_distance = station_distances[0][1]
        second_station, second_distance = station_distances[1]
        if second_distance <= first_distance * second_station_ratio:
            selected_stations.append(second_station)

    candidates: list[CandidateNode] = []
    for station_name in selected_stations:
        exits = mrt_exits_gdf[mrt_exits_gdf["STATION_NA"] == station_name].copy()
        exits = exits.sort_values(["EXIT_CODE", "OBJECTID"], kind="stable")
        for _, row in exits.iterrows():
            graph_node, snap_distance = nearest_graph_node(row.geometry, nodes, node_xy)
            exit_code = str(row.get("EXIT_CODE", "")).strip()
            name = f"{station_name} {exit_code}".strip()
            candidates.append(
                CandidateNode(
                    node_type="mrt_lrt_exit",
                    name=name,
                    station_name=station_name,
                    exit_code=exit_code,
                    graph_node=graph_node,
                    straight_line_m=float(row.geometry.distance(postal_point)),
                    snap_distance_m=snap_distance,
                    point_xy=(float(row.geometry.x), float(row.geometry.y)),
                )
            )
    return candidates


def bus_stop_candidate_name(candidate: BusStopCandidate) -> str:
    description = candidate.description.strip()
    if description:
        return description
    if candidate.bus_stop_code:
        return f"Bus stop {candidate.bus_stop_code}"
    return "Bus stop"


def select_bus_stop_candidates(
    postal_point: Any,
    bus_index: BusConnectivityIndex | None,
    straight_line_radius_m: float,
) -> list[CandidateNode]:
    if bus_index is None:
        return []

    candidates: list[CandidateNode] = []
    for stop in bus_index.nearby_stop_candidates(postal_point, straight_line_radius_m):
        expected_wait = combined_expected_wait_min(list(stop.service_headways_min.values()))
        if expected_wait is None:
            continue
        name = bus_stop_candidate_name(stop)
        candidates.append(
            CandidateNode(
                node_type="bus_stop",
                name=name,
                station_name=name,
                exit_code=stop.bus_stop_code,
                graph_node=stop.graph_node,
                straight_line_m=stop.straight_line_m,
                snap_distance_m=stop.snap_distance_m,
                service_headways_min=stop.service_headways_min,
                expected_wait_min=expected_wait,
                point_xy=stop.point_xy,
            )
        )

    return sorted(
        candidates,
        key=lambda item: (
            item.straight_line_m,
            item.expected_wait_min if item.expected_wait_min is not None else float("inf"),
            item.exit_code,
            item.name,
        ),
    )


def bus_connectivity_from_routed_candidates(
    route_results: list[dict[str, Any]],
    candidate_by_destination: dict[tuple[float, float], list[CandidateNode]],
    routed_max_m: float,
    straight_line_stop_count: int = 0,
) -> BusConnectivityResult:
    qualifying_headways: dict[tuple[str, int], float] = {}
    routed_stop_count = 0
    routed_distances: list[float] = []

    for route_result in route_results:
        destination = route_result["destination"]
        routed_m = float(route_result["shortest_length_m"])
        if routed_m > routed_max_m:
            continue
        bus_candidates = [
            candidate
            for candidate in candidate_by_destination.get(destination, [])
            if candidate.node_type == "bus_stop"
        ]
        if not bus_candidates:
            continue

        routed_distances.append(routed_m)
        routed_stop_count += len(bus_candidates)
        for candidate in bus_candidates:
            for service_key, headway in (candidate.service_headways_min or {}).items():
                existing = qualifying_headways.get(service_key)
                qualifying_headways[service_key] = (
                    headway if existing is None else min(existing, headway)
                )

    return BusConnectivityResult(
        expected_wait_min=combined_expected_wait_min(list(qualifying_headways.values())),
        routed_stop_count=routed_stop_count,
        service_count=len(qualifying_headways),
        nearest_routed_m=min(routed_distances) if routed_distances else None,
        straight_line_stop_count=straight_line_stop_count,
    )


def count_dbscan_clusters(points_xy: np.ndarray, eps_m: float, min_samples: int) -> int:
    if len(points_xy) < min_samples:
        return 0

    visited = np.zeros(len(points_xy), dtype=bool)
    assigned = np.zeros(len(points_xy), dtype=bool)
    cluster_count = 0

    def neighbours(index: int) -> np.ndarray:
        distances = np.linalg.norm(points_xy - points_xy[index], axis=1)
        return np.flatnonzero(distances <= eps_m)

    for index in range(len(points_xy)):
        if visited[index]:
            continue
        visited[index] = True
        seeds = neighbours(index)
        if len(seeds) < min_samples:
            continue

        cluster_count += 1
        queue = list(seeds)
        assigned[index] = True
        while queue:
            current = queue.pop()
            if not visited[current]:
                visited[current] = True
                current_neighbours = neighbours(current)
                if len(current_neighbours) >= min_samples:
                    queue.extend(int(item) for item in current_neighbours)
            assigned[current] = True

    return cluster_count


class CrossingCounter:
    def __init__(
        self,
        signals_gdf: gpd.GeoDataFrame | None,
        grade_separated_gdf: gpd.GeoDataFrame | None,
        eps_m: float,
        min_samples: int,
    ) -> None:
        self.signals_gdf = signals_gdf
        self.grade_separated_union = (
            unary_union(grade_separated_gdf.geometry)
            if grade_separated_gdf is not None and not grade_separated_gdf.empty
            else None
        )
        self.eps_m = eps_m
        self.min_samples = min_samples

    @property
    def available(self) -> bool:
        return self.signals_gdf is not None

    @classmethod
    def from_raw_data(cls, params: dict[str, Any]) -> CrossingCounter:
        crossing_params = params.get("crossing_friction", {})
        eps_m = float(crossing_params.get("dbscan_eps_m", 20.0))
        min_samples = int(crossing_params.get("dbscan_min_samples", 2))

        signals = None
        signals_zip = raw_file_from_manifest("traffic_signals", "traffic_signals.zip")
        if signals_zip is not None:
            uri = f"zip://{signals_zip}!TrafficLight_Mar2026/TrafficSignalAspect.shp"
            signals = gpd.read_file(uri).to_crs("EPSG:3414")
            desc = signals["TYP_CD_DES"].fillna("").str.lower()
            pedestrian = desc.str.contains("pedestrian", regex=False)
            if "LVL_NUM_DE" in signals.columns:
                level = signals["LVL_NUM_DE"].fillna("").str.lower()
                at_grade = level.str.contains("at-grade", regex=False)
            else:
                at_grade = pd.Series(True, index=signals.index)
            signals = signals[pedestrian & at_grade & signals.geometry.notna()].copy()

        grade_separated = None
        bridge_zip = raw_file_from_manifest(
            "overhead_bridge_underpass", "overhead_bridge_underpass.zip"
        )
        if bridge_zip is not None:
            uri = (
                f"zip://{bridge_zip}!"
                "PedestrainOverheadbridge_UnderPass_Mar2026/"
                "PedestrainOverheadbridge.shp"
            )
            grade_separated = gpd.read_file(uri).to_crs("EPSG:3414")

        return cls(signals, grade_separated, eps_m, min_samples)

    def count_for_route(self, route_geometry: Any) -> int | None:
        if not self.available:
            return None
        signals_gdf = self.signals_gdf
        if signals_gdf is None:
            return None
        if route_geometry is None or route_geometry.is_empty:
            return 0

        route_buffer = route_geometry.buffer(self.eps_m)
        minx, miny, maxx, maxy = route_buffer.bounds
        candidates = signals_gdf.cx[minx:maxx, miny:maxy]
        if candidates.empty:
            return 0

        candidates = candidates[candidates.geometry.within(route_buffer)].copy()
        if self.grade_separated_union is not None and not candidates.empty:
            exempt_area = self.grade_separated_union.buffer(2.0)
            candidates = candidates[~candidates.geometry.within(exempt_area)]
        if candidates.empty:
            return 0

        points_xy = np.asarray([(geom.x, geom.y) for geom in candidates.geometry], dtype=float)
        return count_dbscan_clusters(points_xy, self.eps_m, self.min_samples)


def exposure_gaps_from_path_edges(path_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transformer = Transformer.from_crs("EPSG:3414", "EPSG:4326", always_xy=True)
    gaps: list[dict[str, Any]] = []
    current_edges: list[dict[str, Any]] = []

    def flush() -> None:
        if not current_edges:
            return
        length_m = sum(float(edge["length_m"]) for edge in current_edges)
        geometries = [
            edge["geometry"]
            for edge in current_edges
            if edge.get("geometry") is not None and not edge["geometry"].is_empty
        ]
        gap: dict[str, Any] = {"len_m": round(length_m, 1)}
        if geometries:
            merged = (
                linemerge(MultiLineString(geometries)) if len(geometries) > 1 else geometries[0]
            )
            centroid = merged.centroid
            lon, lat = transformer.transform(centroid.x, centroid.y)
            gap["location"] = {"lat": round(lat, 6), "lon": round(lon, 6)}
            gap["label"] = f"exposed gap near {lat:.5f}, {lon:.5f}"
        else:
            gap["label"] = "exposed gap"
        gaps.append(gap)
        current_edges.clear()

    for edge in path_edges:
        if not edge.get("is_covered") and float(edge.get("length_m", 0.0)) > 0:
            current_edges.append(edge)
        else:
            flush()
    flush()
    return gaps


def round_nullable_score(value: Any) -> float | None:
    if value is None or value in {NO_TRANSIT_IN_RANGE, NOT_YET_SCORED}:
        return None
    return round(float(value), 1)


def heat_comfort_evidence_m(route_result: dict[str, Any], params: dict[str, Any]) -> float:
    length_m = float(route_result["length_m"])
    covered_m = float(route_result["covered_m"])
    shade_m = float(route_result.get("shade_m") or 0.0)
    shade_weight = float(params.get("heat_comfort", {}).get("shade_proxy_weight", 0.0))
    return min(length_m, covered_m + shade_m * shade_weight)


def score_candidate_route(
    candidate: CandidateNode,
    route_result: dict[str, Any],
    params: dict[str, Any],
    weights: dict[str, float],
    crossing_count: int | None,
    bus_expected_wait_min: float | None = None,
    bus_data_available: bool = False,
    include_geometry: bool = False,
) -> dict[str, Any]:
    access: SubscoreValue = score_transit_access(
        float(route_result["shortest_length_m"]),
        params["transit_access"],
        is_bus_interchange=False,
    )
    bus: SubscoreValue = (
        score_bus_connectivity(bus_expected_wait_min, params["bus_connectivity"])
        if bus_data_available
        else NOT_YET_SCORED
    )
    rain: SubscoreValue = score_rain_shelter(
        float(route_result["covered_m"]), float(route_result["length_m"])
    )
    heat_evidence_m = heat_comfort_evidence_m(route_result, params)
    heat: SubscoreValue = score_heat_comfort(heat_evidence_m, float(route_result["length_m"]))
    crossing: SubscoreValue = (
        score_crossing_friction(crossing_count, params["crossing_friction"])
        if crossing_count is not None
        else NOT_YET_SCORED
    )

    subscore_values: dict[str, SubscoreValue] = {
        "transit_access": access,
        "bus_connectivity": bus,
        "rain_shelter": rain,
        "heat_comfort": heat,
        "crossing_friction": crossing,
    }
    composite = calculate_composite_score(
        subscore_values,
        weights,
    )

    shortest_m = float(route_result["shortest_length_m"])
    sheltered_m = float(route_result["length_m"])
    detour_pct = ((sheltered_m / shortest_m) - 1.0) * 100.0 if shortest_m > 0 else 0.0

    candidate_score: dict[str, Any] = {
        "candidate": candidate,
        "total": composite,
        "subscores": {
            "access": round_nullable_score(access),
            "bus": (
                0.0
                if bus_data_available and bus == NO_TRANSIT_IN_RANGE
                else round_nullable_score(bus) if bus_data_available else None
            ),
            "rain": round_nullable_score(rain),
            "heat": round_nullable_score(heat),
            "crossing": round_nullable_score(crossing),
        },
        "best_node": {
            "type": candidate.node_type,
            "name": candidate.name,
            "routed_m": round(shortest_m, 1),
            "station": candidate.station_name,
            "exit": candidate.exit_code,
            "straight_line_m": round(candidate.straight_line_m, 1),
            "snap_distance_m": round(candidate.snap_distance_m, 1),
        },
        "paths": {
            "shortest_m": round(shortest_m, 1),
            "sheltered_m": round(sheltered_m, 1),
            "detour_pct": round(detour_pct, 1),
            "routing_type": route_result["routing_type"],
            "covered_m": round(float(route_result["covered_m"]), 1),
            "covered_ratio": round(float(route_result["covered_ratio"]), 3),
            "shade_m": round(float(route_result.get("shade_m") or 0.0), 1),
            "shade_ratio": round(float(route_result.get("shade_ratio") or 0.0), 3),
            "heat_comfort_m": round(float(heat_evidence_m), 1),
            "heat_comfort_ratio": round(
                (
                    float(heat_evidence_m) / float(route_result["length_m"])
                    if float(route_result["length_m"]) > 0
                    else 0.0
                ),
                3,
            ),
            "shortest_covered_ratio": round(float(route_result["shortest_covered_ratio"]), 3),
            "shortest_shade_ratio": round(
                float(route_result.get("shortest_shade_ratio") or 0.0), 3
            ),
            "origin_snap_connector_m": round(
                float(route_result.get("origin_graph_snap_connector_m") or 0.0), 1
            ),
            "destination_snap_connector_m": round(
                float(route_result.get("destination_graph_snap_connector_m") or 0.0), 1
            ),
            "endpoint_snap_connector_m": round(
                float(route_result.get("endpoint_snap_connector_m") or 0.0), 1
            ),
        },
        "exposure_gaps": exposure_gaps_from_path_edges(route_result.get("path_edges", [])),
        "crossing_count": crossing_count,
    }
    if include_geometry:
        candidate_score["_geometry"] = {
            "shortest": route_result.get("shortest_geometry"),
            "sheltered": route_result.get("geometry"),
            "shortest_path_edges": route_result.get("shortest_path_edges", []),
            "sheltered_path_edges": route_result.get(
                "sheltered_path_edges", route_result.get("path_edges", [])
            ),
            "exposure_gap_edges": route_result.get("path_edges", []),
        }
    return candidate_score


def direct_bus_fallback_candidate_scores(
    candidates: list[CandidateNode],
    postal_point: Any,
    params: dict[str, Any],
    weights: dict[str, float],
    include_geometry: bool = False,
) -> list[dict[str, Any]]:
    """Score nearby buses as partial evidence when graph routing cannot reach transit."""
    fallback_scores: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.node_type != "bus_stop":
            continue
        access = score_transit_access(
            float(candidate.straight_line_m),
            params["transit_access"],
            is_bus_interchange=False,
        )
        bus = score_bus_connectivity(candidate.expected_wait_min, params["bus_connectivity"])
        subscore_values: dict[str, SubscoreValue] = {
            "transit_access": access,
            "bus_connectivity": bus,
            "rain_shelter": NOT_YET_SCORED,
            "heat_comfort": NOT_YET_SCORED,
            "crossing_friction": NOT_YET_SCORED,
        }
        composite = calculate_composite_score(subscore_values, weights)
        if not isinstance(composite, int | float):
            continue

        direct_m = float(candidate.straight_line_m)
        stop_xy = candidate.point_xy or candidate.graph_node
        line = LineString([(float(postal_point.x), float(postal_point.y)), stop_xy])
        direct_edge = {
            "length_m": direct_m,
            "is_covered": False,
            "geometry": line,
            "source_layer": "direct_bus_fallback",
            "synth_class": "unrouted_straight_line",
            "confidence": "partial_unrouted",
        }
        score: dict[str, Any] = {
            "candidate": candidate,
            "total": composite,
            "subscores": {
                "access": round_nullable_score(access),
                "bus": round_nullable_score(bus),
                "rain": None,
                "heat": None,
                "crossing": None,
            },
            "best_node": {
                "type": candidate.node_type,
                "name": candidate.name,
                "routed_m": None,
                "station": candidate.station_name,
                "exit": candidate.exit_code,
                "straight_line_m": round(direct_m, 1),
                "snap_distance_m": round(candidate.snap_distance_m, 1),
            },
            "paths": {
                "shortest_m": round(direct_m, 1),
                "sheltered_m": round(direct_m, 1),
                "detour_pct": 0.0,
                "routing_type": "direct_bus_fallback_unrouted",
            },
            "exposure_gaps": [],
            "crossing_count": None,
        }
        if include_geometry:
            score["_geometry"] = {
                "shortest": line,
                "sheltered": line,
                "shortest_path_edges": [direct_edge],
                "sheltered_path_edges": [direct_edge],
                "exposure_gap_edges": [],
            }
        fallback_scores.append(score)
    return fallback_scores


def bus_route_should_use_direct_fallback(
    candidate: CandidateNode,
    route_result: dict[str, Any],
    bus_params: dict[str, Any],
) -> bool:
    return bus_route_direct_fallback_reason(candidate, route_result, bus_params) is not None


def bus_route_direct_fallback_reason(
    candidate: CandidateNode,
    route_result: dict[str, Any],
    bus_params: dict[str, Any],
) -> str | None:
    if candidate.node_type != "bus_stop":
        return None
    direct_m = float(candidate.straight_line_m)
    if direct_m <= 0:
        return None
    max_direct_m = float(bus_params.get("straight_line_candidate_m", 300.0)) + max(
        0.0,
        float(bus_params.get("straight_line_candidate_tolerance_m", 0.0)),
    )
    if direct_m > max_direct_m:
        return None

    routed_m = float(route_result.get("shortest_length_m") or 0.0)
    if routed_m <= 0:
        return None
    detour_ratio = float(bus_params.get("direct_fallback_detour_ratio", 3.0))
    near_stop_detour_ratio = bus_params.get("direct_fallback_near_stop_detour_ratio")
    if near_stop_detour_ratio is not None:
        detour_ratio = min(detour_ratio, float(near_stop_detour_ratio))
    min_extra_m = float(bus_params.get("direct_fallback_min_extra_m", 100.0))
    if bool(bus_params.get("direct_fallback_scale_min_extra_to_direct", False)):
        min_extra_m = min(min_extra_m, direct_m)
    if routed_m >= direct_m * detour_ratio and (routed_m - direct_m) >= min_extra_m:
        return "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"

    shortcut_ratio = float(bus_params.get("direct_fallback_shortcut_ratio", 0.5))
    min_missing_m = float(bus_params.get("direct_fallback_min_missing_m", 50.0))
    if routed_m <= direct_m * shortcut_ratio and (direct_m - routed_m) >= min_missing_m:
        return "implausibly_short_graph_route_to_datamall_bus_stop_within_direct_radius"

    return None


def normalized_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


def bus_edge_is_low_trust_road_centerline(edge: dict[str, Any]) -> bool:
    source_layer = normalized_text(edge.get("source_layer"))
    synth_class = normalized_text(edge.get("synth_class"))
    confidence = normalized_text(edge.get("confidence"))
    if source_layer or synth_class or confidence:
        return False

    highway = normalized_text(edge.get("highway"))
    if highway not in LOW_TRUST_BUS_ROAD_HIGHWAYS:
        return False
    if normalized_text(edge.get("foot")) in PEDESTRIAN_FOOT_VALUES:
        return False
    if normalized_text(edge.get("sidewalk")) in {"both", "left", "right", "yes"}:
        return False
    if normalized_text(edge.get("crossing")):
        return False
    if normalized_text(edge.get("bridge")) in {"yes", "covered"}:
        return False
    if normalized_text(edge.get("tunnel")) in {"yes", "building_passage"}:
        return False
    return True


def bus_edge_has_pedestrian_evidence(edge: dict[str, Any]) -> bool:
    source_layer = normalized_text(edge.get("source_layer"))
    if source_layer and source_layer not in {
        "origin_graph_snap_connector",
        "destination_graph_snap_connector",
        "bus_stop_access_connector",
    }:
        return True
    highway = normalized_text(edge.get("highway"))
    if highway in PEDESTRIAN_EVIDENCE_HIGHWAYS:
        return True
    if normalized_text(edge.get("foot")) in PEDESTRIAN_FOOT_VALUES:
        return True
    if normalized_text(edge.get("sidewalk")) in {"both", "left", "right", "yes"}:
        return True
    return False


def bus_route_trust_rejection_reason(
    candidate: CandidateNode,
    route_result: dict[str, Any],
    bus_params: dict[str, Any],
) -> str | None:
    if candidate.node_type != "bus_stop":
        return None
    edges = route_result.get("shortest_path_edges")
    if not isinstance(edges, list) or not edges:
        return None

    route_m = float(route_result.get("shortest_length_m") or 0.0)
    if route_m <= 0:
        return None

    endpoint_snap_m = float(route_result.get("endpoint_snap_connector_m") or 0.0)
    min_endpoint_snap_m = float(bus_params.get("endpoint_snap_guard_min_m", 25.0))
    min_endpoint_snap_ratio = float(bus_params.get("endpoint_snap_guard_min_ratio", 0.6))
    if (
        endpoint_snap_m >= min_endpoint_snap_m
        and endpoint_snap_m >= route_m * min_endpoint_snap_ratio
    ):
        return "dominant_unrouted_bus_endpoint_snap"

    bus_stop_connector_m = float(route_result.get("bus_stop_access_connector_m") or 0.0)
    max_bus_stop_connector_m = float(bus_params.get("access_connector_trust_max_m", 40.0))
    min_bus_stop_connector_ratio = float(bus_params.get("access_connector_trust_min_ratio", 0.2))
    if (
        bus_stop_connector_m >= max_bus_stop_connector_m
        and bus_stop_connector_m >= route_m * min_bus_stop_connector_ratio
    ):
        return "large_unrouted_bus_stop_access_connector"

    low_trust_road_m = 0.0
    pedestrian_evidence_m = 0.0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        length_m = float(edge.get("length_m") or 0.0)
        if bus_edge_is_low_trust_road_centerline(edge):
            low_trust_road_m += length_m
        if bus_edge_has_pedestrian_evidence(edge):
            pedestrian_evidence_m += length_m

    min_road_m = float(bus_params.get("road_centerline_guard_min_m", 50.0))
    min_road_ratio = float(bus_params.get("road_centerline_guard_min_ratio", 0.5))
    max_pedestrian_m = float(bus_params.get("road_centerline_guard_max_pedestrian_m", 25.0))
    if (
        route_m > 0
        and low_trust_road_m >= min_road_m
        and low_trust_road_m >= route_m * min_road_ratio
        and pedestrian_evidence_m <= max_pedestrian_m
    ):
        return "low_trust_bus_stop_road_centerline_route"
    return None


def merge_with_connector_geometry(route_geometry: Any, connector: LineString) -> Any:
    if route_geometry is None or getattr(route_geometry, "is_empty", False):
        return connector
    geometries = (
        list(route_geometry.geoms)
        if isinstance(route_geometry, MultiLineString)
        else [route_geometry]
    )
    return linemerge(MultiLineString([*geometries, connector]))


def snap_connector_edge(
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    *,
    source_layer: str,
    synth_class: str,
) -> dict[str, Any] | None:
    connector = LineString([start_xy, end_xy])
    connector_m = float(connector.length)
    if connector_m <= 0.05:
        return None
    return {
        "length_m": connector_m,
        "is_covered": False,
        "geometry": connector,
        "source_layer": source_layer,
        "synth_class": synth_class,
        "confidence": "inferred_endpoint_snap",
    }


def route_with_endpoint_snap_connectors(
    route_result: dict[str, Any],
    *,
    postal_point: Any,
    origin_node: tuple[float, float],
    candidate: CandidateNode,
    include_destination: bool = True,
) -> dict[str, Any]:
    origin_edge = snap_connector_edge(
        (float(postal_point.x), float(postal_point.y)),
        (float(origin_node[0]), float(origin_node[1])),
        source_layer="origin_graph_snap_connector",
        synth_class="ORIGIN_GRAPH_SNAP_CONNECTOR",
    )
    destination_edge = None
    if include_destination and candidate.point_xy is not None:
        destination = route_result.get("destination", candidate.graph_node)
        destination_edge = snap_connector_edge(
            (float(destination[0]), float(destination[1])),
            (float(candidate.point_xy[0]), float(candidate.point_xy[1])),
            source_layer="destination_graph_snap_connector",
            synth_class="DESTINATION_GRAPH_SNAP_CONNECTOR",
        )

    connector_edges = [edge for edge in (origin_edge, destination_edge) if edge is not None]
    if not connector_edges:
        return route_result

    origin_connector_m = float(origin_edge["length_m"]) if origin_edge is not None else 0.0
    destination_connector_m = (
        float(destination_edge["length_m"]) if destination_edge is not None else 0.0
    )
    connector_m = origin_connector_m + destination_connector_m
    shortest_base_m = float(route_result["shortest_length_m"])
    sheltered_base_m = float(route_result["length_m"])
    covered_m = float(route_result["covered_m"])
    shade_m = float(route_result.get("shade_m") or 0.0)
    shortest_covered_m = shortest_base_m * float(route_result["shortest_covered_ratio"])
    shortest_shade_m = float(route_result.get("shortest_shade_m") or 0.0)
    shortest_total_m = shortest_base_m + connector_m
    sheltered_total_m = sheltered_base_m + connector_m

    attached = dict(route_result)
    attached.update(
        {
            "shortest_length_m": shortest_total_m,
            "length_m": sheltered_total_m,
            "covered_ratio": covered_m / sheltered_total_m if sheltered_total_m > 0 else 0.0,
            "shade_ratio": shade_m / sheltered_total_m if sheltered_total_m > 0 else 0.0,
            "shortest_covered_ratio": (
                shortest_covered_m / shortest_total_m if shortest_total_m > 0 else 0.0
            ),
            "shortest_shade_ratio": (
                shortest_shade_m / shortest_total_m if shortest_total_m > 0 else 0.0
            ),
            "sheltered_length_m": (
                float(route_result["sheltered_length_m"]) + connector_m
                if route_result.get("sheltered_length_m") is not None
                else None
            ),
            "origin_graph_snap_connector_m": origin_connector_m,
            "destination_graph_snap_connector_m": destination_connector_m,
            "endpoint_snap_connector_m": connector_m,
        }
    )

    shortest_edges = [
        *([origin_edge] if origin_edge is not None else []),
        *route_result.get("shortest_path_edges", []),
        *([destination_edge] if destination_edge is not None else []),
    ]
    sheltered_edges = [
        *([origin_edge] if origin_edge is not None else []),
        *route_result.get("sheltered_path_edges", route_result.get("path_edges", [])),
        *([destination_edge] if destination_edge is not None else []),
    ]
    attached["shortest_path_edges"] = shortest_edges
    attached["sheltered_path_edges"] = sheltered_edges
    attached["path_edges"] = sheltered_edges

    shortest_geometry = route_result.get("shortest_geometry")
    sheltered_geometry = route_result.get("geometry")
    for edge in connector_edges:
        shortest_geometry = merge_with_connector_geometry(shortest_geometry, edge["geometry"])
        sheltered_geometry = merge_with_connector_geometry(sheltered_geometry, edge["geometry"])
    attached["shortest_geometry"] = shortest_geometry
    attached["geometry"] = sheltered_geometry
    return attached


def route_with_bus_stop_access_connector(
    route_result: dict[str, Any],
    candidate: CandidateNode,
) -> dict[str, Any]:
    if candidate.point_xy is None:
        raise ValueError("bus stop connector requires candidate.point_xy")

    destination = route_result["destination"]
    stop_xy = (float(candidate.point_xy[0]), float(candidate.point_xy[1]))
    connector = LineString([(float(destination[0]), float(destination[1])), stop_xy])
    connector_m = float(connector.length)
    connector_edge = {
        "length_m": connector_m,
        "is_covered": False,
        "geometry": connector,
        "source_layer": "bus_stop_access_connector",
        "synth_class": "BUS_STOP_ACCESS_CONNECTOR",
        "confidence": "inferred_endpoint_snap",
    }

    shortest_base_m = float(route_result["shortest_length_m"])
    sheltered_base_m = float(route_result["length_m"])
    covered_m = float(route_result["covered_m"])
    shade_m = float(route_result.get("shade_m") or 0.0)
    shortest_covered_m = shortest_base_m * float(route_result["shortest_covered_ratio"])
    shortest_shade_m = float(route_result.get("shortest_shade_m") or 0.0)
    shortest_total_m = shortest_base_m + connector_m
    sheltered_total_m = sheltered_base_m + connector_m

    attached = dict(route_result)
    attached.update(
        {
            "destination": candidate.graph_node,
            "routing_type": f"{route_result['routing_type']}_with_bus_stop_access_connector",
            "length_m": sheltered_total_m,
            "covered_m": covered_m,
            "covered_ratio": covered_m / sheltered_total_m if sheltered_total_m > 0 else 0.0,
            "shade_m": shade_m,
            "shade_ratio": shade_m / sheltered_total_m if sheltered_total_m > 0 else 0.0,
            "shortest_length_m": shortest_total_m,
            "shortest_covered_ratio": (
                shortest_covered_m / shortest_total_m if shortest_total_m > 0 else 0.0
            ),
            "shortest_shade_m": shortest_shade_m,
            "shortest_shade_ratio": (
                shortest_shade_m / shortest_total_m if shortest_total_m > 0 else 0.0
            ),
            "sheltered_length_m": (
                float(route_result["sheltered_length_m"]) + connector_m
                if route_result.get("sheltered_length_m") is not None
                else None
            ),
            "bus_stop_access_connector_m": connector_m,
        }
    )

    shortest_edges = [*route_result.get("shortest_path_edges", []), connector_edge]
    sheltered_edges = [*route_result.get("sheltered_path_edges", []), connector_edge]
    attached["shortest_path_edges"] = shortest_edges
    attached["sheltered_path_edges"] = sheltered_edges
    attached["path_edges"] = sheltered_edges
    attached["shortest_geometry"] = merge_with_connector_geometry(
        route_result.get("shortest_geometry"), connector
    )
    attached["geometry"] = merge_with_connector_geometry(route_result.get("geometry"), connector)
    return attached


def bus_access_connector_is_plausible(
    candidate: CandidateNode,
    connector_route: dict[str, Any],
    bus_params: dict[str, Any],
) -> bool:
    total_m = float(connector_route["shortest_length_m"])
    direct_m = float(candidate.straight_line_m)
    if direct_m <= 0:
        return False
    max_walk_m = float(
        bus_params.get(
            "access_connector_max_walk_m",
            bus_params.get("straight_line_candidate_m", 300.0),
        )
    ) + max(0.0, float(bus_params.get("straight_line_candidate_tolerance_m", 0.0)))
    max_ratio = float(bus_params.get("access_connector_max_direct_ratio", 2.5))
    max_extra_m = float(bus_params.get("access_connector_max_extra_m", 100.0))
    shortcut_ratio = float(bus_params.get("direct_fallback_shortcut_ratio", 0.5))
    min_missing_m = float(bus_params.get("direct_fallback_min_missing_m", 50.0))
    if total_m <= direct_m * shortcut_ratio and (direct_m - total_m) >= min_missing_m:
        return False
    return (
        total_m <= max_walk_m
        and total_m <= direct_m * max_ratio
        and (total_m - direct_m) <= max_extra_m
    )


def build_bus_stop_access_connector_route(
    *,
    candidate: CandidateNode,
    origin_node: tuple[float, float],
    routing_graph: RoutingGraph,
    nodes: list[tuple[float, float]],
    node_xy: np.ndarray,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    if candidate.node_type != "bus_stop" or candidate.point_xy is None:
        return None
    bus_params = params["bus_connectivity"]
    search_m = float(bus_params.get("access_connector_search_m", 0.0))
    if search_m <= 0:
        return None
    stop_array = np.asarray(candidate.point_xy, dtype=float)
    deltas = node_xy - stop_array
    distances = np.sqrt(np.einsum("ij,ij->i", deltas, deltas))
    indices = np.flatnonzero(distances <= search_m)
    if len(indices) == 0:
        return None

    max_candidates = int(bus_params.get("access_connector_max_candidates", 24))
    ordered = sorted((float(distances[index]), int(index)) for index in indices)[:max_candidates]
    destinations = [nodes[index] for _, index in ordered if nodes[index] != candidate.graph_node]
    if not destinations:
        return None

    alternate_routes = routing_graph.route(
        {origin_node: destinations},
        float(params["shelter_lambda"]),
        float(params["detour_budget"]),
        include_geometry=True,
    )
    attached_routes = [
        route_with_bus_stop_access_connector(route_result, candidate)
        for route_result in alternate_routes
    ]
    plausible = [
        route
        for route in attached_routes
        if bus_access_connector_is_plausible(candidate, route, bus_params)
    ]
    if not plausible:
        return None
    return min(plausible, key=lambda route: float(route["shortest_length_m"]))


def candidate_sort_key(candidate_score: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(candidate_score["total"]),
        float(candidate_score["subscores"].get("rain") or 0.0),
        -float(candidate_score["paths"]["shortest_m"]),
    )


def public_route_option(candidate_score: dict[str, Any]) -> dict[str, Any]:
    has_pending_subscores = any(value is None for value in candidate_score["subscores"].values())
    return {
        "state": "SCORED_PARTIAL" if has_pending_subscores else "SCORED",
        "total": round(float(candidate_score["total"]), 1),
        "subscores": candidate_score["subscores"],
        "best_node": candidate_score["best_node"],
        "paths": candidate_score["paths"],
        "exposure_gaps": candidate_score["exposure_gaps"],
    }


def empty_route_option(node_type: str) -> dict[str, Any]:
    return {
        "state": NO_TRANSIT_IN_RANGE,
        "total": None,
        "subscores": None,
        "best_node": {
            "type": node_type,
            "name": "No route found",
            "routed_m": None,
        },
        "paths": None,
        "exposure_gaps": None,
    }


def best_candidate_by_type(
    scored_candidates: list[dict[str, Any]],
    node_type: str,
) -> dict[str, Any] | None:
    typed = [
        candidate_score
        for candidate_score in scored_candidates
        if candidate_score["best_node"].get("type") == node_type
    ]
    return max(typed, key=candidate_sort_key) if typed else None


def build_provenance(
    params: dict[str, Any],
    crossing_counter: CrossingCounter,
    bus_data_available: bool,
    network_path: Path = NETWORK_PATH,
    postal_universe_path: Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest()
    sources = manifest.get("sources", {})
    network_label = (
        str(network_path.relative_to(PROJECT_ROOT))
        if network_path.is_relative_to(PROJECT_ROOT)
        else str(network_path)
    )
    return {
        "manifest": "raw/manifest.json",
        "source_hashes": {
            key: value.get("sha256")
            for key, value in sources.items()
            if key
            in {
                "mrt_lrt_exits",
                "osm_extract",
                "covered_linkway",
                "overhead_bridge_underpass",
                "traffic_signals",
                "bus_stops",
                "bus_services",
                "bus_routes",
                "leaf_area_index",
                "nparks_heritage_road_green_buffers",
                "nparks_nature_ways",
                "nparks_park_connector_loop",
                "nparks_tracks",
                "nparks_heritage_trees",
            }
        },
        "routing": {
            "network": network_label,
            "shelter_lambda": params["shelter_lambda"],
            "detour_budget": params["detour_budget"],
        },
        "postal_universe": (
            str(postal_universe_path.relative_to(PROJECT_ROOT))
            if postal_universe_path is not None
            and postal_universe_path.is_relative_to(PROJECT_ROOT)
            else (
                str(postal_universe_path)
                if postal_universe_path is not None
                else "raw/geocode_cache.db"
            )
        ),
        "subscore_status": {
            "access": "real_routed_shortest_distance",
            "bus": "real" if bus_data_available else "pending_lta_datamall_account_key",
            "rain": "real_routed_covered_length_ratio",
            "heat": (
                "provisional_covered_plus_nparks_shade_proxy_heat_only"
                if any(
                    key in sources
                    for key in (
                        "nparks_nature_ways",
                        "nparks_park_connector_loop",
                        "nparks_tracks",
                        "nparks_heritage_trees",
                        "nparks_heritage_road_green_buffers",
                    )
                )
                else "provisional_covered_only_until_phase_4"
            ),
            "crossing": (
                "real_traffic_signals_with_grade_separated_exemption"
                if crossing_counter.available
                else "pending_traffic_signal_data"
            ),
        },
    }


def load_scoring_context(
    network_path: Path = NETWORK_PATH,
    postal_universe_path: Path | None = None,
) -> ScoringContext:
    params, weights = load_params_and_weights()
    edges_df, edges_dict, nodes, node_xy = load_network_inputs(network_path=network_path)
    routing_graph = RoutingGraph.from_prepared_edges(edges_df)
    mrt_exits_gdf = load_mrt_exits()
    crossing_counter = CrossingCounter.from_raw_data(params)
    bus_index = BusConnectivityIndex.from_raw_data(nodes, node_xy)
    base_provenance = build_provenance(
        params,
        crossing_counter,
        bus_data_available=bus_index is not None,
        network_path=network_path,
        postal_universe_path=postal_universe_path,
    )
    data_as_of = load_manifest().get("generated_at")
    return ScoringContext(
        params=params,
        weights=weights,
        edges_dict=edges_dict,
        routing_graph=routing_graph,
        nodes=nodes,
        node_xy=node_xy,
        mrt_exits_gdf=mrt_exits_gdf,
        crossing_counter=crossing_counter,
        bus_index=bus_index,
        network_path=network_path,
        postal_universe_path=postal_universe_path,
        base_provenance=base_provenance,
        data_as_of=data_as_of,
    )


def assemble_score_record(
    postal: str,
    candidate_scores: list[dict[str, Any]],
    data_as_of: str | None,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    scored_candidates = [
        candidate for candidate in candidate_scores if isinstance(candidate["total"], int | float)
    ]
    if not scored_candidates:
        return {
            "postal": postal,
            "state": NO_TRANSIT_IN_RANGE,
            "total": None,
            "subscores": None,
            "best_node": None,
            "paths": None,
            "exposure_gaps": None,
            "data_as_of": data_as_of,
            "provenance": provenance,
        }

    best = max(scored_candidates, key=candidate_sort_key)
    best_mrt = best_candidate_by_type(scored_candidates, "mrt_lrt_exit")
    best_bus = best_candidate_by_type(scored_candidates, "bus_stop")

    route_options = {
        "best_transit": public_route_option(best),
        "mrt_lrt": (
            public_route_option(best_mrt)
            if best_mrt is not None
            else empty_route_option("mrt_lrt_exit")
        ),
        "bus": (
            public_route_option(best_bus)
            if best_bus is not None
            else empty_route_option("bus_stop")
        ),
    }

    record = {
        "postal": postal,
        "state": route_options["best_transit"]["state"],
        "total": round(float(best["total"]), 1),
        "subscores": best["subscores"],
        "best_node": best["best_node"],
        "paths": best["paths"],
        "exposure_gaps": best["exposure_gaps"],
        "route_options": route_options,
        "data_as_of": data_as_of,
        "provenance": provenance,
    }
    if "_geometry" in best:
        record["_geometry"] = best["_geometry"]
    geometry_options = {
        key: candidate_score["_geometry"]
        for key, candidate_score in {
            "best_transit": best,
            "mrt_lrt": best_mrt,
            "bus": best_bus,
        }.items()
        if candidate_score is not None and "_geometry" in candidate_score
    }
    if geometry_options:
        record["_geometry_options"] = geometry_options
    return record


def annotate_no_transit_reason(
    provenance: dict[str, Any],
    candidates: list[CandidateNode],
    route_distances: list[float],
    candidate_scores: list[dict[str, Any]],
    access_zero_m: float,
) -> None:
    if any(
        isinstance(candidate_score.get("total"), int | float)
        for candidate_score in candidate_scores
    ):
        return
    if provenance.get("reason"):
        return
    if not candidates:
        provenance["reason"] = "no_transit_candidates_selected"
        return
    if not route_distances:
        provenance["reason"] = "transit_candidates_graph_disconnected"
        return
    nearest = min(route_distances)
    if nearest > access_zero_m:
        provenance["reason"] = "all_routed_transit_candidates_beyond_access_range"
        provenance["access_zero_credit_m"] = round(access_zero_m, 1)
        provenance["nearest_routed_m"] = round(nearest, 1)
        return
    provenance["reason"] = "no_numeric_candidate_score"


def json_safe_geometry(value: Any) -> Any:
    if value is None:
        return None
    wkt_value = getattr(value, "wkt", None)
    return wkt_value if isinstance(wkt_value, str) else value


def json_safe_score_record(record: dict[str, Any]) -> dict[str, Any]:
    safe = dict(record)
    geometry_payload = safe.get("_geometry")
    if not isinstance(geometry_payload, dict):
        return safe

    safe_geometry = dict(geometry_payload)
    safe_geometry["shortest"] = json_safe_geometry(safe_geometry.get("shortest"))
    safe_geometry["sheltered"] = json_safe_geometry(safe_geometry.get("sheltered"))

    for edges_key in ["shortest_path_edges", "sheltered_path_edges", "exposure_gap_edges"]:
        path_edges = safe_geometry.get(edges_key)
        if not isinstance(path_edges, list):
            continue
        safe_edges: list[Any] = []
        for edge in path_edges:
            if not isinstance(edge, dict):
                safe_edges.append(edge)
                continue
            safe_edge = dict(edge)
            safe_edge["geometry"] = json_safe_geometry(safe_edge.get("geometry"))
            safe_edges.append(safe_edge)
        safe_geometry[edges_key] = safe_edges

    safe["_geometry"] = safe_geometry
    geometry_options = safe.get("_geometry_options")
    if isinstance(geometry_options, dict):
        safe_options = {}
        for key, option_geometry in geometry_options.items():
            if not isinstance(option_geometry, dict):
                continue
            option_safe = dict(option_geometry)
            option_safe["shortest"] = json_safe_geometry(option_safe.get("shortest"))
            option_safe["sheltered"] = json_safe_geometry(option_safe.get("sheltered"))
            for edges_key in ["shortest_path_edges", "sheltered_path_edges", "exposure_gap_edges"]:
                path_edges = option_safe.get(edges_key)
                if not isinstance(path_edges, list):
                    continue
                option_safe_edges: list[Any] = []
                for edge in path_edges:
                    if not isinstance(edge, dict):
                        option_safe_edges.append(edge)
                        continue
                    safe_edge = dict(edge)
                    safe_edge["geometry"] = json_safe_geometry(safe_edge.get("geometry"))
                    option_safe_edges.append(safe_edge)
                option_safe[edges_key] = option_safe_edges
            safe_options[str(key)] = option_safe
        safe["_geometry_options"] = safe_options
    return safe


def add_private_origin(record: dict[str, Any], postal_point: Any) -> dict[str, Any]:
    transformer = Transformer.from_crs("EPSG:3414", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(postal_point.x, postal_point.y)
    record["_origin"] = {
        "lat": round(float(lat), 9),
        "lon": round(float(lon), 9),
        "x": round(float(postal_point.x), 3),
        "y": round(float(postal_point.y), 3),
    }
    return record


def score_postal_row(
    postal_row: pd.Series,
    mrt_exits_gdf: gpd.GeoDataFrame,
    edges_dict: dict[str, list[Any]],
    routing_graph: RoutingGraph,
    nodes: list[tuple[float, float]],
    node_xy: np.ndarray,
    params: dict[str, Any],
    weights: dict[str, float],
    crossing_counter: CrossingCounter,
    bus_index: BusConnectivityIndex | None = None,
    include_geometry: bool = False,
    network_path: Path = NETWORK_PATH,
    postal_universe_path: Path | None = None,
    base_provenance: dict[str, Any] | None = None,
    data_as_of: str | None = None,
) -> dict[str, Any]:
    postal = str(postal_row["postal_code"])
    origin_node, origin_snap_m = nearest_graph_node(postal_row.geometry, nodes, node_xy)
    bus_data_available = bus_index is not None
    mrt_candidates = select_mrt_exit_candidates(postal_row.geometry, mrt_exits_gdf, nodes, node_xy)
    bus_candidate_radius_m = float(
        params.get("bus_connectivity", {}).get(
            "straight_line_candidate_m",
            float(params.get("bus_connectivity", {}).get("routed_max_m", 250.0)) + 50.0,
        )
    )
    bus_candidate_tolerance_m = max(
        0.0,
        float(
            params.get("bus_connectivity", {}).get(
                "straight_line_candidate_tolerance_m",
                0.0,
            )
        ),
    )
    bus_candidate_selection_radius_m = bus_candidate_radius_m + bus_candidate_tolerance_m
    bus_candidates = select_bus_stop_candidates(
        postal_row.geometry,
        bus_index,
        bus_candidate_selection_radius_m,
    )
    candidates = mrt_candidates + bus_candidates
    provenance = (
        copy.deepcopy(base_provenance)
        if base_provenance is not None
        else build_provenance(
            params,
            crossing_counter,
            bus_data_available=bus_data_available,
            network_path=network_path,
            postal_universe_path=postal_universe_path,
        )
    )
    provenance["origin_snap_distance_m"] = round(origin_snap_m, 1)
    provenance["transit_node_set"] = {
        "mrt_lrt_exit_candidates": len(mrt_candidates),
        "bus_stop_candidates_direct": len(bus_candidates),
        "bus_stop_candidate_radius_m": round(bus_candidate_radius_m, 1),
        "bus_stop_candidate_tolerance_m": round(bus_candidate_tolerance_m, 1),
        "bus_stop_candidate_selection_radius_m": round(bus_candidate_selection_radius_m, 1),
    }
    record_data_as_of = (
        data_as_of if data_as_of is not None else load_manifest().get("generated_at")
    )

    if not candidates:
        provenance["reason"] = "no_transit_candidates_selected"
        provenance["routing_diagnostics"] = {
            "candidate_destination_nodes": 0,
            "route_results": 0,
            "candidate_scores": 0,
            "nearest_routed_m": None,
            "routes_within_access_range": 0,
        }
        record = assemble_score_record(postal, [], record_data_as_of, provenance)
        return add_private_origin(record, postal_row.geometry) if include_geometry else record

    destinations: list[tuple[float, float]] = []
    candidate_by_destination: dict[tuple[float, float], list[CandidateNode]] = {}
    for candidate in candidates:
        if candidate.graph_node not in candidate_by_destination:
            destinations.append(candidate.graph_node)
            candidate_by_destination[candidate.graph_node] = []
        candidate_by_destination[candidate.graph_node].append(candidate)

    route_results = routing_graph.route(
        {origin_node: destinations},
        float(params["shelter_lambda"]),
        float(params["detour_budget"]),
        include_geometry=True,
    )
    if not route_results:
        destination_components = {
            routing_graph.component_membership[routing_graph.node_map[destination]]
            for destination in destinations
            if destination in routing_graph.node_map
        }
        resnap_max_m = float(params.get("origin_reachable_component_resnap_max_m", 0.0))
        resnap = nearest_graph_node_in_components(
            postal_row.geometry,
            nodes,
            node_xy,
            routing_graph,
            destination_components,
            resnap_max_m,
        )
        if resnap is not None and resnap[0] != origin_node:
            original_origin_node = origin_node
            original_origin_snap_m = origin_snap_m
            origin_node, origin_snap_m = resnap
            provenance["origin_snap_distance_m"] = round(origin_snap_m, 1)
            provenance["origin_resnap"] = {
                "reason": "nearest_origin_component_cannot_reach_selected_transit_candidates",
                "max_distance_m": round(resnap_max_m, 1),
                "original_snap_distance_m": round(original_origin_snap_m, 1),
                "resnap_distance_m": round(origin_snap_m, 1),
                "original_component": routing_graph.component_membership[
                    routing_graph.node_map[original_origin_node]
                ],
                "resnapped_component": routing_graph.component_membership[
                    routing_graph.node_map[origin_node]
                ],
            }
            route_results = routing_graph.route(
                {origin_node: destinations},
                float(params["shelter_lambda"]),
                float(params["detour_budget"]),
                include_geometry=True,
            )
        elif resnap_max_m > 0:
            provenance["origin_resnap"] = {
                "reason": "no_transit_reachable_component_within_resnap_cap",
                "max_distance_m": round(resnap_max_m, 1),
            }

    bus_result = (
        bus_connectivity_from_routed_candidates(
            route_results,
            candidate_by_destination,
            float(params["bus_connectivity"]["routed_max_m"]),
            straight_line_stop_count=len(bus_candidates),
        )
        if bus_data_available
        else None
    )
    if bus_result is not None:
        provenance["bus_connectivity"] = {
            "expected_wait_min": (
                round(bus_result.expected_wait_min, 3)
                if bus_result.expected_wait_min is not None
                else None
            ),
            "routed_stop_count": bus_result.routed_stop_count,
            "straight_line_stop_count": bus_result.straight_line_stop_count,
            "service_count": bus_result.service_count,
            "nearest_routed_m": (
                round(bus_result.nearest_routed_m, 1)
                if bus_result.nearest_routed_m is not None
                else None
            ),
        }

    candidate_scores = []
    implausible_bus_candidates: list[CandidateNode] = []
    implausible_bus_route_distances: list[float] = []
    implausible_bus_reasons: Counter[str] = Counter()
    bus_access_connector_rows: list[dict[str, Any]] = []
    untrusted_bus_route_reasons: Counter[str] = Counter()
    untrusted_bus_route_rows: list[dict[str, Any]] = []
    for route_result in route_results:
        for candidate in candidate_by_destination[route_result["destination"]]:
            candidate_route = route_with_endpoint_snap_connectors(
                route_result,
                postal_point=postal_row.geometry,
                origin_node=origin_node,
                candidate=candidate,
            )
            crossing_count = crossing_counter.count_for_route(candidate_route.get("geometry"))
            direct_fallback_reason = bus_route_direct_fallback_reason(
                candidate,
                candidate_route,
                params["bus_connectivity"],
            )
            if direct_fallback_reason is not None:
                connector_route = build_bus_stop_access_connector_route(
                    candidate=candidate,
                    origin_node=origin_node,
                    routing_graph=routing_graph,
                    nodes=nodes,
                    node_xy=node_xy,
                    params=params,
                )
                if connector_route is not None:
                    connector_route = route_with_endpoint_snap_connectors(
                        connector_route,
                        postal_point=postal_row.geometry,
                        origin_node=origin_node,
                        candidate=candidate,
                        include_destination=False,
                    )
                    connector_trust_rejection_reason = bus_route_trust_rejection_reason(
                        candidate,
                        connector_route,
                        params["bus_connectivity"],
                    )
                    if connector_trust_rejection_reason is not None:
                        untrusted_bus_route_reasons[connector_trust_rejection_reason] += 1
                        untrusted_bus_route_rows.append(
                            {
                                "name": candidate.name,
                                "bus_stop_code": candidate.exit_code,
                                "direct_m": round(float(candidate.straight_line_m), 1),
                                "routed_m": round(float(connector_route["shortest_length_m"]), 1),
                                "routing_type": connector_route.get("routing_type"),
                            }
                        )
                    else:
                        connector_crossings = crossing_counter.count_for_route(
                            connector_route.get("geometry")
                        )
                        connector_routed_m = float(connector_route["shortest_length_m"])
                        routed_bus_wait = (
                            candidate.expected_wait_min
                            if connector_routed_m
                            <= float(params["bus_connectivity"]["routed_max_m"])
                            else bus_result.expected_wait_min if bus_result else None
                        )
                        candidate_scores.append(
                            score_candidate_route(
                                candidate,
                                connector_route,
                                params,
                                weights,
                                connector_crossings,
                                bus_expected_wait_min=routed_bus_wait,
                                bus_data_available=bus_data_available,
                                include_geometry=include_geometry,
                            )
                        )
                        bus_access_connector_rows.append(
                            {
                                "name": candidate.name,
                                "bus_stop_code": candidate.exit_code,
                                "direct_m": round(float(candidate.straight_line_m), 1),
                                "routed_m": round(connector_routed_m, 1),
                                "connector_m": round(
                                    float(connector_route["bus_stop_access_connector_m"]), 1
                                ),
                            }
                        )
                        continue
                if (
                    direct_fallback_reason
                    == "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"
                ):
                    implausible_bus_candidates.append(candidate)
                    implausible_bus_route_distances.append(
                        float(candidate_route["shortest_length_m"])
                    )
                    implausible_bus_reasons[direct_fallback_reason] += 1
                    continue
                if connector_route is not None:
                    continue
                implausible_bus_candidates.append(candidate)
                implausible_bus_route_distances.append(float(candidate_route["shortest_length_m"]))
                implausible_bus_reasons[direct_fallback_reason] += 1
                continue
            trust_rejection_reason = bus_route_trust_rejection_reason(
                candidate,
                candidate_route,
                params["bus_connectivity"],
            )
            if trust_rejection_reason is not None:
                untrusted_bus_route_reasons[trust_rejection_reason] += 1
                untrusted_bus_route_rows.append(
                    {
                        "name": candidate.name,
                        "bus_stop_code": candidate.exit_code,
                        "direct_m": round(float(candidate.straight_line_m), 1),
                        "routed_m": round(float(candidate_route["shortest_length_m"]), 1),
                        "routing_type": candidate_route.get("routing_type"),
                    }
                )
                continue
            candidate_scores.append(
                score_candidate_route(
                    candidate,
                    candidate_route,
                    params,
                    weights,
                    crossing_count,
                    bus_expected_wait_min=bus_result.expected_wait_min if bus_result else None,
                    bus_data_available=bus_data_available,
                    include_geometry=include_geometry,
                )
            )

    if bus_access_connector_rows:
        bus_params = params["bus_connectivity"]
        provenance["bus_stop_access_connector"] = {
            "reason": "implausible_bus_stop_graph_snap_replaced_by_nearby_graph_route_plus_exposed_connector",
            "candidate_count": len(bus_access_connector_rows),
            "search_m": round(float(bus_params.get("access_connector_search_m", 0.0)), 1),
            "max_candidates": int(bus_params.get("access_connector_max_candidates", 24)),
            "max_walk_m": round(
                float(
                    bus_params.get(
                        "access_connector_max_walk_m",
                        bus_params.get("straight_line_candidate_m", 300.0),
                    )
                ),
                1,
            ),
            "max_direct_ratio": round(
                float(bus_params.get("access_connector_max_direct_ratio", 2.5)), 3
            ),
            "max_extra_m": round(float(bus_params.get("access_connector_max_extra_m", 100.0)), 1),
            "geometry": "graph_route_plus_exposed_endpoint_connector_to_datamall_bus_stop",
            "source_layer": "bus_stop_access_connector",
            "examples": bus_access_connector_rows[:5],
        }

    if untrusted_bus_route_reasons:
        bus_params = params["bus_connectivity"]
        provenance["untrusted_bus_routes"] = {
            "reason_counts": dict(sorted(untrusted_bus_route_reasons.items())),
            "candidate_count": sum(untrusted_bus_route_reasons.values()),
            "policy": "skipped_from_routed_scoring; long-detour bus candidates may still use partial direct fallback",
            "endpoint_snap_guard_min_m": round(
                float(bus_params.get("endpoint_snap_guard_min_m", 25.0)), 1
            ),
            "endpoint_snap_guard_min_ratio": round(
                float(bus_params.get("endpoint_snap_guard_min_ratio", 0.6)), 3
            ),
            "access_connector_trust_max_m": round(
                float(bus_params.get("access_connector_trust_max_m", 40.0)), 1
            ),
            "access_connector_trust_min_ratio": round(
                float(bus_params.get("access_connector_trust_min_ratio", 0.2)), 3
            ),
            "road_centerline_guard_min_m": round(
                float(bus_params.get("road_centerline_guard_min_m", 50.0)), 1
            ),
            "road_centerline_guard_min_ratio": round(
                float(bus_params.get("road_centerline_guard_min_ratio", 0.5)), 3
            ),
            "road_centerline_guard_max_pedestrian_m": round(
                float(bus_params.get("road_centerline_guard_max_pedestrian_m", 25.0)), 1
            ),
            "examples": untrusted_bus_route_rows[:5],
        }

    if implausible_bus_candidates:
        fallback_scores = direct_bus_fallback_candidate_scores(
            implausible_bus_candidates,
            postal_row.geometry,
            params,
            weights,
            include_geometry=include_geometry,
        )
        if fallback_scores:
            candidate_scores.extend(fallback_scores)
            expected_waits = [
                candidate.expected_wait_min
                for candidate in implausible_bus_candidates
                if candidate.expected_wait_min is not None
            ]
            reason_counts = dict(sorted(implausible_bus_reasons.items()))
            provenance["direct_bus_fallback"] = {
                "reason": (
                    next(iter(reason_counts))
                    if len(reason_counts) == 1
                    else "multiple_implausible_graph_routes_to_datamall_bus_stops_within_direct_radius"
                ),
                "reason_counts": reason_counts,
                "candidate_count": len(implausible_bus_candidates),
                "radius_m": round(bus_candidate_radius_m, 1),
                "coordinate_tolerance_m": round(bus_candidate_tolerance_m, 1),
                "selection_radius_m": round(bus_candidate_selection_radius_m, 1),
                "nearest_direct_m": round(
                    min(candidate.straight_line_m for candidate in implausible_bus_candidates),
                    1,
                ),
                "nearest_graph_routed_m": round(min(implausible_bus_route_distances), 1),
                "detour_ratio_threshold": round(
                    float(params["bus_connectivity"].get("direct_fallback_detour_ratio", 3.0)),
                    3,
                ),
                "min_extra_m_threshold": round(
                    float(params["bus_connectivity"].get("direct_fallback_min_extra_m", 100.0)),
                    1,
                ),
                "shortcut_ratio_threshold": round(
                    float(params["bus_connectivity"].get("direct_fallback_shortcut_ratio", 0.5)),
                    3,
                ),
                "min_missing_m_threshold": round(
                    float(params["bus_connectivity"].get("direct_fallback_min_missing_m", 50.0)),
                    1,
                ),
                "best_expected_wait_min": (
                    round(min(expected_waits), 3) if expected_waits else None
                ),
                "geometry": "straight_line_origin_to_bus_stop_not_pedestrian_route",
                "untrusted_subscores": ["rain", "heat", "crossing"],
            }

    has_numeric_candidate = any(
        isinstance(candidate_score["total"], int | float) for candidate_score in candidate_scores
    )
    if not has_numeric_candidate and untrusted_bus_route_reasons:
        provenance["reason"] = "all_numeric_transit_candidates_rejected_by_bus_route_trust_gate"
    if not has_numeric_candidate and bus_candidates and not untrusted_bus_route_reasons:
        fallback_scores = direct_bus_fallback_candidate_scores(
            bus_candidates,
            postal_row.geometry,
            params,
            weights,
            include_geometry=include_geometry,
        )
        if fallback_scores:
            candidate_scores = fallback_scores
            expected_waits = [
                candidate.expected_wait_min
                for candidate in bus_candidates
                if candidate.expected_wait_min is not None
            ]
            provenance["direct_bus_fallback"] = {
                "reason": "no_graph_routed_transit_candidate_but_datamall_bus_stop_within_direct_radius",
                "candidate_count": len(bus_candidates),
                "radius_m": round(bus_candidate_radius_m, 1),
                "coordinate_tolerance_m": round(bus_candidate_tolerance_m, 1),
                "selection_radius_m": round(bus_candidate_selection_radius_m, 1),
                "nearest_direct_m": round(
                    min(candidate.straight_line_m for candidate in bus_candidates), 1
                ),
                "best_expected_wait_min": (
                    round(min(expected_waits), 3) if expected_waits else None
                ),
                "geometry": "straight_line_origin_to_bus_stop_not_pedestrian_route",
                "untrusted_subscores": ["rain", "heat", "crossing"],
            }

    route_distances = [float(route_result["shortest_length_m"]) for route_result in route_results]
    access_zero_m = float(params["transit_access"]["zero_credit_m"])
    provenance["routing_diagnostics"] = {
        "candidate_destination_nodes": len(destinations),
        "route_results": len(route_results),
        "candidate_scores": len(candidate_scores),
        "nearest_routed_m": round(min(route_distances), 1) if route_distances else None,
        "routes_within_access_range": sum(
            1 for distance in route_distances if distance <= access_zero_m
        ),
    }
    annotate_no_transit_reason(
        provenance,
        candidates,
        route_distances,
        candidate_scores,
        access_zero_m,
    )

    record = assemble_score_record(postal, candidate_scores, record_data_as_of, provenance)
    return add_private_origin(record, postal_row.geometry) if include_geometry else record


def score_postals(
    postal_codes: list[str] | None = None,
    limit: int | None = 5,
    include_geometry: bool = False,
    network_path: Path = NETWORK_PATH,
    postal_universe_path: Path | None = None,
) -> list[dict[str, Any]]:
    postal_limit = None if postal_codes or limit is None else max(limit * 4, limit)
    if postal_universe_path is not None:
        postal_gdf = load_postal_universe_points(
            postal_universe_path,
            postal_codes=postal_codes,
            limit=postal_limit,
        )
    else:
        postal_gdf = load_postal_points(postal_codes=postal_codes, limit=postal_limit)

    context = load_scoring_context(
        network_path=network_path,
        postal_universe_path=postal_universe_path,
    )
    return score_postal_gdf(
        postal_gdf,
        context,
        include_geometry=include_geometry,
        limit=None if postal_codes else limit,
    )


def score_postal_gdf(
    postal_gdf: gpd.GeoDataFrame,
    context: ScoringContext,
    include_geometry: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, postal_row in postal_gdf.iterrows():
        records.append(
            score_postal_row(
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
                include_geometry,
                context.network_path,
                context.postal_universe_path,
                context.base_provenance,
                context.data_as_of,
            )
        )
        if limit is not None and len(records) >= limit:
            break
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Score postals on real routed paths.")
    parser.add_argument("--postal", action="append", dest="postals", help="Postal code to score")
    parser.add_argument("--limit", type=int, default=5, help="Number of cache postals to score")
    parser.add_argument("--postal-universe", type=Path, help="processed/postal_universe_*.parquet")
    parser.add_argument("--network", type=Path, default=NETWORK_PATH)
    parser.add_argument("--include-geometry", action="store_true")
    parser.add_argument("--output", type=Path, help="Write score records JSON instead of printing")
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

    if args.full_batch:
        if not args.confirm_full_batch:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "full scoring batch requires --confirm-full-batch after checkpoint approval",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1
        if args.postal_universe is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "--full-batch requires --postal-universe",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1

    records = score_postals(
        postal_codes=args.postals,
        limit=None if args.full_batch else args.limit,
        include_geometry=args.include_geometry,
        network_path=args.network,
        postal_universe_path=args.postal_universe,
    )
    output_records = [json_safe_score_record(record) for record in records]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_records, f, indent=2, sort_keys=True)
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(args.output),
                    "records": len(records),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(json.dumps(output_records, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
