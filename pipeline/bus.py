"""LTA DataMall bus connectivity ingestion and indexing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from pipeline.routing import RoutingGraph, route_worker

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"
USER_AGENT = "SHIOK-Index-Pipeline/1.0 (Singapore Walk-to-Transit Index)"
DATAMALL_PAGE_SIZE = 500
BUS_SOURCE_KEYS = {"bus_stops", "bus_services", "bus_routes"}


@dataclass(frozen=True)
class BusStopCandidate:
    bus_stop_code: str
    description: str
    graph_node: tuple[float, float]
    straight_line_m: float
    snap_distance_m: float
    service_headways_min: dict[tuple[str, int], float]


@dataclass(frozen=True)
class BusConnectivityResult:
    expected_wait_min: float | None
    routed_stop_count: int
    service_count: int
    nearest_routed_m: float | None


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {"generated_at": None, "sources": {}}
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, dict):
        raise TypeError(f"expected JSON object in {MANIFEST_PATH}")
    return cast(dict[str, Any], data)


def save_manifest(manifest: dict[str, Any]) -> None:
    manifest["generated_at"] = datetime.now(UTC).isoformat()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def datamall_headers() -> dict[str, str]:
    account_key = os.getenv("LTA_DATAMALL_ACCOUNT_KEY", "")
    if not account_key:
        raise ValueError("LTA_DATAMALL_ACCOUNT_KEY missing")
    return {"AccountKey": account_key, "User-Agent": USER_AGENT}


def fetch_paginated(endpoint: str, page_delay_sec: float = 0.15) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    skip = 0
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        while True:
            separator = "&" if "?" in endpoint else "?"
            url = f"{endpoint}{separator}$skip={skip}"
            response = client.get(url, headers=datamall_headers())
            response.raise_for_status()
            page = response.json().get("value", [])
            if not isinstance(page, list):
                raise TypeError(f"unexpected DataMall page shape for {endpoint}")
            records.extend(cast(list[dict[str, Any]], page))
            if len(page) < DATAMALL_PAGE_SIZE:
                break
            skip += DATAMALL_PAGE_SIZE
            time.sleep(page_delay_sec)
    return records


def write_api_records_to_raw(
    source_key: str, source_name: str, endpoint: str, records: list[dict[str, Any]]
) -> str:
    payload = {
        "source_key": source_key,
        "source_name": source_name,
        "endpoint": endpoint,
        "value": records,
    }
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    sha256 = hashlib.sha256(content).hexdigest()
    target_dir = RAW_DIR / sha256
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{source_key}.json"
    with open(target_path, "wb") as f:
        f.write(content)

    manifest = load_manifest()
    sources = manifest.setdefault("sources", {})
    sources[source_key] = {
        "source_name": source_name,
        "url_as_discovered": endpoint,
        "sha256": sha256,
        "bytes": len(content),
        "etag": None,
        "last_modified": None,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    save_manifest(manifest)
    return sha256


def ingest_bus_api_sources(sources: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source_key in sorted(BUS_SOURCE_KEYS):
        spec = sources.get(source_key)
        if not spec:
            continue
        endpoint = str(spec["endpoint"])
        source_name = str(spec["name"])
        records = fetch_paginated(endpoint)
        sha256 = write_api_records_to_raw(source_key, source_name, endpoint, records)
        print(f"[{source_key}] fetched {len(records)} records -> raw/{sha256}/{source_key}.json")
        counts[source_key] = len(records)
    return counts


def raw_json_from_manifest(source_key: str) -> list[dict[str, Any]] | None:
    manifest = load_manifest()
    source = manifest.get("sources", {}).get(source_key, {})
    sha256 = source.get("sha256")
    if not isinstance(sha256, str):
        return None
    path = RAW_DIR / sha256 / f"{source_key}.json"
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
        raise TypeError(f"unexpected raw bus payload shape: {path}")
    return cast(list[dict[str, Any]], payload["value"])


def parse_peak_frequency_minutes(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    if "-" in text:
        left, right = text.split("-", 1)
        try:
            return (float(left) + float(right)) / 2.0
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def combined_expected_wait_min(headways_min: list[float]) -> float | None:
    positive = [headway for headway in headways_min if headway > 0]
    if not positive:
        return None
    combined_headway = 1.0 / sum(1.0 / headway for headway in positive)
    return combined_headway / 2.0


def nearest_graph_node(
    point: Any, nodes: list[tuple[float, float]], node_xy: np.ndarray
) -> tuple[tuple[float, float], float]:
    xy = np.asarray([point.x, point.y], dtype=float)
    deltas = node_xy - xy
    squared = np.einsum("ij,ij->i", deltas, deltas)
    index = int(np.argmin(squared))
    return nodes[index], float(squared[index] ** 0.5)


def build_stop_service_headways(
    bus_services: list[dict[str, Any]], bus_routes: list[dict[str, Any]]
) -> dict[str, dict[tuple[str, int], float]]:
    service_headways: dict[tuple[str, int], float] = {}
    for row in bus_services:
        service_no = str(row.get("ServiceNo", "")).strip()
        direction_value = row.get("Direction")
        if direction_value is None:
            continue
        try:
            direction = int(direction_value)
        except (TypeError, ValueError):
            continue
        headway = parse_peak_frequency_minutes(row.get("AM_Peak_Freq"))
        if service_no and headway is not None:
            service_headways[(service_no, direction)] = headway

    stop_services: dict[str, dict[tuple[str, int], float]] = {}
    for row in bus_routes:
        bus_stop_code = str(row.get("BusStopCode", "")).strip()
        service_no = str(row.get("ServiceNo", "")).strip()
        direction_value = row.get("Direction")
        if direction_value is None:
            continue
        try:
            direction = int(direction_value)
        except (TypeError, ValueError):
            continue
        service_key = (service_no, direction)
        headway = service_headways.get(service_key)
        if bus_stop_code and headway is not None:
            stop_services.setdefault(bus_stop_code, {})[service_key] = headway
    return stop_services


class BusConnectivityIndex:
    def __init__(
        self,
        stops_gdf: gpd.GeoDataFrame,
        stop_service_headways: dict[str, dict[tuple[str, int], float]],
    ) -> None:
        self.stops_gdf = stops_gdf
        self.stop_service_headways = stop_service_headways
        self.stop_xy = np.asarray([(geom.x, geom.y) for geom in stops_gdf.geometry], dtype=float)

    @classmethod
    def from_raw_data(
        cls, nodes: list[tuple[float, float]], node_xy: np.ndarray
    ) -> BusConnectivityIndex | None:
        bus_stops = raw_json_from_manifest("bus_stops")
        bus_services = raw_json_from_manifest("bus_services")
        bus_routes = raw_json_from_manifest("bus_routes")
        if bus_stops is None or bus_services is None or bus_routes is None:
            return None

        stop_service_headways = build_stop_service_headways(bus_services, bus_routes)
        stops_df = pd.DataFrame(bus_stops)
        stops_gdf = gpd.GeoDataFrame(
            stops_df,
            geometry=gpd.points_from_xy(stops_df["Longitude"], stops_df["Latitude"]),
            crs="EPSG:4326",
        ).to_crs("EPSG:3414")

        graph_nodes: list[tuple[float, float]] = []
        snap_distances: list[float] = []
        for geom in stops_gdf.geometry:
            graph_node, snap_distance = nearest_graph_node(geom, nodes, node_xy)
            graph_nodes.append(graph_node)
            snap_distances.append(snap_distance)
        stops_gdf["graph_node"] = graph_nodes
        stops_gdf["snap_distance_m"] = snap_distances
        return cls(stops_gdf, stop_service_headways)

    def nearby_stop_candidates(
        self, postal_point: Any, straight_line_radius_m: float
    ) -> list[BusStopCandidate]:
        xy = np.asarray([postal_point.x, postal_point.y], dtype=float)
        deltas = self.stop_xy - xy
        distances = np.sqrt(np.einsum("ij,ij->i", deltas, deltas))
        candidate_indices = np.flatnonzero(distances <= straight_line_radius_m)

        candidates: list[BusStopCandidate] = []
        for index in candidate_indices:
            row = self.stops_gdf.iloc[int(index)]
            bus_stop_code = str(row["BusStopCode"]).strip()
            service_headways = self.stop_service_headways.get(bus_stop_code, {})
            if not service_headways:
                continue
            candidates.append(
                BusStopCandidate(
                    bus_stop_code=bus_stop_code,
                    description=str(row.get("Description", "")),
                    graph_node=cast(tuple[float, float], row["graph_node"]),
                    straight_line_m=float(distances[index]),
                    snap_distance_m=float(row["snap_distance_m"]),
                    service_headways_min=service_headways,
                )
            )
        return candidates

    def expected_wait_for_postal(
        self,
        postal_point: Any,
        origin_node: tuple[float, float],
        edges_dict: dict[str, list[Any]],
        routed_max_m: float,
        straight_line_radius_m: float | None = None,
        routing_graph: RoutingGraph | None = None,
    ) -> BusConnectivityResult:
        radius = (
            straight_line_radius_m if straight_line_radius_m is not None else routed_max_m + 125.0
        )
        candidates = self.nearby_stop_candidates(postal_point, radius)
        if not candidates:
            return BusConnectivityResult(None, 0, 0, None)

        candidate_by_node: dict[tuple[float, float], list[BusStopCandidate]] = {}
        for candidate in candidates:
            candidate_by_node.setdefault(candidate.graph_node, []).append(candidate)

        od_pairs = {origin_node: sorted(candidate_by_node)}
        if routing_graph is None:
            route_results = route_worker((edges_dict, od_pairs, 0.0, 1.0))
        else:
            route_results = routing_graph.route(od_pairs, 0.0, 1.0, include_geometry=False)

        qualifying_headways: dict[tuple[str, int], float] = {}
        routed_stop_count = 0
        routed_distances: list[float] = []
        for route_result in route_results:
            routed_m = float(route_result["shortest_length_m"])
            if routed_m > routed_max_m:
                continue
            routed_distances.append(routed_m)
            routed_stop_count += len(candidate_by_node[route_result["destination"]])
            for stop_candidate in candidate_by_node[route_result["destination"]]:
                for service_key, headway in stop_candidate.service_headways_min.items():
                    if service_key not in qualifying_headways:
                        qualifying_headways[service_key] = headway
                    else:
                        qualifying_headways[service_key] = min(
                            qualifying_headways[service_key], headway
                        )

        expected_wait = combined_expected_wait_min(list(qualifying_headways.values()))
        nearest_routed = min(routed_distances) if routed_distances else None
        return BusConnectivityResult(
            expected_wait_min=expected_wait,
            routed_stop_count=routed_stop_count,
            service_count=len(qualifying_headways),
            nearest_routed_m=nearest_routed,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest DataMall bus API datasets.")
    parser.add_argument("action", choices=["ingest"])
    args = parser.parse_args()

    if args.action == "ingest":
        from pipeline.fetch import load_sources

        counts = ingest_bus_api_sources(load_sources())
        print(json.dumps(counts, indent=2, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
