"""Static JSON artifact export and validation for the web frontend."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import geopandas as gpd
import h3
from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiLineString, Point
from shapely.ops import linemerge

from pipeline.bus import parse_peak_frequency_minutes
from pipeline.scoring import NO_TRANSIT_IN_RANGE, NOT_YET_SCORED
from pipeline.scoring_integration import NETWORK_PATH, raw_file_from_manifest, score_postals

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "web" / "public" / "data" / "generated"
DEFAULT_VALIDATE_DIR = PROJECT_ROOT / "web" / "public" / "data"
MAX_DATA_FILES = 5000
MAX_FILE_BYTES = 5 * 1024 * 1024
GEOM_PROMOTION_THRESHOLD_BYTES = int(MAX_FILE_BYTES * 0.9)
GEOM_MAX_PROMOTION_RESOLUTION = 12
VALID_STATES = {"SCORED", "SCORED_PARTIAL", NOT_YET_SCORED, NO_TRANSIT_IN_RANGE}
TRANSIT_SOURCE_KEYS = ("mrt_lrt_exits", "bus_stops", "bus_services", "bus_routes")


def slugify_area(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        text = "UNKNOWN"
    else:
        text = str(value).strip().upper()
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


def geometry_line_parts(geometry: Any) -> list[Any]:
    if isinstance(geometry, str):
        geometry = shapely_wkt.loads(geometry)
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type == "MultiLineString":
        return list(geometry.geoms)
    return []


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
        lat_i = math.floor(lat * factor + 0.5)
        lon_i = math.floor(lon * factor + 0.5)
        encoded.append(encode_signed_polyline_value(lat_i - prev_lat))
        encoded.append(encode_signed_polyline_value(lon_i - prev_lon))
        prev_lat = lat_i
        prev_lon = lon_i
    return "".join(encoded)


def encode_geometry(geometry: Any) -> str:
    return encode_polyline(geometry_to_lat_lon_pairs(geometry))


def encode_geometry_parts(geometry: Any) -> list[str]:
    return [
        encoded
        for encoded in (encode_geometry(part) for part in geometry_line_parts(geometry))
        if encoded
    ]


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
    return exposure_gap_geometries_from_payload(
        geometry_payload,
        record.get("exposure_gaps") or [],
    )


def exposure_gap_geometries_from_payload(
    geometry_payload: dict[str, Any],
    public_gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    path_edges = geometry_payload.get("exposure_gap_edges", [])
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
        parts = geometry_line_parts(geometry)
        if not parts:
            current_edges.clear()
            gap_index += 1
            return
        if len(parts) == 1:
            gaps.append(
                {
                    "geom": encode_geometry(parts[0]),
                    "len_m": round(float(public_gap.get("len_m", length_m)), 1),
                    "label": str(public_gap.get("label", "exposed gap")),
                }
            )
        else:
            for part_index, part in enumerate(parts):
                encoded = encode_geometry(part)
                if not encoded:
                    continue
                gaps.append(
                    {
                        "geom": encoded,
                        "len_m": round(float(part.length), 1),
                        "label": str(public_gap.get("label", "exposed gap")),
                        "part_index": part_index,
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


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def route_edge_source_class(edge: dict[str, Any]) -> str:
    if not edge.get("is_covered"):
        return "exposed"

    source_layer = text_value(edge.get("source_layer")).lower()
    synth_class = text_value(edge.get("synth_class")).lower()
    highway = text_value(edge.get("highway")).lower()
    covered = text_value(edge.get("covered")).lower()
    bridge = text_value(edge.get("bridge")).lower()
    tunnel = text_value(edge.get("tunnel")).lower()
    indoor = text_value(edge.get("indoor")).lower()
    location = text_value(edge.get("location")).lower()

    if "audited_shelter_correction" in {source_layer, highway}:
        return "audited_shelter_correction"
    if (
        "overhead_bridge_underpass" in source_layer
        or bridge in {"yes", "covered"}
        or tunnel in {"yes", "building_passage"}
        or location == "underground"
    ):
        return "bridge_underpass"
    if "underpass" in highway or "bridge" in highway:
        return "bridge_underpass"
    if "inferred_hdb" in source_layer or "hdb" in synth_class:
        return "inferred_hdb_void_deck"
    if "covered_linkway" in source_layer:
        return "lta_covered_linkway"
    if indoor in {"yes", "building_passage"} or covered in {
        "yes",
        "covered",
        "arcade",
        "colonnade",
        "building_passage",
    }:
        return "osm_covered"
    if location == "indoor":
        return "osm_covered"
    return "covered_unknown"


def route_edge_source_key(edge: dict[str, Any]) -> tuple[Any, ...]:
    return (
        bool(edge.get("is_covered")),
        route_edge_source_class(edge),
        text_value(edge.get("source_layer")),
        text_value(edge.get("synth_class")),
        text_value(edge.get("confidence")),
    )


def route_segment_properties(
    edges: list[dict[str, Any]],
    is_covered: bool,
    source_class: str,
    source_layer: str,
    synth_class: str,
    confidence: str,
    part_index: int,
    part_len_m: float,
) -> dict[str, Any]:
    source_counts = Counter(route_edge_source_class(edge) for edge in edges)
    properties: dict[str, Any] = {
        "geom": "",
        "len_m": round(part_len_m, 1),
        "is_covered": is_covered,
        "source_class": source_class,
        "part_index": part_index,
    }
    if source_layer:
        properties["source_layer"] = source_layer
    if synth_class:
        properties["synth_class"] = synth_class
    if confidence:
        properties["confidence"] = confidence
    if source_counts:
        properties["source_summary"] = ",".join(
            f"{key}:{source_counts[key]}" for key in sorted(source_counts)
        )
    return properties


def route_segment_geometries(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_edges: list[dict[str, Any]] = []
    current_key: tuple[Any, ...] | None = None

    def flush() -> None:
        nonlocal current_key
        if not current_edges or current_key is None:
            current_edges.clear()
            current_key = None
            return
        current_covered, source_class, source_layer, synth_class, confidence = current_key
        geometry = merged_geometry(current_edges)
        parts = geometry_line_parts(geometry)
        for part_index, part in enumerate(parts):
            encoded = encode_geometry(part)
            if not encoded:
                continue
            part_len_m = (
                sum(float(edge.get("length_m", 0.0)) for edge in current_edges)
                if len(parts) == 1
                else float(part.length)
            )
            segment = route_segment_properties(
                current_edges,
                bool(current_covered),
                str(source_class),
                str(source_layer),
                str(synth_class),
                str(confidence),
                part_index,
                part_len_m,
            )
            segment["geom"] = encoded
            segments.append(segment)
        current_edges.clear()
        current_key = None

    for edge in edges:
        length_m = float(edge.get("length_m", 0.0))
        geometry = edge.get("geometry")
        if length_m <= 0 or geometry is None:
            continue
        source_key = route_edge_source_key(edge)
        if current_key is None:
            current_key = source_key
        if source_key != current_key:
            flush()
            current_key = source_key
        current_edges.append(edge)
    flush()
    return segments


def geom_payload_record(
    postal: str,
    geometry_payload: dict[str, Any],
    exposure_gaps: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(geometry_payload, dict):
        return None
    shortest = encode_geometry(geometry_payload.get("shortest"))
    sheltered = encode_geometry(geometry_payload.get("sheltered"))
    if not shortest or not sheltered:
        return None
    shortest_parts = encode_geometry_parts(geometry_payload.get("shortest"))
    sheltered_parts = encode_geometry_parts(geometry_payload.get("sheltered"))
    output: dict[str, Any] = {
        "postal": postal,
        "shortest": shortest,
        "sheltered": sheltered,
        "exposure_gaps": exposure_gap_geometries_from_payload(geometry_payload, exposure_gaps),
    }
    if len(shortest_parts) > 1:
        output["shortest_parts"] = shortest_parts
    if len(sheltered_parts) > 1:
        output["sheltered_parts"] = sheltered_parts
    shortest_segments = route_segment_geometries(geometry_payload.get("shortest_path_edges", []))
    sheltered_segments = route_segment_geometries(
        geometry_payload.get("sheltered_path_edges", geometry_payload.get("exposure_gap_edges", []))
    )
    if shortest_segments or sheltered_segments:
        output["route_segments"] = {
            "shortest": shortest_segments,
            "sheltered": sheltered_segments,
        }
    return output


def geom_record(record: dict[str, Any]) -> dict[str, Any] | None:
    geometry_payload = record.get("_geometry")
    if not isinstance(geometry_payload, dict):
        return None
    output = geom_payload_record(
        str(record["postal"]),
        geometry_payload,
        record.get("exposure_gaps") or [],
    )
    if output is None:
        return None

    geometry_options = record.get("_geometry_options")
    route_options = record.get("route_options")
    if isinstance(geometry_options, dict) and isinstance(route_options, dict):
        option_output: dict[str, Any] = {}
        for key, geometry_payload in sorted(geometry_options.items()):
            public_option = route_options.get(key, {})
            exposure_gaps = (
                public_option.get("exposure_gaps") if isinstance(public_option, dict) else []
            )
            option_geom = geom_payload_record(
                str(record["postal"]),
                geometry_payload,
                exposure_gaps if isinstance(exposure_gaps, list) else [],
            )
            if option_geom is None:
                continue
            option_geom.pop("postal", None)
            option_output[str(key)] = option_geom
        if option_output:
            output["route_options"] = option_output
    return output


def public_score_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def load_planning_area_lookup(records: list[dict[str, Any]]) -> dict[str, str]:
    explicit = {
        str(record["postal"]): slugify_area(record["_area"])
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


def score_prefix_index(
    score_index: dict[str, list[str]], prefix_len: int = 3
) -> dict[str, list[str]]:
    prefix_map: dict[str, set[str]] = defaultdict(set)
    for shard, postals in score_index.items():
        for postal in postals:
            text = str(postal)
            if len(text) >= prefix_len:
                prefix_map[text[:prefix_len]].add(shard)
    return {prefix: sorted(shards) for prefix, shards in sorted(prefix_map.items())}


def score_record_shards(
    area: str,
    records: list[dict[str, Any]],
    max_bytes: int = MAX_FILE_BYTES,
) -> list[tuple[str, list[dict[str, Any]]]]:
    records = sorted(records, key=lambda item: str(item["postal"]))
    if json_size(records) <= max_bytes:
        return [(area, records)]

    shards: list[tuple[str, list[dict[str, Any]]]] = []
    start = 0
    shard_index = 1
    while start < len(records):
        low = 1
        high = len(records) - start
        best = 0
        while low <= high:
            mid = (low + high) // 2
            candidate = records[start : start + mid]
            if json_size(candidate) <= max_bytes:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best == 0:
            postal = records[start].get("postal")
            raise ValueError(f"single score record exceeds {max_bytes} bytes: {postal}")
        shard_records = records[start : start + best]
        shards.append((f"{area}_PART_{shard_index:03d}", shard_records))
        start += best
        shard_index += 1
    return shards


def sized_record_shards(
    shard_id: str,
    records: list[dict[str, Any]],
    max_bytes: int,
) -> list[tuple[str, list[dict[str, Any]]]]:
    records = sorted(records, key=lambda item: str(item["postal"]))
    if json_size(records) <= max_bytes:
        return [(shard_id, records)]

    shards: list[tuple[str, list[dict[str, Any]]]] = []
    start = 0
    shard_index = 1
    while start < len(records):
        low = 1
        high = len(records) - start
        best = 0
        while low <= high:
            mid = (low + high) // 2
            candidate = records[start : start + mid]
            if json_size(candidate) <= max_bytes:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best == 0:
            postal = records[start].get("postal")
            raise ValueError(
                f"single record exceeds {max_bytes} bytes in shard {shard_id}: {postal}"
            )
        shard_records = records[start : start + best]
        shards.append((f"{shard_id}_PART_{shard_index:03d}", shard_records))
        start += best
        shard_index += 1
    return shards


def geom_record_shards(
    shard_id: str,
    records: list[dict[str, Any]],
    geom_origin_by_postal: dict[str, tuple[float, float]],
    max_bytes: int,
    resolution: int,
    max_resolution: int,
) -> list[tuple[str, list[dict[str, Any]]]]:
    records = sorted(records, key=lambda item: str(item["postal"]))
    if json_size(records) <= max_bytes:
        return [(shard_id, records)]

    if resolution >= max_resolution:
        return sized_record_shards(shard_id, records, max_bytes)

    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        lat, lon = geom_origin_by_postal[str(item["postal"])]
        child = h3.latlng_to_cell(lat, lon, resolution + 1)
        children[child].append(item)

    shards: list[tuple[str, list[dict[str, Any]]]] = []
    for child, child_records in sorted(children.items()):
        shards.extend(
            geom_record_shards(
                child,
                child_records,
                geom_origin_by_postal,
                max_bytes,
                resolution + 1,
                max_resolution,
            )
        )
    return shards


def export_static_artifacts(
    records: list[dict[str, Any]],
    output_dir: Path = DEFAULT_EXPORT_DIR,
    geom_promotion_threshold_bytes: int = GEOM_PROMOTION_THRESHOLD_BYTES,
    geom_max_promotion_resolution: int = GEOM_MAX_PROMOTION_RESOLUTION,
    score_shard_max_bytes: int = MAX_FILE_BYTES,
) -> dict[str, Any]:
    records = sorted(records, key=lambda item: str(item["postal"]))
    area_lookup = load_planning_area_lookup(records)
    scores_by_area: dict[str, list[dict[str, Any]]] = defaultdict(list)
    score_index: dict[str, list[str]] = defaultdict(list)

    for record in records:
        postal = str(record["postal"])
        area = area_lookup.get(postal, "UNKNOWN")
        scores_by_area[area].append(public_score_record(record))

    output_dir.mkdir(parents=True, exist_ok=True)
    scores_dir = output_dir / "scores"
    geom_dir = output_dir / "geom" / "h3"

    written_files: dict[str, int] = {}
    for area, area_records in sorted(scores_by_area.items()):
        for shard, shard_records in score_record_shards(
            area, area_records, max_bytes=score_shard_max_bytes
        ):
            written_files[rel_key(scores_dir / f"{shard}.json", output_dir)] = write_json(
                scores_dir / f"{shard}.json", shard_records
            )
            score_index[shard].extend(str(record["postal"]) for record in shard_records)
    written_files[rel_key(scores_dir / "index.json", output_dir)] = write_json(
        scores_dir / "index.json",
        {key: sorted(value) for key, value in sorted(score_index.items())},
    )
    written_files[rel_key(scores_dir / "prefix-index.json", output_dir)] = write_json(
        scores_dir / "prefix-index.json",
        score_prefix_index(score_index),
    )

    geom_index: dict[str, list[str]] = {}
    geom_postal_index: dict[str, str] = {}
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

    geom_shard_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell, cell_records in sorted(geom_by_cell.items()):
        shards = geom_record_shards(
            cell,
            cell_records,
            geom_origin_by_postal,
            geom_promotion_threshold_bytes,
            8,
            geom_max_promotion_resolution,
        )
        shard_ids = [shard for shard, _records in shards]
        geom_index[cell] = [] if shard_ids == [cell] else sorted(shard_ids)
        for shard, shard_records in shards:
            geom_shard_records[shard].extend(shard_records)
            for item in shard_records:
                geom_postal_index[str(item["postal"])] = shard

    for shard, shard_records in sorted(geom_shard_records.items()):
        written_files[rel_key(geom_dir / f"{shard}.json", output_dir)] = write_json(
            geom_dir / f"{shard}.json", sorted(shard_records, key=lambda item: item["postal"])
        )

    written_files[rel_key(output_dir / "geom" / "index.json", output_dir)] = write_json(
        output_dir / "geom" / "index.json", geom_index
    )
    written_files[rel_key(output_dir / "geom" / "postal-index.json", output_dir)] = write_json(
        output_dir / "geom" / "postal-index.json", dict(sorted(geom_postal_index.items()))
    )
    transit_report = export_transit_pois(output_dir)
    written_files[transit_report["path"]] = int(transit_report["bytes"])

    data_as_of_values = sorted(
        {
            str(record.get("data_as_of"))
            for record in records
            if record.get("data_as_of") is not None
        }
    )
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_as_of": data_as_of_values[-1] if data_as_of_values else None,
        "provenance": {
            "artifact": "shiok-static-json",
            "record_count": len(records),
            "state_counts": state_counts(records),
        },
        "scores": {
            "planning_areas": sorted(scores_by_area),
            "shards": sorted(score_index),
            "index": "scores/index.json",
            "prefix_index": "scores/prefix-index.json",
            "prefix_length": 3,
        },
        "geom": {
            "index": "geom/index.json",
            "postal_index": "geom/postal-index.json",
            "h3_resolution": 8,
            "promoted_resolution": geom_max_promotion_resolution,
            "promotion_mode": "recursive_h3",
            "promotion_threshold_bytes": geom_promotion_threshold_bytes,
        },
        "transit": {
            "pois": transit_report["path"],
            "feature_count": transit_report["feature_count"],
            "counts": transit_report["counts"],
            "source_hashes": source_hashes(TRANSIT_SOURCE_KEYS),
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
        "score_shard_count": len(score_index),
        "geom_shard_count": len([path for path in written_files if path.startswith("geom/h3")]),
        "file_count": len(written_files),
        "written_files": dict(sorted(written_files.items())),
    }


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def artifact_json_path(input_dir: Path, rel_path: str) -> Path:
    plain = input_dir / rel_path
    if plain.is_file():
        return plain
    gzipped = input_dir / f"{rel_path}.gz"
    if gzipped.is_file():
        return gzipped
    return plain


def read_artifact_json(input_dir: Path, rel_path: str) -> Any:
    path = artifact_json_path(input_dir, rel_path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return read_json(path)


def source_hashes(source_keys: Iterable[str]) -> dict[str, Any]:
    manifest_path = PROJECT_ROOT / "raw" / "manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = read_json(manifest_path)
    sources = manifest.get("sources", {}) if isinstance(manifest, dict) else {}
    return {
        key: sources.get(key, {}).get("sha256")
        for key in source_keys
        if isinstance(sources.get(key, {}).get("sha256"), str)
    }


def feature_point(lon: Any, lat: Any) -> list[float] | None:
    try:
        lon_f = float(lon)
        lat_f = float(lat)
    except (TypeError, ValueError):
        return None
    if not (103.5 <= lon_f <= 104.2 and 1.1 <= lat_f <= 1.6):
        return None
    return [round(lon_f, 8), round(lat_f, 8)]


def load_json_if_present(path: Path | None) -> Any | None:
    if path is None or not path.is_file():
        return None
    return read_json(path)


def payload_rows(payload: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("value"), list):
        return [row for row in payload["value"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def natural_service_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def normalize_bus_time(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    digits = re.sub(r"\D+", "", text)
    if not digits:
        return None
    padded = digits.zfill(4)[-4:]
    hour = int(padded[:2])
    minute = int(padded[2:])
    if hour > 29 or minute > 59:
        return None
    if hour >= 24:
        hour -= 24
    return f"{hour:02d}:{minute:02d}"


def bus_time_sort_value(value: str, *, last_bus: bool) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    total = hour * 60 + minute
    if last_bus and hour < 4:
        total += 24 * 60
    return total


def earliest_bus_time(values: Iterable[str]) -> str | None:
    items = list(values)
    return min(items, key=lambda item: bus_time_sort_value(item, last_bus=False)) if items else None


def latest_bus_time(values: Iterable[str]) -> str | None:
    items = list(values)
    return max(items, key=lambda item: bus_time_sort_value(item, last_bus=True)) if items else None


def compact_service_list(service_numbers: Iterable[str], max_items: int = 18) -> str | None:
    services = sorted({service for service in service_numbers if service}, key=natural_service_key)
    if not services:
        return None
    visible = services[:max_items]
    suffix = f" +{len(services) - max_items} more" if len(services) > max_items else ""
    return ", ".join(visible) + suffix


def rounded_frequency(values: Iterable[float]) -> float | None:
    positive = [float(value) for value in values if value and float(value) > 0]
    if not positive:
        return None
    value = round(min(positive), 1)
    return int(value) if value.is_integer() else value


def transit_system_from_station(station: str) -> str:
    return "LRT" if "LRT" in station.upper() else "MRT"


def bus_stop_service_summaries(
    bus_services_payload: dict[str, Any] | list[dict[str, Any]] | None,
    bus_routes_payload: dict[str, Any] | list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    service_meta: dict[tuple[str, int], dict[str, Any]] = {}
    for row in payload_rows(bus_services_payload):
        service_no = str(row.get("ServiceNo", "")).strip()
        direction_value = row.get("Direction")
        if direction_value is None:
            continue
        try:
            direction = int(direction_value)
        except (TypeError, ValueError):
            continue
        if not service_no:
            continue
        service_meta[(service_no, direction)] = {
            "operator": str(row.get("Operator", "")).strip(),
            "category": str(row.get("Category", "")).strip(),
            "am_peak": parse_peak_frequency_minutes(row.get("AM_Peak_Freq")),
            "pm_peak": parse_peak_frequency_minutes(row.get("PM_Peak_Freq")),
        }

    stop_summaries: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "services": set(),
            "operators": set(),
            "categories": set(),
            "am_peak": [],
            "pm_peak": [],
            "wd_first": [],
            "wd_last": [],
            "sat_first": [],
            "sat_last": [],
            "sun_first": [],
            "sun_last": [],
        }
    )
    for row in payload_rows(bus_routes_payload):
        code = str(row.get("BusStopCode", "")).strip()
        service_no = str(row.get("ServiceNo", "")).strip()
        direction_value = row.get("Direction")
        if direction_value is None:
            continue
        try:
            direction = int(direction_value)
        except (TypeError, ValueError):
            continue
        if not code or not service_no:
            continue

        summary = stop_summaries[code]
        summary["services"].add(service_no)
        meta = service_meta.get((service_no, direction), {})
        if meta.get("operator"):
            summary["operators"].add(meta["operator"])
        if meta.get("category"):
            summary["categories"].add(meta["category"])
        if meta.get("am_peak") is not None:
            summary["am_peak"].append(float(meta["am_peak"]))
        if meta.get("pm_peak") is not None:
            summary["pm_peak"].append(float(meta["pm_peak"]))

        for field, target in [
            ("WD_FirstBus", "wd_first"),
            ("WD_LastBus", "wd_last"),
            ("SAT_FirstBus", "sat_first"),
            ("SAT_LastBus", "sat_last"),
            ("SUN_FirstBus", "sun_first"),
            ("SUN_LastBus", "sun_last"),
        ]:
            normalized = normalize_bus_time(row.get(field))
            if normalized:
                summary[target].append(normalized)

    properties_by_stop: dict[str, dict[str, Any]] = {}
    for code, summary in stop_summaries.items():
        services = sorted(summary["services"], key=natural_service_key)
        properties: dict[str, Any] = {
            "service_count": len(services),
            "services": compact_service_list(services),
            "operators": compact_service_list(summary["operators"], max_items=6),
            "weekday_first_bus": earliest_bus_time(summary["wd_first"]),
            "weekday_last_bus": latest_bus_time(summary["wd_last"]),
            "saturday_first_bus": earliest_bus_time(summary["sat_first"]),
            "saturday_last_bus": latest_bus_time(summary["sat_last"]),
            "sunday_first_bus": earliest_bus_time(summary["sun_first"]),
            "sunday_last_bus": latest_bus_time(summary["sun_last"]),
            "am_peak_best_min": rounded_frequency(summary["am_peak"]),
            "pm_peak_best_min": rounded_frequency(summary["pm_peak"]),
        }
        properties_by_stop[code] = {key: value for key, value in properties.items() if value}
    return properties_by_stop


def build_transit_poi_collection(
    mrt_geojson: dict[str, Any] | None,
    bus_payload: dict[str, Any] | list[dict[str, Any]] | None,
    provenance: dict[str, Any] | None = None,
    bus_services_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
    bus_routes_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    station_groups: dict[str, dict[str, Any]] = {}
    bus_summaries = bus_stop_service_summaries(bus_services_payload, bus_routes_payload)

    if isinstance(mrt_geojson, dict):
        for feature in mrt_geojson.get("features", []):
            if not isinstance(feature, dict):
                continue
            geometry = feature.get("geometry", {})
            properties = feature.get("properties", {})
            if not isinstance(geometry, dict) or not isinstance(properties, dict):
                continue
            coordinates = geometry.get("coordinates")
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                continue
            point = feature_point(coordinates[0], coordinates[1])
            if point is None:
                continue
            station = str(properties.get("STATION_NA", "")).strip()
            exit_code = str(properties.get("EXIT_CODE", "")).strip()
            object_id = str(properties.get("OBJECTID", "")).strip()
            name = " ".join(part for part in [station, exit_code] if part).strip()
            system = transit_system_from_station(station)
            if station:
                group = station_groups.setdefault(station, {"points": [], "exits": []})
                group["points"].append(point)
                if exit_code:
                    group["exits"].append(exit_code)
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": point},
                    "properties": {
                        "id": f"mrt:{object_id or len(features)}",
                        "kind": "mrt_exit",
                        "name": name or "MRT/LRT exit",
                        "station": station,
                        "exit": exit_code,
                        "system": system,
                    },
                }
            )

    for station, group in sorted(station_groups.items()):
        points = group["points"]
        if not points:
            continue
        lon = sum(point[0] for point in points) / len(points)
        lat = sum(point[1] for point in points) / len(points)
        station_id = re.sub(r"[^A-Z0-9]+", "_", station.upper()).strip("_")
        label = re.sub(r"\s+(MRT|LRT)\s+STATION$", "", station, flags=re.IGNORECASE).strip()
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 8), round(lat, 8)]},
                "properties": {
                    "id": f"station:{station_id or len(features)}",
                    "kind": "mrt_station",
                    "name": station,
                    "label": label or station,
                    "exit_count": len(set(group["exits"])),
                    "system": transit_system_from_station(station),
                },
            }
        )

    for row in payload_rows(bus_payload):
        point = feature_point(row.get("Longitude"), row.get("Latitude"))
        if point is None:
            continue
        code = str(row.get("BusStopCode", "")).strip()
        properties = {
            "id": f"bus:{code or len(features)}",
            "kind": "bus_stop",
            "name": str(row.get("Description", "")).strip() or "Bus stop",
            "code": code,
            "road": str(row.get("RoadName", "")).strip(),
            **bus_summaries.get(code, {}),
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": point},
                "properties": properties,
            }
        )

    features.sort(
        key=lambda item: (
            str(item["properties"].get("kind", "")),
            str(item["properties"].get("name", "")),
            str(item["properties"].get("id", "")),
        )
    )
    return {
        "type": "FeatureCollection",
        "features": features,
        "provenance": provenance or {},
    }


def export_transit_pois(output_dir: Path = DEFAULT_EXPORT_DIR) -> dict[str, Any]:
    mrt_geojson = load_json_if_present(
        raw_file_from_manifest("mrt_lrt_exits", "mrt_lrt_exits.geojson")
    )
    bus_payload = load_json_if_present(raw_file_from_manifest("bus_stops", "bus_stops.json"))
    bus_services_payload = load_json_if_present(
        raw_file_from_manifest("bus_services", "bus_services.json")
    )
    bus_routes_payload = load_json_if_present(
        raw_file_from_manifest("bus_routes", "bus_routes.json")
    )
    collection = build_transit_poi_collection(
        mrt_geojson if isinstance(mrt_geojson, dict) else None,
        bus_payload if isinstance(bus_payload, (dict, list)) else None,
        {
            "artifact": "shiok-transit-pois",
            "source_hashes": source_hashes(TRANSIT_SOURCE_KEYS),
        },
        bus_services_payload if isinstance(bus_services_payload, (dict, list)) else None,
        bus_routes_payload if isinstance(bus_routes_payload, (dict, list)) else None,
    )
    path = output_dir / "transit" / "pois.json"
    size = write_json(path, collection)
    counts = Counter(
        str(feature.get("properties", {}).get("kind"))
        for feature in collection["features"]
        if isinstance(feature, dict)
    )
    return {
        "path": rel_key(path, output_dir),
        "bytes": size,
        "feature_count": len(collection["features"]),
        "counts": dict(sorted(counts.items())),
    }


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
            raise TypeError(f"score batch chunk must contain a list: {path}")
        for item in payload:
            if not isinstance(item, dict):
                raise TypeError(f"score batch chunk record must be an object: {path}")
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
    files = [
        path
        for path in input_dir.rglob("*")
        if path.is_file() and (path.name.endswith(".json") or path.name.endswith(".json.gz"))
    ]

    if len(files) > MAX_DATA_FILES:
        errors.append(f"file count {len(files)} exceeds {MAX_DATA_FILES}")
    for path in files:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            errors.append(f"{path.relative_to(input_dir)} exceeds {MAX_FILE_BYTES} bytes")

    manifest_path = artifact_json_path(input_dir, "manifest.json")
    score_index_path = artifact_json_path(input_dir, "scores/index.json")
    geom_index_path = artifact_json_path(input_dir, "geom/index.json")
    for rel_path, required in [
        ("manifest.json", manifest_path),
        ("scores/index.json", score_index_path),
        ("geom/index.json", geom_index_path),
    ]:
        if not required.is_file():
            errors.append(f"missing required file: {rel_path}")

    indexed_postals: set[str] = set()
    scored_postals_with_geom_required: set[str] = set()
    score_prefixes = 0
    if score_index_path.is_file():
        score_index = read_artifact_json(input_dir, "scores/index.json")
        if not isinstance(score_index, dict):
            errors.append("scores/index.json must be an object")
            score_index = {}
        for area, postals in sorted(score_index.items()):
            if not isinstance(postals, list):
                errors.append(f"scores/index.json {area}: value must be a list")
                continue
            area_rel_path = f"scores/{area}.json"
            area_path = artifact_json_path(input_dir, area_rel_path)
            if not area_path.is_file():
                errors.append(f"scores/index.json references missing file: {area_rel_path}")
                continue
            records = read_artifact_json(input_dir, area_rel_path)
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

    manifest = None
    if manifest_path.is_file():
        manifest = read_artifact_json(input_dir, "manifest.json")
    if isinstance(manifest, dict):
        prefix_rel_path = manifest.get("scores", {}).get("prefix_index")
        if isinstance(prefix_rel_path, str) and prefix_rel_path:
            prefix_path = artifact_json_path(input_dir, prefix_rel_path)
            if not prefix_path.is_file():
                errors.append(f"manifest references missing file: {prefix_rel_path}")
            else:
                prefix_payload = read_artifact_json(input_dir, prefix_rel_path)
                if not isinstance(prefix_payload, dict):
                    errors.append(f"{prefix_rel_path} must be an object")
                else:
                    score_prefixes = len(prefix_payload)

    geom_postals: set[str] = set()
    if geom_index_path.is_file():
        geom_index = read_artifact_json(input_dir, "geom/index.json")
        if not isinstance(geom_index, dict):
            errors.append("geom/index.json must be an object")
            geom_index = {}
        for cell, children in sorted(geom_index.items()):
            target_cells = children if children else [cell]
            if not isinstance(target_cells, list):
                errors.append(f"geom/index.json {cell}: value must be a list")
                continue
            for target_cell in target_cells:
                geom_rel_path = f"geom/h3/{target_cell}.json"
                geom_path = artifact_json_path(input_dir, geom_rel_path)
                if not geom_path.is_file():
                    errors.append(f"geom/index.json references missing file: {geom_rel_path}")
                    continue
                geom_records = read_artifact_json(input_dir, geom_rel_path)
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

    transit_features = 0
    transit_path = artifact_json_path(input_dir, "transit/pois.json")
    if transit_path.is_file():
        transit = read_artifact_json(input_dir, "transit/pois.json")
        if not isinstance(transit, dict) or transit.get("type") != "FeatureCollection":
            errors.append("transit/pois.json must be a GeoJSON FeatureCollection")
        else:
            features = transit.get("features")
            if not isinstance(features, list):
                errors.append("transit/pois.json features must be a list")
            else:
                transit_features = len(features)
                for index, feature in enumerate(features):
                    if not isinstance(feature, dict):
                        errors.append(f"transit/pois.json:{index}: feature must be an object")
                        continue
                    geometry = feature.get("geometry", {})
                    properties = feature.get("properties", {})
                    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
                        errors.append(f"transit/pois.json:{index}: geometry must be Point")
                    coordinates = (
                        geometry.get("coordinates") if isinstance(geometry, dict) else None
                    )
                    if not isinstance(coordinates, list) or len(coordinates) < 2:
                        errors.append(f"transit/pois.json:{index}: missing coordinates")
                    if not isinstance(properties, dict) or properties.get("kind") not in {
                        "mrt_exit",
                        "mrt_station",
                        "bus_stop",
                    }:
                        errors.append(f"transit/pois.json:{index}: invalid kind")

    report = {
        "input_dir": str(input_dir),
        "ok": not errors,
        "file_count": len(files),
        "indexed_postals": len(indexed_postals),
        "score_prefixes": score_prefixes,
        "geometry_postals": len(geom_postals),
        "transit_features": transit_features,
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
