"""OneMap walk-routing validation gate planning and cached evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLE_DIR = PROJECT_ROOT / "web" / "public" / "data" / "generated_20260801_165500"
DEFAULT_SAMPLE_OUTPUT = PROJECT_ROOT / "qa" / "onemap_validation_sample_2000.json"
DEFAULT_REPORT_OUTPUT = PROJECT_ROOT / "qa" / "onemap_validation_cached_report.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "raw" / "validation" / "onemap_walk"
DEFAULT_SAMPLE_SIZE = 2000
DEFAULT_ONEMAP_DELAY_SEC = 2.0
MEDIAN_THRESHOLD_PCT = 10.0
P95_THRESHOLD_PCT = 25.0


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
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
            yield {
                "postal": postal,
                "area": area,
                "state": record.get("state"),
                "total": route_record.get("total", record.get("total")),
                "best_node": {
                    "type": best_node.get("type"),
                    "name": best_node.get("name"),
                    "station": best_node.get("station"),
                },
                "project_shortest_m": round(float(shortest_m), 1),
            }


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
        payload = read_json(bundle_dir / "geom" / "h3" / f"{shard}.json")
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
        if len(points) < 2:
            continue
        first = {"lat": round(points[0][0], 6), "lon": round(points[0][1], 6)}
        last = {"lat": round(points[-1][0], 6), "lon": round(points[-1][1], 6)}
        sample = {
            **candidate,
            "route_mode": route_mode,
            "route_kind": "shortest",
            "start": first,
            "end": last,
        }
        sample["cache_key"] = route_cache_key(first, last)
        samples.append(sample)
    return samples


def build_validation_sample(
    *,
    bundle_dir: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: str = "shiok-onemap-validation-v1",
    route_mode: str = "best_transit",
    onemap_delay_sec: float = DEFAULT_ONEMAP_DELAY_SEC,
) -> dict[str, Any]:
    geom_index = read_json(bundle_dir / "geom" / "postal-index.json")
    if not isinstance(geom_index, dict):
        raise TypeError("geom/postal-index.json must contain a JSON object")

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in iter_score_candidates(
        bundle_dir, route_mode=route_mode, geom_postal_index=geom_index
    ):
        buckets[str(candidate["area"])].append(candidate)

    for area, items in buckets.items():
        items.sort(key=lambda item: stable_rank(seed, area, str(item["postal"])))

    quotas = allocate_area_quotas(buckets, sample_size)
    selected = [item for area in sorted(quotas) for item in buckets[area][: quotas[area]]]
    selected.sort(key=lambda item: stable_rank(seed, str(item["area"]), str(item["postal"])))
    samples = attach_endpoints(
        bundle_dir,
        selected,
        route_mode=route_mode,
        geom_postal_index=geom_index,
    )
    projected_seconds = len(samples) * onemap_delay_sec
    return {
        "ok": len(samples) == min(sample_size, sum(len(items) for items in buckets.values())),
        "generated_at": datetime.now(UTC).isoformat(),
        "bundle": bundle_dir.name,
        "route_mode": route_mode,
        "sample_size_requested": sample_size,
        "sample_size": len(samples),
        "eligible_records": sum(len(items) for items in buckets.values()),
        "area_count": len(buckets),
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


def evaluate_cached_results(sample_payload: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    missing: list[str] = []
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
        if not isinstance(onemap_m, int | float) or not isinstance(project_m, int | float):
            continue
        delta_pct = abs(float(project_m) - float(onemap_m)) / float(onemap_m) * 100
        results.append(
            {
                "postal": sample.get("postal"),
                "area": sample.get("area"),
                "cache_key": cache_key,
                "project_shortest_m": round(float(project_m), 1),
                "onemap_walk_m": round(float(onemap_m), 1),
                "abs_pct_delta": round(delta_pct, 3),
            }
        )

    deltas = [float(item["abs_pct_delta"]) for item in results]
    median = percentile(deltas, 50)
    p95 = percentile(deltas, 95)
    gate_passed = (
        len(results) == int(sample_payload.get("sample_size", 0))
        and median is not None
        and p95 is not None
        and median <= MEDIAN_THRESHOLD_PCT
        and p95 <= P95_THRESHOLD_PCT
    )
    return {
        "ok": gate_passed,
        "generated_at": datetime.now(UTC).isoformat(),
        "bundle": sample_payload.get("bundle"),
        "sample_size": sample_payload.get("sample_size"),
        "cache_dir": str(cache_dir),
        "cached_results": len(results),
        "missing_cache_results": len(missing),
        "missing_cache_postals_preview": missing[:20],
        "median_abs_pct_delta": round(median, 3) if median is not None else None,
        "p95_abs_pct_delta": round(p95, 3) if p95 is not None else None,
        "thresholds": {
            "median_abs_pct_delta_max": MEDIAN_THRESHOLD_PCT,
            "p95_abs_pct_delta_max": P95_THRESHOLD_PCT,
        },
        "gate_passed": gate_passed,
        "results_preview": results[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan/evaluate OneMap walk validation.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR)
    plan.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    plan.add_argument("--seed", default="shiok-onemap-validation-v1")
    plan.add_argument("--route-mode", default="best_transit")
    plan.add_argument("--onemap-delay-sec", type=float, default=DEFAULT_ONEMAP_DELAY_SEC)
    plan.add_argument("--output", type=Path, default=DEFAULT_SAMPLE_OUTPUT)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE_OUTPUT)
    evaluate.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    evaluate.add_argument("--output", type=Path, default=DEFAULT_REPORT_OUTPUT)

    args = parser.parse_args()
    if args.action == "plan":
        payload = build_validation_sample(
            bundle_dir=args.bundle_dir,
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
        payload = evaluate_cached_results(sample_payload, args.cache_dir)
        write_json(args.output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["ok"] else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
