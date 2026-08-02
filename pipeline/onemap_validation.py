"""OneMap walk-routing validation gate planning and cached evaluation."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLE_DIR = PROJECT_ROOT / "web" / "public" / "data" / "generated_20260801_165500"
DEFAULT_SAMPLE_OUTPUT = PROJECT_ROOT / "qa" / "onemap_validation_sample_2000.json"
DEFAULT_REPORT_OUTPUT = PROJECT_ROOT / "qa" / "onemap_validation_cached_report.json"
DEFAULT_COLLECT_OUTPUT = PROJECT_ROOT / "qa" / "onemap_validation_collect_report.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "raw" / "validation" / "onemap_walk_od"
DEFAULT_POSTAL_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)
DEFAULT_SAMPLE_SIZE = 2000
DEFAULT_ONEMAP_DELAY_SEC = 2.0
MEDIAN_THRESHOLD_PCT = 10.0
P95_THRESHOLD_PCT = 25.0
TOP_OUTLIERS_PREVIEW_LIMIT = 100
DIRECT_DISTANCE_TOLERANCE_M = 5.0
MATERIAL_SHORTER_THAN_DIRECT_RATIO = 0.8
ONEMAP_AUTH_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
ONEMAP_ROUTE_URL = "https://www.onemap.gov.sg/api/public/routingsvc/route"
USER_AGENT = "SHIOK-Index-OneMap-Validation/1.0"

FetchRoute = Callable[[dict[str, Any]], dict[str, Any]]


def resolve_json_path(path: Path) -> Path | None:
    if path.is_file():
        return path
    gz_path = Path(f"{path}.gz")
    if gz_path.is_file():
        return gz_path
    return None


def read_json(path: Path) -> Any:
    actual_path = resolve_json_path(path) or path
    if actual_path.suffix == ".gz":
        with gzip.open(actual_path, "rt", encoding="utf-8-sig") as f:
            return json.load(f)
    with actual_path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode a Google encoded polyline into ``(lat, lon)`` points."""

    coordinates: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0
    factor = 10**precision

    while index < len(encoded):
        result = 0
        shift = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lat += ~(result >> 1) if result & 1 else result >> 1

        result = 0
        shift = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lon += ~(result >> 1) if result & 1 else result >> 1

        coordinates.append((lat / factor, lon / factor))

    return coordinates


def area_from_score_shard(path: Path) -> str:
    return re.sub(r"_PART_\d{3}$", "", path.stem)


def stable_rank(seed: str, area: str, postal: str) -> str:
    return hashlib.sha256(f"{seed}|{area}|{postal}".encode()).hexdigest()


def route_cache_key(start: dict[str, float], end: dict[str, float]) -> str:
    payload = {
        "route_type": "walk",
        "start": [round(start["lat"], 6), round(start["lon"], 6)],
        "end": [round(end["lat"], 6), round(end["lon"], 6)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def valid_lat_lon(lat: Any, lon: Any) -> tuple[float, float] | None:
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return None
    if not (1.1 <= lat_f <= 1.6 and 103.5 <= lon_f <= 104.2):
        return None
    return lat_f, lon_f


def load_postal_origin_index(postal_universe_path: Path) -> dict[str, dict[str, float]]:
    if not postal_universe_path.is_file():
        return {}
    frame = pd.read_parquet(postal_universe_path, columns=["postal_code", "lat", "lon", "status"])
    origins: dict[str, dict[str, float]] = {}
    for row in frame.itertuples(index=False):
        postal = str(row.postal_code).zfill(6)
        lat_lon = valid_lat_lon(row.lat, row.lon)
        if lat_lon is None or row.status != "READY_TO_SCORE":
            continue
        lat, lon = lat_lon
        origins[postal] = {"lat": round(lat, 6), "lon": round(lon, 6)}
    return origins


def transit_key(*parts: Any) -> str:
    return "|".join(str(part or "").strip().lower() for part in parts)


def load_transit_poi_index(bundle_dir: Path) -> dict[str, dict[str, float]]:
    path = bundle_dir / "transit" / "pois.json"
    if resolve_json_path(path) is None:
        return {}
    payload = read_json(path)
    features = payload.get("features", []) if isinstance(payload, dict) else []
    index: dict[str, dict[str, float]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or not isinstance(properties, dict):
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        lat_lon = valid_lat_lon(coordinates[1], coordinates[0])
        if lat_lon is None:
            continue
        lat, lon = lat_lon
        point = {"lat": round(lat, 6), "lon": round(lon, 6)}
        kind = properties.get("kind")
        code = properties.get("code")
        name = properties.get("name")
        station = properties.get("station")
        exit_code = properties.get("exit")
        keys = [
            transit_key(kind, code),
            transit_key(kind, name),
            transit_key(kind, station, exit_code),
        ]
        if kind == "mrt_exit":
            keys.extend(
                [
                    transit_key("mrt_lrt_exit", station, exit_code),
                    transit_key("mrt_lrt_exit", name),
                ]
            )
        if kind == "bus_stop":
            keys.extend([transit_key("bus_stop", code), transit_key("bus_stop", name)])
        for key in keys:
            if key.replace("|", ""):
                index[key] = point
    return index


def destination_from_best_node(
    best_node: dict[str, Any], transit_index: dict[str, dict[str, float]]
) -> dict[str, float] | None:
    node_type = best_node.get("type")
    exit_code = best_node.get("exit")
    name = best_node.get("name")
    station = best_node.get("station")
    kind = "mrt_exit" if node_type == "mrt_lrt_exit" else node_type
    candidate_keys = [
        transit_key(kind, exit_code),
        transit_key(kind, station, exit_code),
        transit_key(kind, name),
        transit_key(node_type, station, exit_code),
        transit_key(node_type, name),
    ]
    for key in candidate_keys:
        point = transit_index.get(key)
        if point is not None:
            return point
    return None


def score_shard_paths(bundle_dir: Path) -> list[Path]:
    scores_dir = bundle_dir / "scores"
    return sorted(
        path
        for path in scores_dir.glob("*.json")
        if path.name not in {"index.json", "prefix-index.json"}
    )


def iter_score_candidates(
    bundle_dir: Path, *, route_mode: str, geom_postal_index: dict[str, str]
) -> Iterable[dict[str, Any]]:
    for path in score_shard_paths(bundle_dir):
        area = area_from_score_shard(path)
        records = read_json(path)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            postal = str(record.get("postal", "")).zfill(6)
            route_record = record
            if route_mode != "best_transit":
                route_options = record.get("route_options")
                if isinstance(route_options, dict) and isinstance(
                    route_options.get(route_mode), dict
                ):
                    route_record = route_options[route_mode]
            paths = route_record.get("paths")
            if (
                record.get("state") != "SCORED"
                or not isinstance(paths, dict)
                or postal not in geom_postal_index
            ):
                continue
            shortest_m = paths.get("shortest_m")
            if not isinstance(shortest_m, int | float) or shortest_m <= 0:
                continue
            best_node = route_record.get("best_node")
            if not isinstance(best_node, dict):
                continue
            routing_type = str(paths.get("routing_type") or "unknown")
            yield {
                "postal": postal,
                "area": area,
                "state": route_record.get("state", record.get("state")),
                "total": route_record.get("total", record.get("total")),
                "best_node": {
                    "type": best_node.get("type"),
                    "exit": best_node.get("exit"),
                    "name": best_node.get("name"),
                    "station": best_node.get("station"),
                },
                "project_shortest_m": round(float(shortest_m), 1),
                "routing_type": routing_type,
                "route_trust": validation_route_trust(
                    node_type=str(best_node.get("type") or "unknown"),
                    routing_type=routing_type,
                ),
            }


def validation_route_trust(*, node_type: str, routing_type: str) -> str:
    if routing_type == "direct_bus_fallback_unrouted":
        return "partial_unrouted_bus_fallback"
    if routing_type.endswith("_with_bus_stop_access_connector") or routing_type.endswith(
        "_with_mrt_lrt_exit_access_connector"
    ):
        return "graph_route_with_endpoint_connector"
    if node_type == "bus_stop":
        return "graph_routed_bus_stop"
    if node_type == "mrt_lrt_exit":
        return "graph_routed_mrt_lrt"
    return "graph_routed_other"


def allocate_area_quotas(
    buckets: dict[str, list[dict[str, Any]]], sample_size: int
) -> dict[str, int]:
    total = sum(len(items) for items in buckets.values())
    if total == 0 or sample_size <= 0:
        return {}
    target = min(sample_size, total)
    areas = sorted(buckets)
    quotas: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for area in areas:
        exact = target * len(buckets[area]) / total
        quota = min(len(buckets[area]), max(1, math.floor(exact)))
        quotas[area] = quota
        assigned += quota
        remainders.append((exact - math.floor(exact), area))

    while assigned > target:
        for _fraction, area in sorted(remainders, key=lambda item: (item[0], item[1])):
            if assigned <= target:
                break
            if quotas[area] > 0:
                quotas[area] -= 1
                assigned -= 1

    while assigned < target:
        changed = False
        for _fraction, area in sorted(remainders, key=lambda item: (-item[0], item[1])):
            if assigned >= target:
                break
            if quotas[area] < len(buckets[area]):
                quotas[area] += 1
                assigned += 1
                changed = True
        if not changed:
            break
    return quotas


def load_geom_record(
    bundle_dir: Path,
    *,
    postal: str,
    geom_postal_index: dict[str, str],
    shard_cache: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    shard = geom_postal_index.get(postal)
    if not shard:
        return None
    if shard not in shard_cache:
        shard_path = bundle_dir / "geom" / "h3" / f"{shard}.json"
        if resolve_json_path(shard_path) is None:
            return None
        payload = read_json(shard_path)
        if not isinstance(payload, list):
            return None
        shard_cache[shard] = payload
    return next(
        (
            item
            for item in shard_cache[shard]
            if isinstance(item, dict) and item.get("postal") == postal
        ),
        None,
    )


def route_points_from_geom(
    geom_record: dict[str, Any], *, route_mode: str, route_kind: str = "shortest"
) -> list[tuple[float, float]]:
    route_geom = geom_record
    route_options = geom_record.get("route_options")
    if route_mode != "best_transit" and isinstance(route_options, dict):
        maybe_route = route_options.get(route_mode)
        if isinstance(maybe_route, dict):
            route_geom = maybe_route
    elif isinstance(route_options, dict) and isinstance(route_options.get("best_transit"), dict):
        route_geom = route_options["best_transit"]

    encoded_parts = route_geom.get(f"{route_kind}_parts")
    if isinstance(encoded_parts, list) and encoded_parts:
        points: list[tuple[float, float]] = []
        for part in encoded_parts:
            if isinstance(part, str) and part:
                points.extend(decode_polyline(part))
        return points
    encoded = route_geom.get(route_kind)
    if isinstance(encoded, str) and encoded:
        return decode_polyline(encoded)
    return []


def attach_endpoints(
    bundle_dir: Path,
    candidates: list[dict[str, Any]],
    *,
    route_mode: str,
    geom_postal_index: dict[str, str],
    origin_index: dict[str, dict[str, float]],
    transit_index: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    shard_cache: dict[str, list[dict[str, Any]]] = {}
    samples: list[dict[str, Any]] = []
    for candidate in candidates:
        postal = str(candidate["postal"])
        geom = load_geom_record(
            bundle_dir,
            postal=postal,
            geom_postal_index=geom_postal_index,
            shard_cache=shard_cache,
        )
        if not geom:
            continue
        points = route_points_from_geom(geom, route_mode=route_mode, route_kind="shortest")
        origin = origin_index.get(postal)
        destination = destination_from_best_node(
            candidate.get("best_node", {}) if isinstance(candidate.get("best_node"), dict) else {},
            transit_index,
        )
        endpoint_source = "postal_universe_to_transit_poi"
        if origin is None or destination is None:
            if len(points) < 2:
                continue
            origin = {"lat": round(points[0][0], 6), "lon": round(points[0][1], 6)}
            destination = {"lat": round(points[-1][0], 6), "lon": round(points[-1][1], 6)}
            endpoint_source = "route_geometry_fallback"
        if origin == destination:
            continue
        sample = {
            **candidate,
            "route_mode": route_mode,
            "route_kind": "shortest",
            "endpoint_source": endpoint_source,
            "start": origin,
            "end": destination,
        }
        sample["cache_key"] = route_cache_key(origin, destination)
        samples.append(sample)
    return samples


def build_validation_sample(
    *,
    bundle_dir: Path,
    postal_universe_path: Path = DEFAULT_POSTAL_UNIVERSE,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: str = "shiok-onemap-validation-v1",
    route_mode: str = "best_transit",
    onemap_delay_sec: float = DEFAULT_ONEMAP_DELAY_SEC,
) -> dict[str, Any]:
    geom_index = read_json(bundle_dir / "geom" / "postal-index.json")
    if not isinstance(geom_index, dict):
        raise TypeError("geom/postal-index.json must contain a JSON object")
    origin_index = load_postal_origin_index(postal_universe_path)
    transit_index = load_transit_poi_index(bundle_dir)

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in iter_score_candidates(
        bundle_dir, route_mode=route_mode, geom_postal_index=geom_index
    ):
        buckets[str(candidate["area"])].append(candidate)

    for area, items in buckets.items():
        items.sort(key=lambda item: stable_rank(seed, area, str(item["postal"])))

    endpoint_buckets: dict[str, list[dict[str, Any]]] = {}
    for area in sorted(buckets):
        endpoint_buckets[area] = attach_endpoints(
            bundle_dir,
            buckets[area],
            route_mode=route_mode,
            geom_postal_index=geom_index,
            origin_index=origin_index,
            transit_index=transit_index,
        )
    endpoint_buckets = {area: samples for area, samples in endpoint_buckets.items() if samples}

    quotas = allocate_area_quotas(endpoint_buckets, sample_size)
    samples = [
        sample for area in sorted(quotas) for sample in endpoint_buckets[area][: quotas[area]]
    ]
    samples.sort(key=lambda item: stable_rank(seed, str(item["area"]), str(item["postal"])))
    raw_candidate_records = sum(len(items) for items in buckets.values())
    eligible_records = sum(len(items) for items in endpoint_buckets.values())
    projected_seconds = len(samples) * onemap_delay_sec
    return {
        "ok": len(samples) == min(sample_size, eligible_records),
        "generated_at": datetime.now(UTC).isoformat(),
        "bundle": bundle_dir.name,
        "route_mode": route_mode,
        "sample_size_requested": sample_size,
        "sample_size": len(samples),
        "eligible_records": eligible_records,
        "raw_candidate_records": raw_candidate_records,
        "skipped_endpoint_records": raw_candidate_records - eligible_records,
        "area_count": len(endpoint_buckets),
        "origin_index_size": len(origin_index),
        "transit_index_size": len(transit_index),
        "area_quotas": quotas,
        "will_call_onemap": False,
        "onemap_delay_sec": onemap_delay_sec,
        "projected_wall_clock_seconds": round(projected_seconds, 1),
        "projected_wall_clock_minutes": round(projected_seconds / 60, 1),
        "thresholds": {
            "median_abs_pct_delta_max": MEDIAN_THRESHOLD_PCT,
            "p95_abs_pct_delta_max": P95_THRESHOLD_PCT,
        },
        "cache_dir": str(DEFAULT_CACHE_DIR.relative_to(PROJECT_ROOT)),
        "samples": samples,
    }


def extract_onemap_distance_m(payload: Any) -> float | None:
    if isinstance(payload, dict):
        for key in ("total_distance", "totalDistance", "distance", "Distance"):
            value = payload.get(key)
            if isinstance(value, int | float):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    pass
        for value in payload.values():
            found = extract_onemap_distance_m(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = extract_onemap_distance_m(value)
            if found is not None:
                return found
    return None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct / 100
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[int(index)]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def summarize_delta_groups(
    results: list[dict[str, Any]], *, group_key: str, limit: int | None = None
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        key = str(result.get(group_key) or "unknown")
        groups[key].append(result)

    summaries: list[dict[str, Any]] = []
    for key, items in groups.items():
        deltas = [float(item["abs_pct_delta"]) for item in items]
        metre_deltas = [float(item["abs_delta_m"]) for item in items]
        median = percentile(deltas, 50)
        p95 = percentile(deltas, 95)
        median_m = percentile(metre_deltas, 50)
        p95_m = percentile(metre_deltas, 95)
        summaries.append(
            {
                group_key: key,
                "count": len(items),
                "median_abs_pct_delta": round(median, 3) if median is not None else None,
                "p95_abs_pct_delta": round(p95, 3) if p95 is not None else None,
                "max_abs_pct_delta": round(max(deltas), 3),
                "median_abs_delta_m": round(median_m, 1) if median_m is not None else None,
                "p95_abs_delta_m": round(p95_m, 1) if p95_m is not None else None,
                "max_abs_delta_m": round(max(metre_deltas), 1),
                "over_25_pct_count": sum(delta > 25 for delta in deltas),
                "over_50_pct_count": sum(delta > 50 for delta in deltas),
            }
        )
    summaries.sort(
        key=lambda item: (
            -(float(item["p95_abs_pct_delta"]) if item["p95_abs_pct_delta"] is not None else -1),
            -int(item["count"]),
            str(item[group_key]),
        )
    )
    return summaries[:limit] if limit is not None else summaries


def validation_metric_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "count": 0,
            "median_abs_pct_delta": None,
            "p95_abs_pct_delta": None,
            "median_abs_delta_m": None,
            "p95_abs_delta_m": None,
            "over_25_pct_count": 0,
            "over_50_pct_count": 0,
            "thresholds_passed": None,
        }

    deltas = [float(item["abs_pct_delta"]) for item in results]
    metre_deltas = [float(item["abs_delta_m"]) for item in results]
    median = percentile(deltas, 50)
    p95 = percentile(deltas, 95)
    median_m = percentile(metre_deltas, 50)
    p95_m = percentile(metre_deltas, 95)
    thresholds_passed = (
        median is not None
        and p95 is not None
        and median <= MEDIAN_THRESHOLD_PCT
        and p95 <= P95_THRESHOLD_PCT
    )
    return {
        "count": len(results),
        "median_abs_pct_delta": round(median, 3) if median is not None else None,
        "p95_abs_pct_delta": round(p95, 3) if p95 is not None else None,
        "median_abs_delta_m": round(median_m, 1) if median_m is not None else None,
        "p95_abs_delta_m": round(p95_m, 1) if p95_m is not None else None,
        "over_25_pct_count": sum(delta > 25 for delta in deltas),
        "over_50_pct_count": sum(delta > 50 for delta in deltas),
        "thresholds_passed": thresholds_passed,
    }


def validation_subset_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    graph_routed_trust = {
        "graph_routed_bus_stop",
        "graph_routed_mrt_lrt",
        "graph_routed_other",
    }
    plausible_distance = [row for row in results if row.get("distance_sanity") == "plausible"]
    subsets = {
        "all_valid_cached": results,
        "graph_routed_without_endpoint_connector": [
            row for row in results if row.get("route_trust") in graph_routed_trust
        ],
        "graph_routed_without_endpoint_connector_plausible_onemap_distance": [
            row for row in plausible_distance if row.get("route_trust") in graph_routed_trust
        ],
        "graph_routed_bus_stop": [
            row for row in results if row.get("route_trust") == "graph_routed_bus_stop"
        ],
        "graph_routed_bus_stop_plausible_onemap_distance": [
            row for row in plausible_distance if row.get("route_trust") == "graph_routed_bus_stop"
        ],
        "graph_routed_mrt_lrt": [
            row for row in results if row.get("route_trust") == "graph_routed_mrt_lrt"
        ],
        "graph_routed_mrt_lrt_plausible_onemap_distance": [
            row for row in plausible_distance if row.get("route_trust") == "graph_routed_mrt_lrt"
        ],
        "endpoint_connector": [
            row
            for row in results
            if row.get("route_trust") == "graph_route_with_endpoint_connector"
        ],
        "endpoint_connector_plausible_onemap_distance": [
            row
            for row in plausible_distance
            if row.get("route_trust") == "graph_route_with_endpoint_connector"
        ],
        "plausible_onemap_distance": plausible_distance,
        "non_tiny_onemap_walk_gt_20m": [
            row for row in results if row.get("onemap_walk_bucket") != "le_20m"
        ],
    }
    return {
        name: validation_metric_summary(rows)
        for name, rows in sorted(subsets.items(), key=lambda item: item[0])
    }


def signed_delta_direction(signed_delta_pct: float) -> str:
    if signed_delta_pct > 0:
        return "project_longer_than_onemap"
    if signed_delta_pct < 0:
        return "project_shorter_than_onemap"
    return "same_length"


def onemap_walk_bucket(onemap_m: float) -> str:
    if onemap_m <= 20:
        return "le_20m"
    if onemap_m <= 50:
        return "gt_20m_le_50m"
    if onemap_m <= 100:
        return "gt_50m_le_100m"
    return "gt_100m"


def haversine_distance_m(start: Any, end: Any) -> float | None:
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    try:
        start_lat = math.radians(float(start["lat"]))
        start_lon = math.radians(float(start["lon"]))
        end_lat = math.radians(float(end["lat"]))
        end_lon = math.radians(float(end["lon"]))
    except (KeyError, TypeError, ValueError):
        return None

    delta_lat = end_lat - start_lat
    delta_lon = end_lon - start_lon
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(start_lat) * math.cos(end_lat) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * 6_371_008.8 * math.asin(math.sqrt(haversine))


def onemap_distance_sanity(onemap_m: float, direct_distance_m: float | None) -> str:
    if direct_distance_m is None:
        return "missing_coordinates"
    if direct_distance_m <= 0:
        return "zero_direct_distance"
    if (
        onemap_m + DIRECT_DISTANCE_TOLERANCE_M
        < direct_distance_m * MATERIAL_SHORTER_THAN_DIRECT_RATIO
    ):
        return "onemap_materially_shorter_than_direct"
    if onemap_m + DIRECT_DISTANCE_TOLERANCE_M < direct_distance_m:
        return "onemap_slightly_shorter_than_direct"
    return "plausible"


def top_outliers_by_group(
    results: list[dict[str, Any]], *, group_key: str, limit: int
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        groups[str(result.get(group_key) or "unknown")].append(result)
    return {
        key: sorted(items, key=lambda item: float(item["abs_pct_delta"]), reverse=True)[:limit]
        for key, items in sorted(groups.items())
    }


def evaluate_cached_results(
    sample_payload: dict[str, Any],
    cache_dir: Path,
    *,
    include_results: bool = False,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[dict[str, Any]] = []
    for sample in sample_payload.get("samples", []):
        if not isinstance(sample, dict):
            continue
        cache_key = str(sample.get("cache_key", ""))
        cache_path = cache_dir / f"{cache_key}.json"
        if not cache_path.is_file():
            missing.append(str(sample.get("postal", "")))
            continue
        onemap_m = extract_onemap_distance_m(read_json(cache_path))
        project_m = sample.get("project_shortest_m")
        raw_best_node = sample.get("best_node")
        best_node: dict[str, Any] = raw_best_node if isinstance(raw_best_node, dict) else {}
        if (
            not isinstance(onemap_m, int | float)
            or not isinstance(project_m, int | float)
            or float(onemap_m) <= 0
        ):
            invalid.append(
                {
                    "postal": sample.get("postal"),
                    "area": sample.get("area"),
                    "cache_key": cache_key,
                    "project_shortest_m": project_m,
                    "onemap_walk_m": onemap_m,
                    "best_node_type": best_node.get("type"),
                    "best_node_name": best_node.get("name") or best_node.get("station"),
                    "endpoint_source": sample.get("endpoint_source"),
                    "reason": "missing_or_non_positive_distance",
                }
            )
            continue
        delta_pct = abs(float(project_m) - float(onemap_m)) / float(onemap_m) * 100
        signed_delta_pct = (float(project_m) - float(onemap_m)) / float(onemap_m) * 100
        signed_delta_m = float(project_m) - float(onemap_m)
        abs_delta_m = abs(signed_delta_m)
        direct_distance_m = haversine_distance_m(sample.get("start"), sample.get("end"))
        distance_sanity = onemap_distance_sanity(float(onemap_m), direct_distance_m)
        results.append(
            {
                "postal": sample.get("postal"),
                "area": sample.get("area"),
                "state": sample.get("state"),
                "cache_key": cache_key,
                "start": sample.get("start"),
                "end": sample.get("end"),
                "direct_distance_m": (
                    round(direct_distance_m, 1) if direct_distance_m is not None else None
                ),
                "onemap_vs_direct_delta_m": (
                    round(float(onemap_m) - direct_distance_m, 1)
                    if direct_distance_m is not None
                    else None
                ),
                "distance_sanity": distance_sanity,
                "project_shortest_m": round(float(project_m), 1),
                "onemap_walk_m": round(float(onemap_m), 1),
                "onemap_walk_bucket": onemap_walk_bucket(float(onemap_m)),
                "abs_delta_m": round(abs_delta_m, 1),
                "signed_delta_m": round(signed_delta_m, 1),
                "abs_pct_delta": round(delta_pct, 3),
                "signed_pct_delta": round(signed_delta_pct, 3),
                "direction": signed_delta_direction(signed_delta_pct),
                "best_node_type": best_node.get("type"),
                "best_node_name": best_node.get("name") or best_node.get("station"),
                "routing_type": sample.get("routing_type"),
                "route_trust": sample.get("route_trust"),
                "endpoint_source": sample.get("endpoint_source"),
            }
        )

    deltas = [float(item["abs_pct_delta"]) for item in results]
    metre_deltas = [float(item["abs_delta_m"]) for item in results]
    median = percentile(deltas, 50)
    p95 = percentile(deltas, 95)
    median_m = percentile(metre_deltas, 50)
    p95_m = percentile(metre_deltas, 95)
    distance_sanity_counts = Counter(
        str(item.get("distance_sanity") or "unknown") for item in results
    )
    gate_passed = (
        len(results) == int(sample_payload.get("sample_size", 0))
        and not invalid
        and median is not None
        and p95 is not None
        and median <= MEDIAN_THRESHOLD_PCT
        and p95 <= P95_THRESHOLD_PCT
    )
    report = {
        "ok": gate_passed,
        "generated_at": datetime.now(UTC).isoformat(),
        "bundle": sample_payload.get("bundle"),
        "sample_size": sample_payload.get("sample_size"),
        "cache_dir": str(cache_dir),
        "cached_results": len(results),
        "missing_cache_results": len(missing),
        "invalid_cache_results": len(invalid),
        "missing_cache_postals_preview": missing[:20],
        "invalid_cache_preview": invalid[:20],
        "median_abs_pct_delta": round(median, 3) if median is not None else None,
        "p95_abs_pct_delta": round(p95, 3) if p95 is not None else None,
        "median_abs_delta_m": round(median_m, 1) if median_m is not None else None,
        "p95_abs_delta_m": round(p95_m, 1) if p95_m is not None else None,
        "thresholds": {
            "median_abs_pct_delta_max": MEDIAN_THRESHOLD_PCT,
            "p95_abs_pct_delta_max": P95_THRESHOLD_PCT,
        },
        "gate_passed": gate_passed,
        "subset_summary": validation_subset_summary(results),
        "distance_sanity_summary": dict(sorted(distance_sanity_counts.items())),
        "route_trust_summary": summarize_delta_groups(results, group_key="route_trust"),
        "routing_type_summary": summarize_delta_groups(results, group_key="routing_type"),
        "transit_type_summary": summarize_delta_groups(results, group_key="best_node_type"),
        "direction_summary": summarize_delta_groups(results, group_key="direction"),
        "onemap_walk_bucket_summary": summarize_delta_groups(
            results,
            group_key="onemap_walk_bucket",
        ),
        "area_summary": summarize_delta_groups(results, group_key="area", limit=20),
        "top_outliers_preview": sorted(
            results, key=lambda item: float(item["abs_pct_delta"]), reverse=True
        )[:TOP_OUTLIERS_PREVIEW_LIMIT],
        "top_outliers_by_direction": top_outliers_by_group(
            results,
            group_key="direction",
            limit=TOP_OUTLIERS_PREVIEW_LIMIT,
        ),
        "results_preview": results[:20],
    }
    if include_results:
        report["results"] = results
    return report


def get_onemap_token(client: httpx.Client) -> str | None:
    email = os.environ.get("ONEMAP_EMAIL")
    password = os.environ.get("ONEMAP_PASSWORD")
    if not email or not password:
        return None
    response = client.post(
        ONEMAP_AUTH_URL,
        json={"email": email, "password": password},
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload: Any = response.json()
    token = payload.get("access_token") if isinstance(payload, dict) else None
    return str(token) if token else None


def fetch_onemap_walk_route(
    sample: dict[str, Any], client: httpx.Client, token: str | None
) -> dict[str, Any]:
    start = sample.get("start")
    end = sample.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise TypeError(f"sample missing start/end coordinates: {sample.get('postal')}")
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.get(
        ONEMAP_ROUTE_URL,
        params={
            "start": f"{float(start['lat'])},{float(start['lon'])}",
            "end": f"{float(end['lat'])},{float(end['lon'])}",
            "routeType": "walk",
        },
        headers=headers,
    )
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, dict):
        raise TypeError("OneMap route response must be a JSON object")
    return payload


def collect_onemap_walk_cache(
    sample_payload: dict[str, Any],
    *,
    cache_dir: Path,
    delay_sec: float = DEFAULT_ONEMAP_DELAY_SEC,
    limit: int | None = None,
    dry_run: bool = False,
    confirm_onemap_collection: bool = False,
    fetcher: FetchRoute | None = None,
) -> tuple[bool, dict[str, Any]]:
    samples = [item for item in sample_payload.get("samples", []) if isinstance(item, dict)]
    existing = [
        sample for sample in samples if (cache_dir / f"{sample.get('cache_key')}.json").is_file()
    ]
    pending = [
        sample
        for sample in samples
        if not (cache_dir / f"{sample.get('cache_key')}.json").is_file()
    ]
    if limit is not None:
        pending = pending[: max(0, int(limit))]

    report: dict[str, Any] = {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "bundle": sample_payload.get("bundle"),
        "cache_dir": str(cache_dir),
        "dry_run": dry_run,
        "confirm_onemap_collection": confirm_onemap_collection,
        "delay_sec": delay_sec,
        "sample_size": len(samples),
        "existing_cache_results": len(existing),
        "queued_requests": len(pending),
        "http_requests": 0,
        "written_cache_results": 0,
        "errors": [],
        "will_call_onemap": bool(pending and not dry_run and confirm_onemap_collection),
    }
    if delay_sec < 0:
        report["ok"] = False
        report["errors"].append("delay_sec must be >= 0")
        return False, report
    if not dry_run and not confirm_onemap_collection:
        report["ok"] = False
        report["errors"].append("OneMap validation collection requires --confirm-onemap-collection")
        return False, report
    if dry_run or not pending:
        return True, report

    cache_dir.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=30.0, follow_redirects=True) if fetcher is None else None
    token = None
    try:
        if client is not None:
            token = get_onemap_token(client)
            if token is None:
                report["ok"] = False
                report["errors"].append("ONEMAP_EMAIL/ONEMAP_PASSWORD token is required")
                return False, report

        for sample in pending:
            cache_key = str(sample.get("cache_key", ""))
            if not cache_key:
                report["errors"].append(f"sample missing cache_key: {sample.get('postal')}")
                continue
            try:
                if fetcher is not None:
                    response_payload = fetcher(sample)
                else:
                    if client is None:
                        raise RuntimeError("missing OneMap HTTP client")
                    response_payload = fetch_onemap_walk_route(sample, client, token)
                cache_payload = {
                    "source": "onemap_walk_route_validation",
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "sample": {
                        "postal": sample.get("postal"),
                        "area": sample.get("area"),
                        "project_shortest_m": sample.get("project_shortest_m"),
                        "start": sample.get("start"),
                        "end": sample.get("end"),
                    },
                    "response": response_payload,
                }
                write_json(cache_dir / f"{cache_key}.json", cache_payload)
                report["written_cache_results"] += 1
            except httpx.HTTPStatusError as exc:
                report["errors"].append(
                    f"{sample.get('postal')}: OneMap HTTP {exc.response.status_code}"
                )
                if exc.response.status_code == 429:
                    time.sleep(max(60.0, delay_sec * 5.0))
            except (httpx.HTTPError, TypeError, ValueError, RuntimeError) as exc:
                report["errors"].append(f"{sample.get('postal')}: {exc}")
            finally:
                report["http_requests"] += 1
                if delay_sec > 0:
                    time.sleep(delay_sec)
    finally:
        if client is not None:
            client.close()

    report["ok"] = not report["errors"]
    return bool(report["ok"]), report


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan/evaluate OneMap walk validation.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR)
    plan.add_argument("--postal-universe", type=Path, default=DEFAULT_POSTAL_UNIVERSE)
    plan.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    plan.add_argument("--seed", default="shiok-onemap-validation-v1")
    plan.add_argument("--route-mode", default="best_transit")
    plan.add_argument("--onemap-delay-sec", type=float, default=DEFAULT_ONEMAP_DELAY_SEC)
    plan.add_argument("--output", type=Path, default=DEFAULT_SAMPLE_OUTPUT)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE_OUTPUT)
    evaluate.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    evaluate.add_argument("--output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    evaluate.add_argument(
        "--include-results",
        action="store_true",
        help="Include every evaluated row in the report for targeted QA follow-up.",
    )

    collect = subparsers.add_parser("collect")
    collect.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE_OUTPUT)
    collect.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    collect.add_argument("--output", type=Path, default=DEFAULT_COLLECT_OUTPUT)
    collect.add_argument("--delay-sec", type=float, default=DEFAULT_ONEMAP_DELAY_SEC)
    collect.add_argument("--limit", type=int)
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--confirm-onemap-collection", action="store_true")

    args = parser.parse_args()
    if args.action == "plan":
        payload = build_validation_sample(
            bundle_dir=args.bundle_dir,
            postal_universe_path=args.postal_universe,
            sample_size=args.sample_size,
            seed=args.seed,
            route_mode=args.route_mode,
            onemap_delay_sec=args.onemap_delay_sec,
        )
        write_json(args.output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["ok"] else 1

    if args.action == "evaluate":
        sample_payload = read_json(args.sample)
        if not isinstance(sample_payload, dict):
            raise TypeError(f"sample must contain a JSON object: {args.sample}")
        payload = evaluate_cached_results(
            sample_payload,
            args.cache_dir,
            include_results=bool(args.include_results),
        )
        write_json(args.output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["ok"] else 1

    if args.action == "collect":
        sample_payload = read_json(args.sample)
        if not isinstance(sample_payload, dict):
            raise TypeError(f"sample must contain a JSON object: {args.sample}")
        ok, payload = collect_onemap_walk_cache(
            sample_payload,
            cache_dir=args.cache_dir,
            delay_sec=float(args.delay_sec),
            limit=args.limit,
            dry_run=bool(args.dry_run),
            confirm_onemap_collection=bool(args.confirm_onemap_collection),
        )
        write_json(args.output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
