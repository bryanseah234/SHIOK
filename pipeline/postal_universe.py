"""Build deterministic Singapore postal-code universe candidates."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal, cast

import geopandas as gpd
import httpx
import pandas as pd
from pyproj import Transformer
from pyrosm import OSM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
PROCESSED_DIR = PROJECT_ROOT / "processed"
MANIFEST_PATH = RAW_DIR / "manifest.json"
TMP_DIR = RAW_DIR / "tmp"

USER_AGENT = "SHIOK-Index-Pipeline/1.0 (Singapore Walk-to-Transit Index)"
POSTAL_RE = re.compile(r"^\d{6}$")

ONEMAP_2020_SOURCE_KEY = "postal_universe_onemap_2020"
ONEMAP_2020_URL = (
    "https://raw.githubusercontent.com/xuancong84/singapore-address-heatmap/"
    "master/singpostcode.json.gz"
)
ONEMAP_2020_RAW_NAME = "postal_universe_onemap_2020.json.gz"
ACRA_SOURCE_KEY = "acra_registered_entities"
ACRA_DATASET_ID = "d_3f960c10fed6145404ca7b821f263b87"
ACRA_RAW_NAME = "acra_registered_entities.csv"
SLA_DWELLING_SOURCE_KEY = "sla_dwelling_information"
SLA_DWELLING_DATASET_ID = "d_e4495201ba4f77fa2ef9855bad6d2cd1"
SLA_DWELLING_RAW_NAME = "sla_dwelling_information.geojson"
OFFICIAL_CURRENT_PARQUET = PROCESSED_DIR / "postal_universe_official_current.parquet"
OFFICIAL_CURRENT_SUMMARY = PROCESSED_DIR / "postal_universe_official_current_summary.json"

UniverseMode = Literal["official_current", "candidate_full_registered", "candidate_full_all"]
AcraPolicy = Literal["none", "registered", "all"]


@dataclass
class UniverseRecord:
    postal_code: str
    lat: float | None = None
    lon: float | None = None
    x: float | None = None
    y: float | None = None
    coordinate_source: str | None = None
    coordinate_priority: int = 999
    address: str | None = None
    building: str | None = None
    road_name: str | None = None
    sources: set[str] = field(default_factory=set)

    @property
    def status(self) -> str:
        return (
            "READY_TO_SCORE" if self.lat is not None and self.lon is not None else "NEEDS_GEOCODE"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "postal_code": self.postal_code,
            "lat": self.lat,
            "lon": self.lon,
            "x": self.x,
            "y": self.y,
            "coordinate_source": self.coordinate_source,
            "status": self.status,
            "address": self.address,
            "building": self.building,
            "road_name": self.road_name,
            "sources": sorted(self.sources),
        }


@dataclass(frozen=True)
class SourceStats:
    source_key: str
    raw_records: int
    valid_unique_postals: int
    records_with_coordinates: int
    path: str | None = None
    sha256: str | None = None
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "raw_records": self.raw_records,
            "valid_unique_postals": self.valid_unique_postals,
            "records_with_coordinates": self.records_with_coordinates,
            "path": self.path,
            "sha256": self.sha256,
            "url": self.url,
        }


@dataclass(frozen=True)
class SourceRow:
    postal_code: str
    source_key: str
    priority: int
    lat: float | None = None
    lon: float | None = None
    x: float | None = None
    y: float | None = None
    address: str | None = None
    building: str | None = None
    road_name: str | None = None


def normalize_postal_code(value: Any) -> str | None:
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) <= 6:
        text = text.zfill(6)
    if not POSTAL_RE.fullmatch(text) or text == "000000":
        return None
    return text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {"generated_at": None, "sources": {}}
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {MANIFEST_PATH}")
    return cast(dict[str, Any], data)


def manifest_sha256(source_key: str) -> str | None:
    source = load_manifest().get("sources", {}).get(source_key, {})
    sha = source.get("sha256")
    return sha if isinstance(sha, str) else None


def save_manifest(manifest: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def raw_file_from_manifest(source_key: str, filename: str) -> Path | None:
    manifest = load_manifest()
    source = manifest.get("sources", {}).get(source_key, {})
    sha = source.get("sha256")
    if isinstance(sha, str) and sha:
        path = RAW_DIR / sha / filename
        if path.is_file():
            return path
    matches = sorted(RAW_DIR.glob(f"*/{filename}"))
    return matches[0] if matches else None


def find_raw_file(pattern: str) -> Path:
    matches = sorted(RAW_DIR.rglob(pattern))
    if not matches:
        raise FileNotFoundError(f"raw file not found: {pattern}")
    return matches[0]


def source_priority(source_key: str) -> int:
    return {
        "hdb_existing_building": 10,
        SLA_DWELLING_SOURCE_KEY: 15,
        "osm_addr_postcode": 20,
        ONEMAP_2020_SOURCE_KEY: 30,
        ACRA_SOURCE_KEY: 90,
    }.get(source_key, 999)


def singapore_bbox_4326() -> Any:
    boundary = gpd.read_file(find_raw_file("planning_area_boundary.geojson")).to_crs("EPSG:4326")
    return boundary.geometry.union_all().envelope


def write_download_to_hashed_raw(
    source_key: str,
    source_name: str,
    url: str,
    filename: str,
    content: bytes,
) -> Path:
    sha256 = hashlib.sha256(content).hexdigest()
    target_dir = RAW_DIR / sha256
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(content)

    manifest = load_manifest()
    sources = manifest.setdefault("sources", {})
    sources[source_key] = {
        "source_name": source_name,
        "url_as_discovered": url,
        "sha256": sha256,
        "bytes": len(content),
        "etag": None,
        "last_modified": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    save_manifest(manifest)
    return target


def ensure_onemap_2020_raw(download_missing: bool) -> Path:
    path = raw_file_from_manifest(ONEMAP_2020_SOURCE_KEY, ONEMAP_2020_RAW_NAME)
    if path is not None:
        return path

    tmp_path = TMP_DIR / "xuancong_onemap_2020_singpostcode.json.gz"
    if tmp_path.is_file():
        return write_download_to_hashed_raw(
            ONEMAP_2020_SOURCE_KEY,
            "OneMap-derived Singapore postal dump, accessed 10 Jun 2020 by xuancong84",
            ONEMAP_2020_URL,
            ONEMAP_2020_RAW_NAME,
            tmp_path.read_bytes(),
        )

    if not download_missing:
        raise FileNotFoundError(
            f"{ONEMAP_2020_SOURCE_KEY} not in raw manifest; rerun with --download-missing"
        )

    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        response = c.get(ONEMAP_2020_URL)
        response.raise_for_status()
        return write_download_to_hashed_raw(
            ONEMAP_2020_SOURCE_KEY,
            "OneMap-derived Singapore postal dump, accessed 10 Jun 2020 by xuancong84",
            ONEMAP_2020_URL,
            ONEMAP_2020_RAW_NAME,
            response.content,
        )


def ensure_acra_raw(download_missing: bool) -> Path:
    path = raw_file_from_manifest(ACRA_SOURCE_KEY, ACRA_RAW_NAME)
    if path is not None:
        return path

    tmp_path = TMP_DIR / "acra_entities.csv"
    if tmp_path.is_file():
        return write_download_to_hashed_raw(
            ACRA_SOURCE_KEY,
            "Entities Registered with ACRA",
            f"https://api-open.data.gov.sg/v1/public/api/datasets/{ACRA_DATASET_ID}/initiate-download",
            ACRA_RAW_NAME,
            tmp_path.read_bytes(),
        )

    if not download_missing:
        raise FileNotFoundError(
            f"{ACRA_SOURCE_KEY} not in raw manifest; rerun with --download-missing"
        )

    initiate_url = (
        f"https://api-open.data.gov.sg/v1/public/api/datasets/{ACRA_DATASET_ID}/initiate-download"
    )
    with httpx.Client(
        timeout=300.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as c:
        initiate = c.get(initiate_url)
        initiate.raise_for_status()
        download_url = str(initiate.json().get("data", {}).get("url", ""))
        if not download_url:
            raise ValueError(f"data.gov.sg did not return a download URL for {ACRA_DATASET_ID}")
        response = c.get(download_url)
        response.raise_for_status()
        return write_download_to_hashed_raw(
            ACRA_SOURCE_KEY,
            "Entities Registered with ACRA",
            initiate_url,
            ACRA_RAW_NAME,
            response.content,
        )


def ensure_sla_dwelling_raw(download_missing: bool) -> Path:
    path = raw_file_from_manifest(SLA_DWELLING_SOURCE_KEY, SLA_DWELLING_RAW_NAME)
    if path is not None:
        return path

    tmp_path = TMP_DIR / "sla_dwelling_information.geojson"
    if tmp_path.is_file():
        return write_download_to_hashed_raw(
            SLA_DWELLING_SOURCE_KEY,
            "SLA Dwelling Information",
            f"https://api-open.data.gov.sg/v1/public/api/datasets/{SLA_DWELLING_DATASET_ID}/initiate-download",
            SLA_DWELLING_RAW_NAME,
            tmp_path.read_bytes(),
        )

    if not download_missing:
        raise FileNotFoundError(
            f"{SLA_DWELLING_SOURCE_KEY} not in raw manifest; rerun with --download-missing"
        )

    initiate_url = f"https://api-open.data.gov.sg/v1/public/api/datasets/{SLA_DWELLING_DATASET_ID}/initiate-download"
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        initiate = c.get(initiate_url)
        initiate.raise_for_status()
        download_url = str(initiate.json().get("data", {}).get("url", ""))
        if not download_url:
            raise ValueError(
                f"data.gov.sg did not return a download URL for {SLA_DWELLING_DATASET_ID}"
            )
        response = c.get(download_url)
        response.raise_for_status()
        return write_download_to_hashed_raw(
            SLA_DWELLING_SOURCE_KEY,
            "SLA Dwelling Information",
            initiate_url,
            SLA_DWELLING_RAW_NAME,
            response.content,
        )


def lat_lon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    x, y = wgs84_to_svy21_transformer().transform(lon, lat)
    return float(x), float(y)


def xy_to_lat_lon(x: float, y: float) -> tuple[float, float]:
    lon, lat = svy21_to_wgs84_transformer().transform(x, y)
    return float(lat), float(lon)


@lru_cache(maxsize=1)
def wgs84_to_svy21_transformer() -> Transformer:
    return Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)


@lru_cache(maxsize=1)
def svy21_to_wgs84_transformer() -> Transformer:
    return Transformer.from_crs("EPSG:3414", "EPSG:4326", always_xy=True)


def valid_singapore_lat_lon(lat: float, lon: float) -> bool:
    return 1.1 <= lat <= 1.6 and 103.5 <= lon <= 104.1


def iter_onemap_2020_rows(path: Path) -> tuple[list[SourceRow], SourceStats]:
    rows: list[SourceRow] = []
    seen: set[str] = set()
    emitted: set[str] = set()
    coordinate_postals: set[str] = set()
    raw_records = 0

    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"expected list in {path}")

    for item in payload:
        raw_records += 1
        if not isinstance(item, dict):
            continue
        postal = normalize_postal_code(item.get("POSTAL"))
        if postal is None:
            continue
        seen.add(postal)
        try:
            lat = float(str(item.get("LATITUDE", "")).strip())
            lon = float(str(item.get("LONGITUDE", "")).strip())
        except ValueError:
            continue
        if not valid_singapore_lat_lon(lat, lon):
            continue
        x, y = lat_lon_to_xy(lat, lon)
        coordinate_postals.add(postal)
        if postal in emitted:
            continue
        emitted.add(postal)
        rows.append(
            SourceRow(
                postal_code=postal,
                source_key=ONEMAP_2020_SOURCE_KEY,
                priority=30,
                lat=lat,
                lon=lon,
                x=x,
                y=y,
                address=str(item.get("ADDRESS", "")).strip() or None,
                building=str(item.get("BUILDING", "")).strip() or None,
                road_name=str(item.get("ROAD_NAME", "")).strip() or None,
            )
        )

    return rows, SourceStats(
        source_key=ONEMAP_2020_SOURCE_KEY,
        raw_records=raw_records,
        valid_unique_postals=len(seen),
        records_with_coordinates=len(coordinate_postals),
        path=display_path(path),
        sha256=sha256_file(path),
        url=ONEMAP_2020_URL,
    )


def iter_hdb_existing_building_rows(path: Path) -> tuple[list[SourceRow], SourceStats]:
    gdf = gpd.read_file(path).to_crs("EPSG:3414")
    rows: list[SourceRow] = []
    seen: set[str] = set()
    emitted: set[str] = set()
    coordinate_postals: set[str] = set()
    for _, row in gdf.iterrows():
        postal = normalize_postal_code(row.get("POSTAL_COD"))
        if postal is None:
            continue
        seen.add(postal)
        point = row.geometry.representative_point()
        lat, lon = xy_to_lat_lon(float(point.x), float(point.y))
        if valid_singapore_lat_lon(lat, lon):
            coordinate_postals.add(postal)
            if postal in emitted:
                continue
            emitted.add(postal)
            rows.append(
                SourceRow(
                    postal_code=postal,
                    source_key="hdb_existing_building",
                    priority=10,
                    lat=lat,
                    lon=lon,
                    x=float(point.x),
                    y=float(point.y),
                )
            )
    return rows, SourceStats(
        source_key="hdb_existing_building",
        raw_records=len(gdf),
        valid_unique_postals=len(seen),
        records_with_coordinates=len(coordinate_postals),
        path=display_path(path),
        sha256=sha256_file(path),
    )


def iter_sla_dwelling_rows(path: Path) -> tuple[list[SourceRow], SourceStats]:
    gdf = gpd.read_file(path).to_crs("EPSG:3414")
    rows: list[SourceRow] = []
    seen: set[str] = set()
    emitted: set[str] = set()
    coordinate_postals: set[str] = set()
    for _, row in gdf.iterrows():
        postal = normalize_postal_code(row.get("POSTAL_CODE"))
        if postal is None or row.geometry is None or row.geometry.is_empty:
            continue
        seen.add(postal)
        point = row.geometry.representative_point()
        lat, lon = xy_to_lat_lon(float(point.x), float(point.y))
        if not valid_singapore_lat_lon(lat, lon):
            continue
        coordinate_postals.add(postal)
        if postal in emitted:
            continue
        emitted.add(postal)
        block = str(row.get("HOUSE_BLK_NO", "")).strip()
        street = str(row.get("STREET_NAME", "")).strip()
        rows.append(
            SourceRow(
                postal_code=postal,
                source_key=SLA_DWELLING_SOURCE_KEY,
                priority=15,
                lat=lat,
                lon=lon,
                x=float(point.x),
                y=float(point.y),
                address=" ".join(part for part in (block, street) if part) or None,
                road_name=street or None,
            )
        )
    return rows, SourceStats(
        source_key=SLA_DWELLING_SOURCE_KEY,
        raw_records=len(gdf),
        valid_unique_postals=len(seen),
        records_with_coordinates=len(coordinate_postals),
        path=display_path(path),
        sha256=sha256_file(path),
        url=f"https://data.gov.sg/datasets/{SLA_DWELLING_DATASET_ID}/view",
    )


def iter_osm_addr_postcode_rows(path: Path) -> tuple[list[SourceRow], SourceStats]:
    osm = OSM(str(path), bounding_box=singapore_bbox_4326())
    osm_data = osm.get_data_by_custom_criteria(
        custom_filter={"addr:postcode": True},
        keep_nodes=True,
        keep_ways=True,
        keep_relations=True,
    )
    if osm_data is None or osm_data.empty:
        return [], SourceStats(
            source_key="osm_addr_postcode",
            raw_records=0,
            valid_unique_postals=0,
            records_with_coordinates=0,
            path=display_path(path),
            sha256=sha256_file(path),
        )

    gdf = gpd.GeoDataFrame(osm_data, geometry="geometry", crs="EPSG:4326").to_crs("EPSG:3414")
    rows: list[SourceRow] = []
    seen: set[str] = set()
    emitted: set[str] = set()
    coordinate_postals: set[str] = set()
    for _, row in gdf.iterrows():
        postal = normalize_postal_code(row.get("addr:postcode"))
        if postal is None or row.geometry is None or row.geometry.is_empty:
            continue
        seen.add(postal)
        point = row.geometry.representative_point()
        lat, lon = xy_to_lat_lon(float(point.x), float(point.y))
        if not valid_singapore_lat_lon(lat, lon):
            continue
        coordinate_postals.add(postal)
        if postal in emitted:
            continue
        emitted.add(postal)
        rows.append(
            SourceRow(
                postal_code=postal,
                source_key="osm_addr_postcode",
                priority=20,
                lat=lat,
                lon=lon,
                x=float(point.x),
                y=float(point.y),
                address=str(row.get("addr:full", "")).strip() or None,
                building=str(row.get("name", "")).strip() or None,
                road_name=str(row.get("addr:street", "")).strip() or None,
            )
        )
    return rows, SourceStats(
        source_key="osm_addr_postcode",
        raw_records=len(gdf),
        valid_unique_postals=len(seen),
        records_with_coordinates=len(coordinate_postals),
        path=display_path(path),
        sha256=sha256_file(path),
    )


def iter_acra_rows(path: Path, policy: AcraPolicy) -> tuple[list[SourceRow], SourceStats]:
    if policy == "none":
        return [], SourceStats(
            source_key=ACRA_SOURCE_KEY,
            raw_records=0,
            valid_unique_postals=0,
            records_with_coordinates=0,
            path=display_path(path),
            sha256=sha256_file(path),
        )

    rows: list[SourceRow] = []
    raw_records = 0
    seen: set[str] = set()
    emitted: set[str] = set()
    allowed_statuses = {"REGISTERED", "LIVE"}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_records += 1
            if policy == "registered":
                status = str(row.get("uen_status_desc", "")).strip().upper()
                if status not in allowed_statuses:
                    continue
            postal = normalize_postal_code(row.get("reg_postal_code"))
            if postal is None:
                continue
            seen.add(postal)
            if postal in emitted:
                continue
            emitted.add(postal)
            rows.append(
                SourceRow(
                    postal_code=postal,
                    source_key=ACRA_SOURCE_KEY,
                    priority=90,
                    road_name=str(row.get("reg_street_name", "")).strip() or None,
                )
            )

    return rows, SourceStats(
        source_key=ACRA_SOURCE_KEY,
        raw_records=raw_records,
        valid_unique_postals=len(seen),
        records_with_coordinates=0,
        path=display_path(path),
        sha256=sha256_file(path),
    )


def merge_source_rows(source_rows: Iterable[SourceRow]) -> list[UniverseRecord]:
    records: dict[str, UniverseRecord] = {}
    for row in source_rows:
        record = records.setdefault(row.postal_code, UniverseRecord(postal_code=row.postal_code))
        record.sources.add(row.source_key)
        if (
            row.lat is not None
            and row.lon is not None
            and row.x is not None
            and row.y is not None
            and row.priority < record.coordinate_priority
        ):
            record.lat = round(float(row.lat), 9)
            record.lon = round(float(row.lon), 9)
            record.x = round(float(row.x), 3)
            record.y = round(float(row.y), 3)
            record.coordinate_source = row.source_key
            record.coordinate_priority = row.priority
        if row.address and not record.address:
            record.address = row.address
        if row.building and not record.building:
            record.building = row.building
        if row.road_name and not record.road_name:
            record.road_name = row.road_name

    return [records[key] for key in sorted(records)]


def mode_to_options(mode: UniverseMode) -> tuple[bool, AcraPolicy]:
    if mode == "official_current":
        return False, "none"
    if mode == "candidate_full_registered":
        return True, "registered"
    if mode == "candidate_full_all":
        return True, "all"
    raise ValueError(f"unknown postal universe mode: {mode}")


def source_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value]
    except TypeError:
        return []


def official_current_cache() -> tuple[list[SourceRow], list[SourceStats]] | None:
    if not OFFICIAL_CURRENT_PARQUET.is_file() or not OFFICIAL_CURRENT_SUMMARY.is_file():
        return None

    with open(OFFICIAL_CURRENT_SUMMARY, "r", encoding="utf-8") as f:
        summary: Any = json.load(f)
    if not isinstance(summary, dict):
        return None

    stats_payload = summary.get("source_stats", [])
    if not isinstance(stats_payload, list):
        return None
    stats = [
        SourceStats(
            source_key=str(item["source_key"]),
            raw_records=int(item["raw_records"]),
            valid_unique_postals=int(item["valid_unique_postals"]),
            records_with_coordinates=int(item["records_with_coordinates"]),
            path=item.get("path"),
            sha256=item.get("sha256"),
            url=item.get("url"),
        )
        for item in stats_payload
        if isinstance(item, dict) and "source_key" in item
    ]
    stats_by_key = {stat.source_key: stat for stat in stats}
    expected_hashes = {
        "hdb_existing_building": manifest_sha256("building_points"),
        SLA_DWELLING_SOURCE_KEY: manifest_sha256(SLA_DWELLING_SOURCE_KEY),
        "osm_addr_postcode": manifest_sha256("osm_extract"),
    }
    for source_key, expected_hash in expected_hashes.items():
        if not expected_hash or stats_by_key.get(source_key, None) is None:
            return None
        if stats_by_key[source_key].sha256 != expected_hash:
            return None

    df = pd.read_parquet(OFFICIAL_CURRENT_PARQUET)
    rows: list[SourceRow] = []
    for _, row in df.iterrows():
        postal = str(row["postal_code"])
        coordinate_source = row.get("coordinate_source")
        for source_key in source_list(row.get("sources")):
            has_coordinate = source_key == coordinate_source and row.get("lat") is not None
            rows.append(
                SourceRow(
                    postal_code=postal,
                    source_key=source_key,
                    priority=source_priority(source_key),
                    lat=float(row["lat"]) if has_coordinate else None,
                    lon=float(row["lon"]) if has_coordinate else None,
                    x=float(row["x"]) if has_coordinate else None,
                    y=float(row["y"]) if has_coordinate else None,
                    address=row.get("address"),
                    building=row.get("building"),
                    road_name=row.get("road_name"),
                )
            )
    return rows, stats


def build_universe(
    mode: UniverseMode,
    download_missing: bool = False,
    output_path: Path | None = None,
    summary_path: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    include_onemap_2020, acra_policy = mode_to_options(mode)
    all_rows: list[SourceRow] = []
    stats: list[SourceStats] = []

    cached_current = official_current_cache() if mode != "official_current" else None
    if cached_current is not None:
        current_rows, current_stats = cached_current
        all_rows.extend(current_rows)
        stats.extend(current_stats)
        print(
            "[postal-universe] reusing validated official_current cache "
            f"({len({row.postal_code for row in current_rows})} postals)",
            flush=True,
        )
    else:
        print("[postal-universe] loading HDB existing building postals...", flush=True)
        hdb_rows, hdb_stats = iter_hdb_existing_building_rows(
            find_raw_file("building_points.geojson")
        )
        all_rows.extend(hdb_rows)
        stats.append(hdb_stats)
        print(
            f"[postal-universe] HDB: {hdb_stats.valid_unique_postals} unique, "
            f"{hdb_stats.records_with_coordinates} with coordinates",
            flush=True,
        )

        print("[postal-universe] loading SLA dwelling postals...", flush=True)
        sla_dwelling_rows, sla_dwelling_stats = iter_sla_dwelling_rows(
            ensure_sla_dwelling_raw(download_missing)
        )
        all_rows.extend(sla_dwelling_rows)
        stats.append(sla_dwelling_stats)
        print(
            f"[postal-universe] SLA Dwelling: {sla_dwelling_stats.valid_unique_postals} unique, "
            f"{sla_dwelling_stats.records_with_coordinates} with coordinates",
            flush=True,
        )

        print("[postal-universe] loading Singapore OSM addr:postcode postals...", flush=True)
        osm_rows, osm_stats = iter_osm_addr_postcode_rows(find_raw_file("*.osm.pbf"))
        all_rows.extend(osm_rows)
        stats.append(osm_stats)
        print(
            f"[postal-universe] OSM: {osm_stats.valid_unique_postals} unique, "
            f"{osm_stats.records_with_coordinates} with coordinates",
            flush=True,
        )

    if include_onemap_2020:
        print("[postal-universe] loading candidate OneMap-derived 2020 postal dump...", flush=True)
        onemap_path = ensure_onemap_2020_raw(download_missing)
        onemap_rows, onemap_stats = iter_onemap_2020_rows(onemap_path)
        all_rows.extend(onemap_rows)
        stats.append(onemap_stats)
        print(
            f"[postal-universe] OneMap 2020: {onemap_stats.valid_unique_postals} unique, "
            f"{onemap_stats.records_with_coordinates} with coordinates",
            flush=True,
        )

    if acra_policy != "none":
        print(f"[postal-universe] loading ACRA postals with policy={acra_policy}...", flush=True)
        acra_path = ensure_acra_raw(download_missing)
        acra_rows, acra_stats = iter_acra_rows(acra_path, acra_policy)
        all_rows.extend(acra_rows)
        stats.append(acra_stats)
        print(
            f"[postal-universe] ACRA: {acra_stats.valid_unique_postals} unique, "
            f"{acra_stats.records_with_coordinates} with coordinates",
            flush=True,
        )

    print("[postal-universe] merging source rows...", flush=True)
    records = merge_source_rows(all_rows)
    df = pd.DataFrame([record.as_dict() for record in records])
    if not df.empty:
        df = df.sort_values("postal_code", kind="stable").reset_index(drop=True)

    sources_by_postal = {
        stat.source_key: {row.postal_code for row in all_rows if row.source_key == stat.source_key}
        for stat in stats
    }
    source_only_counts = {}
    for source_key, postals in sources_by_postal.items():
        other_postals = set().union(
            *(values for key, values in sources_by_postal.items() if key != source_key)
        )
        source_only_counts[source_key] = len(postals - other_postals)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "acra_policy": acra_policy,
        "total_unique_postals": int(len(df)),
        "ready_to_score": int((df["status"] == "READY_TO_SCORE").sum()) if not df.empty else 0,
        "needs_geocode": int((df["status"] == "NEEDS_GEOCODE").sum()) if not df.empty else 0,
        "source_stats": [stat.as_dict() for stat in stats],
        "source_only_counts": source_only_counts,
        "warnings": (
            [
                "postal_universe_onemap_2020 is a third-party OneMap-derived 2020 dump and must be human-approved before full-batch use"
            ]
            if include_onemap_2020
            else []
        ),
    }

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = output_path or PROCESSED_DIR / f"postal_universe_{mode}.parquet"
    summary_path = summary_path or PROCESSED_DIR / f"postal_universe_{mode}_summary.json"
    df.to_parquet(output_path, index=False)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    return df, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build postal universe candidates.")
    parser.add_argument(
        "--mode",
        choices=["official_current", "candidate_full_registered", "candidate_full_all"],
        default="official_current",
    )
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    df, summary = build_universe(
        mode=cast(UniverseMode, args.mode),
        download_missing=bool(args.download_missing),
        output_path=args.output,
        summary_path=args.summary,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {len(df)} postals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
