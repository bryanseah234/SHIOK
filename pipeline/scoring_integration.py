"""Integration layer that scores real routed postal-to-transit paths."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml
from pyproj import Transformer
from shapely.geometry import MultiLineString
from shapely.ops import linemerge, unary_union

from pipeline.bus import BusConnectivityIndex, BusConnectivityResult
from pipeline.routing import prepare_edges_for_routing, route_worker
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
NETWORK_PATH = PROCESSED_DIR / "network.parquet"
GEOCODE_DB_PATH = RAW_DIR / "geocode_cache.db"
MANIFEST_PATH = RAW_DIR / "manifest.json"
SubscoreValue = float | str


@dataclass(frozen=True)
class CandidateNode:
    node_type: str
    name: str
    station_name: str
    exit_code: str
    graph_node: tuple[float, float]
    straight_line_m: float
    snap_distance_m: float


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data: Any = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected YAML mapping in {path}")
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
        raise ValueError(f"expected JSON object in {MANIFEST_PATH}")
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
                )
            )
    return candidates


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
    def from_raw_data(cls, params: dict[str, Any]) -> "CrossingCounter":
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
    heat: SubscoreValue = score_heat_comfort(
        float(route_result["covered_m"]), float(route_result["length_m"])
    )
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
            "shortest_covered_ratio": round(float(route_result["shortest_covered_ratio"]), 3),
        },
        "exposure_gaps": exposure_gaps_from_path_edges(route_result.get("path_edges", [])),
        "crossing_count": crossing_count,
    }
    if include_geometry:
        candidate_score["_geometry"] = {
            "shortest": route_result.get("shortest_geometry"),
            "sheltered": route_result.get("geometry"),
            "exposure_gap_edges": route_result.get("path_edges", []),
        }
    return candidate_score


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
            "heat": "provisional_covered_only_until_phase_4",
            "crossing": (
                "real_traffic_signals_with_grade_separated_exemption"
                if crossing_counter.available
                else "pending_traffic_signal_data"
            ),
        },
    }


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

    best = max(
        scored_candidates,
        key=lambda item: (
            float(item["total"]),
            float(item["subscores"].get("rain") or 0.0),
            -float(item["paths"]["shortest_m"]),
        ),
    )
    has_pending_subscores = any(value is None for value in best["subscores"].values())

    record = {
        "postal": postal,
        "state": "SCORED_PARTIAL" if has_pending_subscores else "SCORED",
        "total": round(float(best["total"]), 1),
        "subscores": best["subscores"],
        "best_node": best["best_node"],
        "paths": best["paths"],
        "exposure_gaps": best["exposure_gaps"],
        "data_as_of": data_as_of,
        "provenance": provenance,
    }
    if "_geometry" in best:
        record["_geometry"] = best["_geometry"]
    return record


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
    nodes: list[tuple[float, float]],
    node_xy: np.ndarray,
    params: dict[str, Any],
    weights: dict[str, float],
    crossing_counter: CrossingCounter,
    bus_index: BusConnectivityIndex | None = None,
    include_geometry: bool = False,
    network_path: Path = NETWORK_PATH,
    postal_universe_path: Path | None = None,
) -> dict[str, Any]:
    postal = str(postal_row["postal_code"])
    origin_node, origin_snap_m = nearest_graph_node(postal_row.geometry, nodes, node_xy)
    candidates = select_mrt_exit_candidates(postal_row.geometry, mrt_exits_gdf, nodes, node_xy)
    bus_data_available = bus_index is not None
    provenance = build_provenance(
        params,
        crossing_counter,
        bus_data_available=bus_data_available,
        network_path=network_path,
        postal_universe_path=postal_universe_path,
    )
    provenance["origin_snap_distance_m"] = round(origin_snap_m, 1)
    data_as_of = load_manifest().get("generated_at")

    bus_result: BusConnectivityResult | None = None
    if bus_index is not None:
        bus_result = bus_index.expected_wait_for_postal(
            postal_row.geometry,
            origin_node,
            edges_dict,
            float(params["bus_connectivity"]["routed_max_m"]),
        )
        provenance["bus_connectivity"] = {
            "expected_wait_min": (
                round(bus_result.expected_wait_min, 3)
                if bus_result.expected_wait_min is not None
                else None
            ),
            "routed_stop_count": bus_result.routed_stop_count,
            "service_count": bus_result.service_count,
            "nearest_routed_m": (
                round(bus_result.nearest_routed_m, 1)
                if bus_result.nearest_routed_m is not None
                else None
            ),
        }

    if not candidates:
        record = assemble_score_record(postal, [], data_as_of, provenance)
        return add_private_origin(record, postal_row.geometry) if include_geometry else record

    destinations: list[tuple[float, float]] = []
    candidate_by_destination: dict[tuple[float, float], CandidateNode] = {}
    for candidate in candidates:
        if candidate.graph_node not in candidate_by_destination:
            destinations.append(candidate.graph_node)
            candidate_by_destination[candidate.graph_node] = candidate

    route_results = route_worker(
        (
            edges_dict,
            {origin_node: destinations},
            float(params["shelter_lambda"]),
            float(params["detour_budget"]),
        )
    )

    candidate_scores = []
    for route_result in route_results:
        candidate = candidate_by_destination[route_result["destination"]]
        crossing_count = crossing_counter.count_for_route(route_result.get("geometry"))
        candidate_scores.append(
            score_candidate_route(
                candidate,
                route_result,
                params,
                weights,
                crossing_count,
                bus_expected_wait_min=bus_result.expected_wait_min if bus_result else None,
                bus_data_available=bus_data_available,
                include_geometry=include_geometry,
            )
        )

    record = assemble_score_record(postal, candidate_scores, data_as_of, provenance)
    return add_private_origin(record, postal_row.geometry) if include_geometry else record


def score_postals(
    postal_codes: list[str] | None = None,
    limit: int | None = 5,
    include_geometry: bool = False,
    network_path: Path = NETWORK_PATH,
    postal_universe_path: Path | None = None,
) -> list[dict[str, Any]]:
    params, weights = load_params_and_weights()
    _, edges_dict, nodes, node_xy = load_network_inputs(network_path=network_path)
    mrt_exits_gdf = load_mrt_exits()
    crossing_counter = CrossingCounter.from_raw_data(params)
    bus_index = BusConnectivityIndex.from_raw_data(nodes, node_xy)

    postal_limit = None if postal_codes or limit is None else max(limit * 4, limit)
    if postal_universe_path is not None:
        postal_gdf = load_postal_universe_points(
            postal_universe_path,
            postal_codes=postal_codes,
            limit=postal_limit,
        )
    else:
        postal_gdf = load_postal_points(postal_codes=postal_codes, limit=postal_limit)

    records: list[dict[str, Any]] = []
    for _, postal_row in postal_gdf.iterrows():
        records.append(
            score_postal_row(
                postal_row,
                mrt_exits_gdf,
                edges_dict,
                nodes,
                node_xy,
                params,
                weights,
                crossing_counter,
                bus_index,
                include_geometry,
                network_path,
                postal_universe_path,
            )
        )
        if postal_codes is None and limit is not None and len(records) >= limit:
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
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, sort_keys=True)
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
        print(json.dumps(records, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
