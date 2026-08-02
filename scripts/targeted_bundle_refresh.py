# mypy: ignore-errors
# ruff: noqa: E402, RUF100

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.export import (
    GEOM_MAX_PROMOTION_RESOLUTION,
    GEOM_PROMOTION_THRESHOLD_BYTES,
    MAX_FILE_BYTES,
    geom_record,
    geom_record_shards,
    public_score_record,
    score_record_shards,
    sized_record_shards,
)
from pipeline.export import (
    write_json as write_plain_json,
)
from pipeline.onemap_validation import decode_polyline
from pipeline.scoring import NO_TRANSIT_IN_RANGE
from pipeline.scoring_integration import score_postals

DEFAULT_NETWORK = PROJECT_ROOT / "processed" / "network_island.parquet"
DEFAULT_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)


def read_json(path: Path) -> Any:
    if path.is_file():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    gz_path = path.with_name(f"{path.name}.gz")
    if gz_path.is_file():
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError(path)


def write_json(path: Path, payload: Any) -> int:
    size = write_plain_json(path, payload)
    gz_path = path.with_name(f"{path.name}.gz")
    if gz_path.exists():
        with gzip.open(gz_path, "wb") as f:
            f.write(path.read_bytes())
    return size


def active_bundle_name() -> str:
    config = read_json(PROJECT_ROOT / "web" / "data-bundle.json")
    return str(config["bundle"])


def active_bundle_dir() -> Path:
    return PROJECT_ROOT / "web" / "public" / "data" / active_bundle_name()


def area_from_shard(shard: str) -> str:
    return shard.split("_PART_")[0]


def normalize_postal(value: str) -> str:
    return str(value).strip().zfill(6)


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def postals_from_partial_report(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    report = read_json(path)
    postals: list[str] = []
    for item in report.get("comparisons", []):
        if isinstance(item, dict) and item.get("postal"):
            postals.append(normalize_postal(str(item["postal"])))
    return postals


def postals_from_file(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.is_file():
        raise FileNotFoundError(path)
    postals: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        postals.append(normalize_postal(value))
    return postals


def unique_postals(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        postal = normalize_postal(value)
        if postal not in seen:
            seen.add(postal)
            output.append(postal)
    return output


def selected_postals_from_inputs(
    *,
    partial_report: Path | None,
    postal_file: Path | None,
    postals: list[str],
) -> list[str]:
    return [
        *postals_from_partial_report(partial_report),
        *postals_from_file(postal_file),
        *[normalize_postal(postal) for postal in postals],
    ]


def copy_bundle(source_dir: Path, target_dir: Path) -> None:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"source bundle not found: {source_dir}")
    if target_dir.exists():
        raise FileExistsError(f"target bundle already exists: {target_dir}")
    shutil.copytree(source_dir, target_dir)


def load_score_index(bundle_dir: Path) -> dict[str, list[str]]:
    return {
        str(shard): [normalize_postal(str(postal)) for postal in postals]
        for shard, postals in read_json(bundle_dir / "scores" / "index.json").items()
    }


def load_score_records(bundle_dir: Path, shard: str) -> list[dict[str, Any]]:
    return read_json(bundle_dir / "scores" / f"{shard}.json")


def postal_to_shard(score_index: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for shard, postals in score_index.items():
        for postal in postals:
            lookup[postal] = shard
    return lookup


def load_area_records(
    bundle_dir: Path, score_index: dict[str, list[str]], area: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for shard in sorted(score_index):
        if area_from_shard(shard) == area:
            records.extend(load_score_records(bundle_dir, shard))
    return records


def replace_area_scores(
    bundle_dir: Path,
    score_index: dict[str, list[str]],
    area: str,
    replacements: dict[str, dict[str, Any]],
) -> None:
    old_shards = [shard for shard in score_index if area_from_shard(shard) == area]
    area_records = load_area_records(bundle_dir, score_index, area)
    patched = []
    for record in area_records:
        postal = normalize_postal(str(record["postal"]))
        patched.append(replacements.get(postal, record))

    scores_dir = bundle_dir / "scores"
    for shard in old_shards:
        (scores_dir / f"{shard}.json").unlink(missing_ok=True)
        score_index.pop(shard, None)

    for shard, records in score_record_shards(area, patched, max_bytes=MAX_FILE_BYTES):
        write_json(scores_dir / f"{shard}.json", records)
        score_index[shard] = [normalize_postal(str(record["postal"])) for record in records]


def load_geom_shard(bundle_dir: Path, shard: str) -> list[dict[str, Any]]:
    path = bundle_dir / "geom" / "h3" / f"{shard}.json"
    if not path.is_file() and not path.with_name(f"{path.name}.gz").is_file():
        return []
    return read_json(path)


def write_geom_shard(bundle_dir: Path, shard: str, records: list[dict[str, Any]]) -> None:
    write_json(
        bundle_dir / "geom" / "h3" / f"{shard}.json",
        sorted(records, key=lambda item: str(item["postal"])),
    )


def target_geom_shard(geom_index: dict[str, list[str]], lat: float, lon: float) -> tuple[str, str]:
    parent = h3.latlng_to_cell(lat, lon, 8)
    children = geom_index.setdefault(parent, [])
    if children:
        child = h3.latlng_to_cell(lat, lon, 9)
        if child not in children:
            children.append(child)
            children.sort()
        return parent, child
    return parent, parent


def delete_geom_shard(bundle_dir: Path, shard: str) -> None:
    path = bundle_dir / "geom" / "h3" / f"{shard}.json"
    path.unlink(missing_ok=True)
    path.with_name(f"{path.name}.gz").unlink(missing_ok=True)


def geom_parent_lookup(geom_index: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for parent, children in geom_index.items():
        targets = children if children else [parent]
        for target in targets:
            lookup[target] = parent
    return lookup


def geom_parent_refs(geom_index: dict[str, list[str]]) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = defaultdict(set)
    for parent, children in geom_index.items():
        targets = children if children else [parent]
        for target in targets:
            refs[target].add(parent)
    return refs


def expand_shared_geom_parents(
    geom_index: dict[str, list[str]], touched_parents: set[str]
) -> set[str]:
    refs = geom_parent_refs(geom_index)
    expanded = set(touched_parents)
    changed = True
    while changed:
        changed = False
        target_shards: set[str] = set()
        for parent in expanded:
            target_shards.update(geom_index.get(parent) or [parent])
            target_shards.add(parent)
        for shard in target_shards:
            for parent in refs.get(shard, set()):
                if parent not in expanded:
                    expanded.add(parent)
                    changed = True
    return expanded


def geometry_record_origin(record: dict[str, Any]) -> tuple[float, float]:
    for key in ("shortest", "sheltered"):
        encoded = record.get(key)
        if isinstance(encoded, str) and encoded:
            points = decode_polyline(encoded)
            if points:
                return points[0]

    route_segments = record.get("route_segments")
    if isinstance(route_segments, dict):
        for key in ("shortest", "sheltered"):
            segments = route_segments.get(key)
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                encoded = segment.get("geom")
                if isinstance(encoded, str) and encoded:
                    points = decode_polyline(encoded)
                    if points:
                        return points[0]

    postal = record.get("postal")
    raise ValueError(f"cannot derive geometry shard origin for postal {postal}")


def rebalance_geom_parents(
    bundle_dir: Path,
    geom_index: dict[str, list[str]],
    postal_index: dict[str, str],
    touched_parents: set[str],
    shard_cache: dict[str, list[dict[str, Any]]],
    origin_by_postal: dict[str, tuple[float, float]],
    *,
    max_bytes: int = GEOM_PROMOTION_THRESHOLD_BYTES,
    max_resolution: int = GEOM_MAX_PROMOTION_RESOLUTION,
) -> list[str]:
    expanded_parents = expand_shared_geom_parents(geom_index, touched_parents)
    old_targets: set[str] = set()
    for parent in expanded_parents:
        old_targets.update(geom_index.get(parent) or [parent])
        old_targets.add(parent)

    records_by_postal: dict[str, dict[str, Any]] = {}
    record_source_by_postal: dict[str, str] = {}
    for shard in sorted(old_targets):
        records = shard_cache.get(shard)
        if records is None:
            records = load_geom_shard(bundle_dir, shard)
        for item in records:
            postal = normalize_postal(str(item.get("postal")))
            preferred_shard = postal_index.get(postal)
            existing_source = record_source_by_postal.get(postal)
            if (
                postal not in records_by_postal
                or preferred_shard == shard
                or (preferred_shard is not None and existing_source != preferred_shard)
            ):
                records_by_postal[postal] = item
                record_source_by_postal[postal] = shard

    for shard in sorted(old_targets):
        delete_geom_shard(bundle_dir, shard)

    for parent in expanded_parents:
        geom_index.pop(parent, None)

    records_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    local_origins = dict(origin_by_postal)
    for item in records_by_postal.values():
        postal = normalize_postal(str(item["postal"]))
        if postal not in local_origins:
            local_origins[postal] = geometry_record_origin(item)
        lat, lon = local_origins[postal]
        parent = h3.latlng_to_cell(lat, lon, 8)
        records_by_parent[parent].append(item)

    written_shards: set[str] = set()
    for parent, records in sorted(records_by_parent.items()):
        shards = geom_record_shards(
            parent,
            sorted(records, key=lambda item: str(item["postal"])),
            local_origins,
            max_bytes,
            8,
            max_resolution,
        )
        shard_ids = [shard for shard, _records in shards]
        geom_index[parent] = [] if shard_ids == [parent] else sorted(shard_ids)
        for shard, shard_records in shards:
            write_geom_shard(bundle_dir, shard, shard_records)
            written_shards.add(shard)
            for item in shard_records:
                postal_index[normalize_postal(str(item["postal"]))] = shard

    return sorted(written_shards)


def split_oversized_geom_shards(
    bundle_dir: Path,
    geom_index: dict[str, list[str]],
    postal_index: dict[str, str],
    candidate_shards: set[str],
    *,
    max_bytes: int = MAX_FILE_BYTES,
) -> dict[str, Any]:
    split_report: list[dict[str, Any]] = []
    geom_dir = bundle_dir / "geom" / "h3"

    for shard in sorted(candidate_shards):
        path = geom_dir / f"{shard}.json"
        gz_path = path.with_name(f"{path.name}.gz")
        if not path.is_file() and not gz_path.is_file():
            continue
        file_size = path.stat().st_size if path.is_file() else gz_path.stat().st_size
        if file_size <= max_bytes:
            continue

        records = load_geom_shard(bundle_dir, shard)
        parts = sized_record_shards(shard, records, max_bytes)
        part_ids = [part_id for part_id, _records in parts]
        if part_ids == [shard]:
            continue

        delete_geom_shard(bundle_dir, shard)
        for part_id, part_records in parts:
            write_geom_shard(bundle_dir, part_id, part_records)
            for item in part_records:
                postal_index[normalize_postal(str(item["postal"]))] = part_id

        for parent, children in list(geom_index.items()):
            targets = children if children else [parent]
            if shard not in targets:
                continue
            if children:
                next_children: list[str] = []
                for child in children:
                    if child == shard:
                        next_children.extend(part_ids)
                    else:
                        next_children.append(child)
                geom_index[parent] = sorted(dict.fromkeys(next_children))
            elif parent == shard:
                geom_index[parent] = [] if part_ids == [parent] else sorted(part_ids)

        split_report.append(
            {
                "shard": shard,
                "input_bytes": file_size,
                "parts": part_ids,
            }
        )

    return {
        "geom_oversized_shards_split": split_report,
        "geom_oversized_shard_split_count": len(split_report),
    }


def patch_geometry_records(
    bundle_dir: Path,
    rescored_records: list[dict[str, Any]],
) -> dict[str, Any]:
    geom_index_path = bundle_dir / "geom" / "index.json"
    postal_index_path = bundle_dir / "geom" / "postal-index.json"
    geom_index = {
        str(cell): [str(child) for child in children]
        for cell, children in read_json(geom_index_path).items()
    }
    postal_index = {
        normalize_postal(str(postal)): str(shard)
        for postal, shard in read_json(postal_index_path).items()
    }
    touched_shards: set[str] = set()
    shard_cache: dict[str, list[dict[str, Any]]] = {}

    def cached_shard(shard: str) -> list[dict[str, Any]]:
        if shard not in shard_cache:
            shard_cache[shard] = load_geom_shard(bundle_dir, shard)
        return shard_cache[shard]

    for record in rescored_records:
        postal = normalize_postal(str(record["postal"]))
        old_shard = postal_index.get(postal)
        geometry_record = geom_record(record)

        if old_shard:
            old_records = [
                item for item in cached_shard(old_shard) if str(item.get("postal")) != postal
            ]
            shard_cache[old_shard] = old_records
            touched_shards.add(old_shard)
            postal_index.pop(postal, None)

        if geometry_record is None:
            continue

        origin = record.get("_origin")
        if not isinstance(origin, dict):
            continue
        lat = float(origin["lat"])
        lon = float(origin["lon"])
        _, shard = target_geom_shard(geom_index, lat, lon)
        shard_records = [item for item in cached_shard(shard) if str(item.get("postal")) != postal]
        shard_records.append(geometry_record)
        shard_cache[shard] = shard_records
        postal_index[postal] = shard
        touched_shards.add(shard)

    for shard in touched_shards:
        write_geom_shard(bundle_dir, shard, shard_cache.get(shard, []))

    split_report = split_oversized_geom_shards(
        bundle_dir,
        geom_index,
        postal_index,
        touched_shards,
    )

    write_json(geom_index_path, dict(sorted(geom_index.items())))
    write_json(postal_index_path, dict(sorted(postal_index.items())))
    return {
        "geometry_postals": len(postal_index),
        "geom_shards_touched": sorted(touched_shards),
        **split_report,
    }


def score_state_counts(
    bundle_dir: Path, score_index: dict[str, list[str]]
) -> tuple[int, dict[str, int]]:
    counts: Counter[str] = Counter()
    total = 0
    for shard in sorted(score_index):
        records = load_score_records(bundle_dir, shard)
        total += len(records)
        counts.update(str(record.get("state")) for record in records)
    return total, dict(sorted(counts.items()))


def compact_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "state": record.get("state"),
        "total": record.get("total"),
        "best_node": record.get("best_node"),
        "paths": record.get("paths"),
        "has_route_segments": bool(record.get("_geometry")),
    }


def refresh_bundle(
    *,
    source_dir: Path,
    target_dir: Path,
    postals: list[str],
    network_path: Path,
    postal_universe_path: Path,
) -> dict[str, Any]:
    selected_postals = unique_postals(postals)
    if not selected_postals:
        raise ValueError("no postals selected")

    copy_bundle(source_dir, target_dir)
    score_index = load_score_index(target_dir)
    existing_lookup = postal_to_shard(score_index)
    existing_postals = [postal for postal in selected_postals if postal in existing_lookup]
    missing_postals = [postal for postal in selected_postals if postal not in existing_lookup]
    if not existing_postals:
        raise ValueError("none of the selected postals exist in the current bundle")

    before_records: dict[str, dict[str, Any]] = {}
    for postal in existing_postals:
        shard = existing_lookup[postal]
        before_records[postal] = next(
            record
            for record in load_score_records(target_dir, shard)
            if str(record["postal"]) == postal
        )

    rescored = score_postals(
        postal_codes=existing_postals,
        include_geometry=True,
        network_path=network_path,
        postal_universe_path=postal_universe_path,
    )
    rescored_by_postal = {normalize_postal(str(record["postal"])): record for record in rescored}
    replacements = {
        postal: public_score_record(record)
        for postal, record in rescored_by_postal.items()
        if postal in existing_lookup
    }

    replacements_by_area: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for postal, replacement in replacements.items():
        replacements_by_area[area_from_shard(existing_lookup[postal])][postal] = replacement
    for area, area_replacements in replacements_by_area.items():
        replace_area_scores(target_dir, score_index, area, area_replacements)

    write_json(
        target_dir / "scores" / "index.json",
        {shard: sorted(postals) for shard, postals in sorted(score_index.items())},
    )
    geom_report = patch_geometry_records(target_dir, list(rescored_by_postal.values()))
    record_count, state_counts = score_state_counts(target_dir, score_index)

    manifest_path = target_dir / "manifest.json"
    manifest = read_json(manifest_path)
    refreshed_at = datetime.now(UTC).isoformat()
    manifest["generated_at"] = refreshed_at
    manifest.setdefault("provenance", {})["record_count"] = record_count
    manifest["provenance"]["state_counts"] = state_counts
    manifest["provenance"]["targeted_refresh"] = {
        "source_bundle": source_dir.name,
        "refreshed_at": refreshed_at,
        "postal_count": len(existing_postals),
        "postals": existing_postals,
        "missing_from_bundle": missing_postals,
        "network": display_path(network_path),
        "postal_universe": display_path(postal_universe_path),
    }
    manifest.setdefault("scores", {})["shards"] = sorted(score_index)
    manifest["scores"]["planning_areas"] = sorted({area_from_shard(shard) for shard in score_index})
    write_json(manifest_path, manifest)

    comparisons = []
    for postal in existing_postals:
        after_record = replacements.get(postal)
        rescored_record = rescored_by_postal.get(postal)
        before = before_records.get(postal)
        after_compact = compact_record(after_record)
        if after_compact is not None and rescored_record is not None:
            geometry = rescored_record.get("_geometry")
            after_compact["has_route_segments"] = bool(
                isinstance(geometry, dict)
                and (
                    geometry.get("shortest_path_edges")
                    or geometry.get("sheltered_path_edges")
                    or geometry.get("exposure_gap_edges")
                )
            )
        comparisons.append(
            {
                "postal": postal,
                "area": area_from_shard(existing_lookup[postal]),
                "before": compact_record(before),
                "after": after_compact,
                "state_changed": (before or {}).get("state") != (after_record or {}).get("state"),
            }
        )

    converted = [
        item["postal"]
        for item in comparisons
        if item["before"]
        and item["after"]
        and item["before"]["state"] == NO_TRANSIT_IN_RANGE
        and item["after"]["state"] != NO_TRANSIT_IN_RANGE
    ]
    return {
        "ok": True,
        "source_bundle": source_dir.name,
        "target_bundle": target_dir.name,
        "network": str(network_path),
        "postal_universe": str(postal_universe_path),
        "selected_count": len(selected_postals),
        "patched_count": len(existing_postals),
        "missing_from_bundle": missing_postals,
        "converted_from_no_transit": converted,
        "converted_count": len(converted),
        "state_counts": state_counts,
        **geom_report,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch a copied static bundle with targeted rescores."
    )
    parser.add_argument("--source-bundle-dir", type=Path, default=None)
    parser.add_argument("--target-bundle", default="")
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK)
    parser.add_argument("--postal-universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--postal", action="append", dest="postals", default=[])
    parser.add_argument("--postal-file", type=Path, default=None)
    parser.add_argument(
        "--from-partial-report",
        type=Path,
        default=None,
        help="Optional targeted-rescore report to read postals from. Omitted means no report input.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    source_dir = args.source_bundle_dir if args.source_bundle_dir else active_bundle_dir()
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    target_bundle = args.target_bundle or f"generated_{stamp}_targeted"
    target_dir = PROJECT_ROOT / "web" / "public" / "data" / target_bundle
    selected_postals = selected_postals_from_inputs(
        partial_report=args.from_partial_report,
        postal_file=args.postal_file,
        postals=args.postals,
    )
    report = refresh_bundle(
        source_dir=source_dir,
        target_dir=target_dir,
        postals=selected_postals,
        network_path=args.network,
        postal_universe_path=args.postal_universe,
    )
    output = args.output or PROJECT_ROOT / "qa" / f"targeted_bundle_refresh_{target_bundle}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "target_bundle": target_bundle,
                "output": str(output),
                "patched_count": report["patched_count"],
                "converted_count": report["converted_count"],
                "state_counts": report["state_counts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
