"""Static JSON artifact export and validation for the web frontend."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import h3
from pyproj import Transformer
from shapely.geometry import MultiLineString, Point
from shapely.ops import linemerge
from shapely import wkt as shapely_wkt

from pipeline.scoring import NO_TRANSIT_IN_RANGE, NOT_YET_SCORED
from pipeline.scoring_integration import NETWORK_PATH, raw_file_from_manifest, score_postals

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "web" / "public" / "data" / "generated"
DEFAULT_VALIDATE_DIR = PROJECT_ROOT / "web" / "public" / "data"
MAX_DATA_FILES = 5000
MAX_FILE_BYTES = 5 * 1024 * 1024
GEOM_PROMOTION_THRESHOLD_BYTES = 250 * 1024
VALID_STATES = {"SCORED", "SCORED_PARTIAL", NOT_YET_SCORED, NO_TRANSIT_IN_RANGE}


def slugify_area(value: str | None) -> str:
    text = (value or "UNKNOWN").strip().upper()
    slug = re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
    return slug or "UNKNOWN"


def write_json(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(content)
    return len(content)


def rel_key(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def json_size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))


@lru_cache(maxsize=1)
def svy21_to_wgs84_transformer() -> Transformer:
    return Transformer.from_crs("EPSG:3414", "EPSG:4326", always_xy=True)


def geometry_to_lat_lon_pairs(geometry: Any) -> list[tuple[float, float]]:
    if isinstance(geometry, str):
        geometry = shapely_wkt.loads(geometry)
    if geometry is None or getattr(geometry, "is_empty", True):
        return []

    if geometry.geom_type == "LineString":
        lines = [geometry]
    elif geometry.geom_type == "MultiLineString":
        lines = list(geometry.geoms)
    else:
        return []

    transformer = svy21_to_wgs84_transformer()
    pairs: list[tuple[float, float]] = []
    for line in lines:
        for x, y in line.coords:
            lon, lat = transformer.transform(x, y)
            pairs.append((float(lat), float(lon)))
    return pairs


def encode_signed_polyline_value(value: int) -> str:
    shifted = value << 1
    if value < 0:
        shifted = ~shifted
    chunks = []
    while shifted >= 0x20:
        chunks.append(chr((0x20 | (shifted & 0x1F)) + 63))
        shifted >>= 5
    chunks.append(chr(shifted + 63))
    return "".join(chunks)


def encode_polyline(points: Iterable[tuple[float, float]], precision: int = 5) -> str:
    factor = 10**precision
    prev_lat = 0
    prev_lon = 0
    encoded = []
    for lat, lon in points:
        lat_i = int(math.floor(lat * factor + 0.5))
        lon_i = int(math.floor(lon * factor + 0.5))
        encoded.append(encode_signed_polyline_value(lat_i - prev_lat))
        encoded.append(encode_signed_polyline_value(lon_i - prev_lon))
        prev_lat = lat_i
        prev_lon = lon_i
    return "".join(encoded)


def encode_geometry(geometry: Any) -> str:
    return encode_polyline(geometry_to_lat_lon_pairs(geometry))


def merged_geometry(edges: list[dict[str, Any]]) -> Any:
    geometries = []
    for edge in edges:
        geometry = edge.get("geometry")
        if isinstance(geometry, str):
            geometry = shapely_wkt.loads(geometry)
        if geometry is not None and not geometry.is_empty:
            geometries.append(geometry)
    if not geometries:
        return None
    return linemerge(MultiLineString(geometries)) if len(geometries) > 1 else geometries[0]


def exposure_gap_geometries(record: dict[str, Any]) -> list[dict[str, Any]]:
    geometry_payload = record.get("_geometry", {})
    path_edges = geometry_payload.get("exposure_gap_edges", [])
    public_gaps = record.get("exposure_gaps") or []
    gaps: list[dict[str, Any]] = []
    current_edges: list[dict[str, Any]] = []
    gap_index = 0

    def flush() -> None:
        nonlocal gap_index
        if not current_edges:
            return
        length_m = sum(float(edge["length_m"]) for edge in current_edges)
        public_gap = public_gaps[gap_index] if gap_index < len(public_gaps) else {}
        geometry = merged_geometry(current_edges)
        gaps.append(
            {
                "geom": encode_geometry(geometry),
                "len_m": round(float(public_gap.get("len_m", length_m)), 1),
                "label": str(public_gap.get("label", "exposed gap")),
            }
        )
        current_edges.clear()
        gap_index += 1

    for edge in path_edges:
        if not edge.get("is_covered") and float(edge.get("length_m", 0.0)) > 0:
            current_edges.append(edge)
        else:
            flush()
    flush()
    return gaps


def geom_record(record: dict[str, Any]) -> dict[str, Any] | None:
    geometry_payload = record.get("_geometry")
    if not isinstance(geometry_payload, dict):
        return None
    shortest = encode_geometry(geometry_payload.get("shortest"))
    sheltered = encode_geometry(geometry_payload.get("sheltered"))
    if not shortest or not sheltered:
        return None
    return {
        "postal": record["postal"],
        "shortest": shortest,
        "sheltered": sheltered,
        "exposure_gaps": exposure_gap_geometries(record),
    }


def public_score_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def load_planning_area_lookup(records: list[dict[str, Any]]) -> dict[str, str]:
    explicit = {
        str(record["postal"]): slugify_area(str(record["_area"]))
        for record in records
        if "_area" in record
    }
    unresolved = [
        record
        for record in records
        if str(record["postal"]) not in explicit and isinstance(record.get("_origin"), dict)
    ]
    if not unresolved:
        return explicit

    boundary_path = raw_file_from_manifest(
        "planning_area_boundary", "planning_area_boundary.geojson"
    )
    if boundary_path is None:
        return {**explicit, **{str(record["postal"]): "UNKNOWN" for record in unresolved}}

    points = gpd.GeoDataFrame(
        [
            {
                "postal": str(record["postal"]),
                "geometry": Point(record["_origin"]["lon"], record["_origin"]["lat"]),
            }
            for record in unresolved
        ],
        crs="EPSG:4326",
    ).to_crs("EPSG:3414")
    boundaries = gpd.read_file(boundary_path).to_crs("EPSG:3414")[["PLN_AREA_N", "geometry"]]
    joined = gpd.sjoin(points, boundaries, how="left", predicate="within")

    lookup = dict(explicit)
    for _, row in joined.iterrows():
        lookup[str(row["postal"])] = slugify_area(row.get("PLN_AREA_N"))
    return lookup


def state_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(record.get("state")) for record in records)
    return dict(sorted(counts.items()))


def export_static_artifacts(
    records: list[dict[str, Any]],
    output_dir: Path = DEFAULT_EXPORT_DIR,
    geom_promotion_threshold_bytes: int = GEOM_PROMOTION_THRESHOLD_BYTES,
) -> dict[str, Any]:
    records = sorted(records, key=lambda item: str(item["postal"]))
    area_lookup = load_planning_area_lookup(records)
    scores_by_area: dict[str, list[dict[str, Any]]] = defaultdict(list)
    score_index: dict[str, list[str]] = defaultdict(list)

    for record in records:
        postal = str(record["postal"])
        area = area_lookup.get(postal, "UNKNOWN")
        scores_by_area[area].append(public_score_record(record))
        score_index[area].append(postal)

    output_dir.mkdir(parents=True, exist_ok=True)
    scores_dir = output_dir / "scores"
    geom_dir = output_dir / "geom" / "h3"

    written_files: dict[str, int] = {}
    for area, area_records in sorted(scores_by_area.items()):
        written_files[rel_key(scores_dir / f"{area}.json", output_dir)] = write_json(
            scores_dir / f"{area}.json", area_records
        )
    written_files[rel_key(scores_dir / "index.json", output_dir)] = write_json(
        scores_dir / "index.json",
        {key: sorted(value) for key, value in sorted(score_index.items())},
    )

    geom_index: dict[str, list[str]] = {}
    geom_by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    geom_origin_by_postal: dict[str, tuple[float, float]] = {}
    for record in records:
        origin = record.get("_origin")
        geometry_record = geom_record(record)
        if not isinstance(origin, dict) or geometry_record is None:
            continue
        lat = float(origin["lat"])
        lon = float(origin["lon"])
        cell = h3.latlng_to_cell(lat, lon, 8)
        geom_by_cell[cell].append(geometry_record)
        geom_origin_by_postal[str(record["postal"])] = (lat, lon)

    for cell, cell_records in sorted(geom_by_cell.items()):
        if json_size(cell_records) <= geom_promotion_threshold_bytes:
            geom_index[cell] = []
            written_files[rel_key(geom_dir / f"{cell}.json", output_dir)] = write_json(
                geom_dir / f"{cell}.json", sorted(cell_records, key=lambda item: item["postal"])
            )
            continue

        children: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in cell_records:
            lat, lon = geom_origin_by_postal[item["postal"]]
            child = h3.latlng_to_cell(lat, lon, 9)
            children[child].append(item)
        geom_index[cell] = sorted(children)
        for child, child_records in sorted(children.items()):
            written_files[rel_key(geom_dir / f"{child}.json", output_dir)] = write_json(
                geom_dir / f"{child}.json", sorted(child_records, key=lambda item: item["postal"])
            )

    written_files[rel_key(output_dir / "geom" / "index.json", output_dir)] = write_json(
        output_dir / "geom" / "index.json", geom_index
    )

    data_as_of_values = sorted(
        {
            str(record.get("data_as_of"))
            for record in records
            if record.get("data_as_of") is not None
        }
    )
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_as_of": data_as_of_values[-1] if data_as_of_values else None,
        "provenance": {
            "artifact": "shiok-static-json",
            "record_count": len(records),
            "state_counts": state_counts(records),
        },
        "scores": {
            "areas": sorted(scores_by_area),
            "index": "scores/index.json",
        },
        "geom": {
            "index": "geom/index.json",
            "h3_resolution": 8,
            "promoted_resolution": 9,
            "promotion_threshold_bytes": geom_promotion_threshold_bytes,
        },
    }
    written_files[rel_key(output_dir / "manifest.json", output_dir)] = write_json(
        output_dir / "manifest.json", manifest
    )

    return {
        "output_dir": str(output_dir),
        "record_count": len(records),
        "state_counts": state_counts(records),
        "score_area_count": len(scores_by_area),
        "geom_shard_count": len([path for path in written_files if path.startswith("geom/h3")]),
        "file_count": len(written_files),
        "written_files": dict(sorted(written_files.items())),
    }


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_score_batch_records(records_dir: Path) -> list[dict[str, Any]]:
    chunks_dir = records_dir / "chunks"
    if not chunks_dir.is_dir():
        raise FileNotFoundError(f"score batch chunks directory not found: {chunks_dir}")

    chunk_paths = sorted(chunks_dir.glob("chunk_*.json"))
    if not chunk_paths:
        raise FileNotFoundError(f"no score batch chunk JSON files found in {chunks_dir}")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in chunk_paths:
        payload = read_json(path)
        if not isinstance(payload, list):
            raise ValueError(f"score batch chunk must contain a list: {path}")
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError(f"score batch chunk record must be an object: {path}")
            postal = str(item.get("postal", ""))
            if not postal:
                raise ValueError(f"score batch chunk record missing postal: {path}")
            if postal in seen:
                raise ValueError(f"duplicate postal across score batch chunks: {postal}")
            seen.add(postal)
            records.append(item)
    return sorted(records, key=lambda item: str(item["postal"]))


def validate_score_record(record: dict[str, Any], errors: list[str], context: str) -> None:
    state = record.get("state")
    if state not in VALID_STATES:
        errors.append(f"{context}: invalid state {state!r}")
        return

    if state in {"SCORED", "SCORED_PARTIAL"}:
        for key in ["total", "subscores", "best_node", "paths", "exposure_gaps"]:
            if record.get(key) is None:
                errors.append(f"{context}: {key} missing for {state}")
    else:
        if record.get("total") is not None:
            errors.append(f"{context}: total must be null for {state}")
        if record.get("subscores") is not None:
            errors.append(f"{context}: subscores must be null for {state}")


def validate_static_artifacts(
    input_dir: Path = DEFAULT_VALIDATE_DIR,
) -> tuple[bool, dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    files = [path for path in input_dir.rglob("*.json") if path.is_file()]

    if len(files) > MAX_DATA_FILES:
        errors.append(f"file count {len(files)} exceeds {MAX_DATA_FILES}")
    for path in files:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            errors.append(f"{path.relative_to(input_dir)} exceeds {MAX_FILE_BYTES} bytes")

    manifest_path = input_dir / "manifest.json"
    score_index_path = input_dir / "scores" / "index.json"
    geom_index_path = input_dir / "geom" / "index.json"
    for required in [manifest_path, score_index_path, geom_index_path]:
        if not required.is_file():
            errors.append(f"missing required file: {required.relative_to(input_dir)}")

    indexed_postals: set[str] = set()
    scored_postals_with_geom_required: set[str] = set()
    if score_index_path.is_file():
        score_index = read_json(score_index_path)
        if not isinstance(score_index, dict):
            errors.append("scores/index.json must be an object")
            score_index = {}
        for area, postals in sorted(score_index.items()):
            if not isinstance(postals, list):
                errors.append(f"scores/index.json {area}: value must be a list")
                continue
            area_path = input_dir / "scores" / f"{area}.json"
            if not area_path.is_file():
                errors.append(f"scores/index.json references missing file: scores/{area}.json")
                continue
            records = read_json(area_path)
            if not isinstance(records, list):
                errors.append(f"scores/{area}.json must be a list")
                continue
            file_postals = [str(record.get("postal")) for record in records]
            if sorted(file_postals) != sorted(str(postal) for postal in postals):
                errors.append(f"scores/{area}.json postals do not match scores/index.json")
            for record in records:
                if not isinstance(record, dict):
                    errors.append(f"scores/{area}.json: record must be an object")
                    continue
                postal = str(record.get("postal"))
                indexed_postals.add(postal)
                validate_score_record(record, errors, f"scores/{area}.json:{postal}")
                if record.get("state") in {"SCORED", "SCORED_PARTIAL"}:
                    scored_postals_with_geom_required.add(postal)

    geom_postals: set[str] = set()
    if geom_index_path.is_file():
        geom_index = read_json(geom_index_path)
        if not isinstance(geom_index, dict):
            errors.append("geom/index.json must be an object")
            geom_index = {}
        for cell, children in sorted(geom_index.items()):
            target_cells = children if children else [cell]
            if not isinstance(target_cells, list):
                errors.append(f"geom/index.json {cell}: value must be a list")
                continue
            for target_cell in target_cells:
                geom_path = input_dir / "geom" / "h3" / f"{target_cell}.json"
                if not geom_path.is_file():
                    errors.append(
                        f"geom/index.json references missing file: geom/h3/{target_cell}.json"
                    )
                    continue
                geom_records = read_json(geom_path)
                if not isinstance(geom_records, list):
                    errors.append(f"geom/h3/{target_cell}.json must be a list")
                    continue
                for item in geom_records:
                    if not isinstance(item, dict):
                        errors.append(f"geom/h3/{target_cell}.json: record must be an object")
                        continue
                    postal = str(item.get("postal"))
                    geom_postals.add(postal)
                    for key in ["shortest", "sheltered", "exposure_gaps"]:
                        if key not in item:
                            errors.append(f"geom/h3/{target_cell}.json:{postal}: missing {key}")

    missing_geom = scored_postals_with_geom_required - geom_postals
    if missing_geom:
        errors.append(f"{len(missing_geom)} scored postals missing geometry shards")
    extra_geom = geom_postals - indexed_postals
    if extra_geom:
        warnings.append(f"{len(extra_geom)} geometry postals are not in scores/index.json")

    report = {
        "input_dir": str(input_dir),
        "ok": not errors,
        "file_count": len(files),
        "indexed_postals": len(indexed_postals),
        "geometry_postals": len(geom_postals),
        "errors": errors,
        "warnings": warnings,
    }
    return not errors, report


def validate_export_batch_args(
    *,
    full_batch: bool,
    confirm_full_batch: bool,
    postal_universe_path: Path | None,
) -> list[str]:
    errors: list[str] = []
    if not full_batch:
        return errors
    if not confirm_full_batch:
        errors.append("full export batch requires --confirm-full-batch after checkpoint approval")
    if postal_universe_path is None:
        errors.append("--full-batch requires --postal-universe")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or validate static web data artifacts.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--postal", action="append", dest="postals")
    export_parser.add_argument("--limit", type=int, default=5)
    export_parser.add_argument("--output", type=Path, default=DEFAULT_EXPORT_DIR)
    export_parser.add_argument(
        "--records-dir",
        type=Path,
        help="Read pre-scored score-batch chunks instead of scoring live.",
    )
    export_parser.add_argument("--postal-universe", type=Path)
    export_parser.add_argument("--network", type=Path, default=NETWORK_PATH)
    export_parser.add_argument(
        "--full-batch",
        action="store_true",
        help="Export all eligible rows from --postal-universe; requires --confirm-full-batch.",
    )
    export_parser.add_argument(
        "--confirm-full-batch",
        action="store_true",
        help="Required with --full-batch after human checkpoint approval.",
    )

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--input", type=Path, default=DEFAULT_VALIDATE_DIR)

    args = parser.parse_args()
    if args.action == "export":
        guard_errors = validate_export_batch_args(
            full_batch=bool(args.full_batch),
            confirm_full_batch=bool(args.confirm_full_batch),
            postal_universe_path=args.postal_universe,
        )
        if guard_errors:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "errors": guard_errors,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1

        if args.records_dir is not None:
            try:
                records = load_score_batch_records(args.records_dir)
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "errors": [str(exc)],
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 1
        else:
            records = score_postals(
                postal_codes=args.postals,
                limit=None if args.full_batch else int(args.limit),
                include_geometry=True,
                network_path=args.network,
                postal_universe_path=args.postal_universe,
            )
        report = export_static_artifacts(records, output_dir=args.output)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if args.action == "validate":
        ok, report = validate_static_artifacts(input_dir=args.input)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
