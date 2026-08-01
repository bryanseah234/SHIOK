from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd

from pipeline.network_qa import validate_network_qa


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
QA_DIR = PROJECT_ROOT / "qa"
PROCESSED_DIR = PROJECT_ROOT / "processed"
MANIFEST_PATH = RAW_DIR / "manifest.json"
PILOT_AREAS = ("Toa Payoh", "Bukit Timah", "Downtown Core")
VALID_AREAS = ("pilot", "island")
NETWORK_SOURCE_FILES = {
    "planning_area_boundary": "planning_area_boundary.geojson",
    "covered_linkway": "covered_linkway.zip",
    "osm_extract": "osm_extract.osm.pbf",
}
ISLAND_NETWORK_SOURCE_FILES = {
    "overhead_bridge_underpass": "overhead_bridge_underpass.zip",
    "building_points": "building_points.geojson",
    "nparks_heritage_trees": "nparks_heritage_trees.geojson",
    "nparks_heritage_road_green_buffers": "nparks_heritage_road_green_buffers.geojson",
    "nparks_nature_ways": "nparks_nature_ways.geojson",
    "nparks_park_connector_loop": "nparks_park_connector_loop.geojson",
    "nparks_tracks": "nparks_tracks.geojson",
}


def network_source_files_for_area(area: str) -> dict[str, str]:
    source_files = dict(NETWORK_SOURCE_FILES)
    if area == "island":
        source_files.update(ISLAND_NETWORK_SOURCE_FILES)
    return source_files


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return payload


def source_file_status(
    *,
    source_key: str,
    filename: str,
    raw_dir: Path,
    manifest_sources: dict[str, Any],
) -> dict[str, Any]:
    entry = manifest_sources.get(source_key)
    status: dict[str, Any] = {
        "source_key": source_key,
        "filename": filename,
        "present": False,
        "manifest_entry_present": isinstance(entry, dict),
        "sha256_expected": entry.get("sha256") if isinstance(entry, dict) else None,
        "sha256_actual": None,
        "hash_ok": False,
        "bytes_manifest": entry.get("bytes") if isinstance(entry, dict) else None,
        "bytes_actual": None,
        "path": None,
        "errors": [],
    }

    if not isinstance(entry, dict):
        status["errors"].append(f"missing manifest source: {source_key}")
        return status

    expected_hash = entry.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        status["errors"].append(f"invalid manifest sha256 for {source_key}")
        return status

    path = raw_dir / expected_hash / filename
    status["path"] = str(path)
    if not path.is_file():
        status["errors"].append(f"missing raw file: {path}")
        return status

    status["present"] = True
    status["bytes_actual"] = path.stat().st_size
    actual_hash = sha256_file(path)
    status["sha256_actual"] = actual_hash
    status["hash_ok"] = actual_hash == expected_hash
    if not status["hash_ok"]:
        status["errors"].append(
            f"hash mismatch for {source_key}: expected {expected_hash}, got {actual_hash}"
        )
    if isinstance(entry.get("bytes"), int) and entry["bytes"] != status["bytes_actual"]:
        status["errors"].append(
            f"byte mismatch for {source_key}: manifest has {entry['bytes']}, "
            f"file has {status['bytes_actual']}"
        )
    return status


def selected_planning_areas(
    planning_areas: gpd.GeoDataFrame, area: str
) -> tuple[gpd.GeoDataFrame, list[str]]:
    if area == "pilot":
        selected = planning_areas[
            planning_areas["PLN_AREA_N"].str.upper().isin([name.upper() for name in PILOT_AREAS])
        ].copy()
        return selected, list(PILOT_AREAS)
    if area == "island":
        selected = planning_areas.copy()
        area_names = sorted(str(name) for name in selected["PLN_AREA_N"].dropna().unique())
        return selected, area_names
    raise ValueError(f"unknown network area: {area}")


def inspect_network_geometries(
    *,
    area: str,
    planning_area_path: Path,
    covered_linkway_zip: Path,
) -> dict[str, Any]:
    planning_areas = gpd.read_file(planning_area_path).to_crs(epsg=3414)
    selected, area_names = selected_planning_areas(planning_areas, area)
    union_poly = selected.geometry.union_all()
    buffered = union_poly.buffer(500)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(covered_linkway_zip, "r") as z:
            z.extractall(tmp_dir)
        shapefiles = sorted(tmp_dir.rglob("*.shp"))
        if not shapefiles:
            raise ValueError(f"covered linkway zip contains no shapefile: {covered_linkway_zip}")
        linkways = gpd.read_file(shapefiles[0]).to_crs(epsg=3414)

    selected_linkways = gpd.sjoin(
        linkways,
        selected[["PLN_AREA_N", "geometry"]],
        how="inner",
        predicate="intersects",
    )
    length_m = (
        float((selected_linkways.geometry.length / 2.0).sum())
        if not selected_linkways.empty
        else 0.0
    )

    return {
        "area": area,
        "planning_area_count": int(len(selected)),
        "planning_area_names": area_names,
        "planning_area_area_km2": float(union_poly.area / 1_000_000.0),
        "clip_buffer_m": 500,
        "clip_buffered_area_km2": float(buffered.area / 1_000_000.0),
        "covered_linkway_features_in_scope": int(len(selected_linkways)),
        "covered_linkway_length_m": length_m,
        "crs_internal": "EPSG:3414",
    }


def build_network_preflight(
    *,
    area: str,
    raw_dir: Path = RAW_DIR,
    manifest_path: Path = MANIFEST_PATH,
    qa_dir: Path = QA_DIR,
    processed_dir: Path = PROCESSED_DIR,
    inspect_geometries: bool = True,
) -> tuple[bool, dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    raw_statuses: dict[str, Any] = {}

    if area not in VALID_AREAS:
        errors.append(f"unsupported network area: {area}")

    manifest: dict[str, Any] = {}
    manifest_sources: dict[str, Any] = {}
    if not manifest_path.is_file():
        errors.append(f"missing manifest: {manifest_path}")
    else:
        try:
            manifest = load_manifest(manifest_path)
            sources = manifest.get("sources", {})
            if isinstance(sources, dict):
                manifest_sources = sources
            else:
                errors.append("manifest sources must be an object")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"could not read manifest: {exc}")

    for source_key, filename in network_source_files_for_area(area).items():
        raw_statuses[source_key] = source_file_status(
            source_key=source_key,
            filename=filename,
            raw_dir=raw_dir,
            manifest_sources=manifest_sources,
        )
        errors.extend(raw_statuses[source_key]["errors"])

    geometry_summary = None
    if inspect_geometries and not errors:
        try:
            geometry_summary = inspect_network_geometries(
                area=area,
                planning_area_path=Path(raw_statuses["planning_area_boundary"]["path"]),
                covered_linkway_zip=Path(raw_statuses["covered_linkway"]["path"]),
            )
            if geometry_summary["planning_area_count"] == 0:
                errors.append(f"no planning areas selected for {area}")
            if geometry_summary["covered_linkway_features_in_scope"] == 0:
                warnings.append(f"no covered linkway features selected for {area}")
        except Exception as exc:  # pragma: no cover - geopandas backends vary by platform
            errors.append(f"could not inspect network geometries: {exc}")

    network_path = processed_dir / (
        "network.parquet" if area == "pilot" else "network_island.parquet"
    )
    qa_path = qa_dir / f"conflation_qa_{area}.json"
    debug_path = qa_dir / f"{area}_debug.geojson"
    qa_ok, qa_summary = validate_network_qa(
        qa_path,
        debug_path,
        require_production_sources=area == "island",
    )
    output_status = {
        "network_parquet": {
            "path": str(network_path),
            "exists": network_path.is_file(),
            "bytes": network_path.stat().st_size if network_path.is_file() else None,
        },
        "qa_json": {
            "path": str(qa_path),
            "exists": qa_path.is_file(),
            "ok": qa_ok,
            "summary": qa_summary,
        },
        "debug_geojson": {
            "path": str(debug_path),
            "exists": debug_path.is_file(),
            "bytes": debug_path.stat().st_size if debug_path.is_file() else None,
        },
    }

    can_run_after_human_approval = not errors
    report: dict[str, Any] = {
        "ok": not errors,
        "area": area,
        "manifest": {
            "path": str(manifest_path),
            "generated_at": manifest.get("generated_at"),
        },
        "raw_inputs": raw_statuses,
        "geometry": geometry_summary,
        "outputs": output_status,
        "checkpoint": {
            "network_build_command": f"uv run python run.py network --area {area}",
            "human_approval_required_before_build": area == "island",
            "build_started": False,
            "build_allowed_now": False,
            "can_run_after_human_approval": can_run_after_human_approval,
        },
        "errors": errors,
        "warnings": warnings,
    }
    return not errors, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preflight network-build inputs without building the graph."
    )
    parser.add_argument("--area", choices=VALID_AREAS, default="island")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--qa-dir", type=Path, default=QA_DIR)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument(
        "--skip-geometry-inspection",
        action="store_true",
        help="Only verify manifest/raw files; intended for narrow tests.",
    )
    args = parser.parse_args()

    ok, report = build_network_preflight(
        area=args.area,
        raw_dir=args.raw_dir,
        manifest_path=args.manifest,
        qa_dir=args.qa_dir,
        processed_dir=args.processed_dir,
        inspect_geometries=not args.skip_geometry_inspection,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
