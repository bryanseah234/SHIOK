import builtins
import json
import math
import tempfile
import warnings
import zipfile
from collections import Counter
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
from centerline.geometry import Centerline
from pyrosm import OSM
from shapely.errors import ShapelyDeprecationWarning
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import nearest_points, substring

from pipeline.shade import (
    NPARKS_SHADE_SOURCE_KEYS,
    compute_edge_shade_ratio,
    prepare_shade_proxy_geometries,
)

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)


def print(*args, **kwargs):
    """Flush long-running build logs when stdout is redirected to a file."""
    kwargs.setdefault("flush", True)
    return builtins.print(*args, **kwargs)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
QA_DIR = PROJECT_ROOT / "qa"
PROCESSED_DIR = PROJECT_ROOT / "processed"
AUDITED_SHELTER_CORRECTIONS_PATH = PROJECT_ROOT / "data" / "audited_shelter_corrections.geojson"
QA_DIR.mkdir(exist_ok=True, parents=True)
PROCESSED_DIR.mkdir(exist_ok=True, parents=True)
PILOT_AREAS = ["Toa Payoh", "Bukit Timah", "Downtown Core"]
VALID_SCOPES = {"pilot", "island"}
DOMINANT_COMPONENTS_BY_SCOPE = {"pilot": 3, "island": 1}
BUS_NON_TRANSIT_THRESHOLD_M = 250.0
MRT_NON_TRANSIT_THRESHOLD_M = 1200.0
ISLAND_OWNER_APPROVED_BRIDGE_THRESHOLD_M = 50.0
PARK_NON_TRANSIT_AREAS = {
    "CENTRAL WATER CATCHMENT",
    "LIM CHU KANG",
    "MANDAI",
    "MARINA EAST",
    "PASIR RIS",
    "PUNGGOL",
    "SOUTHERN ISLANDS",
    "TENGAH",
}
PARK_NAME_TOKENS = {
    "boardwalk",
    "broadwalk",
    "central catchment park connector",
    "circle road",
    "coney island",
    "park connector",
    "treasure hunters",
}
APPROVED_CORRECTION_STATUSES = {"approved"}
COVERED_CORRECTION_VALUES = {"1", "true", "yes", "covered"}
HDB_VOID_DECK_BUILDING_TAGS = {"apartments", "residential"}
OSM_ROOF_SHELTER_TAGS = {"roof", "canopy"}
OSM_COVERED_TAG_VALUES = {"yes", "covered", "arcade", "colonnade", "building_passage"}
OSM_TUNNEL_COVERED_VALUES = {"yes", "building_passage"}
OSM_INDOOR_COVERED_VALUES = {"yes", "building_passage"}
OSM_LOCATION_COVERED_VALUES = {"underground", "indoor"}
HDB_PRECINCT_COVERED_HIGHWAYS = {
    "corridor",
    "footway",
    "living_street",
    "path",
    "pedestrian",
    "steps",
}


def normalized_text_series(frame: gpd.GeoDataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series("", index=frame.index)
    return frame[column].fillna("").astype(str).str.strip().str.lower()


def native_osm_covered_mask(edges_gdf: gpd.GeoDataFrame) -> pd.Series:
    """Return OSM-tagged edges that are explicit rain-shelter evidence."""
    mask = pd.Series(False, index=edges_gdf.index)
    covered = normalized_text_series(edges_gdf, "covered")
    highway = normalized_text_series(edges_gdf, "highway")
    tunnel = normalized_text_series(edges_gdf, "tunnel")
    indoor = normalized_text_series(edges_gdf, "indoor")
    location = normalized_text_series(edges_gdf, "location")

    mask |= covered.isin(OSM_COVERED_TAG_VALUES)
    mask |= highway.str.contains("covered", na=False)
    mask |= tunnel.isin(OSM_TUNNEL_COVERED_VALUES)
    mask |= indoor.isin(OSM_INDOOR_COVERED_VALUES)
    mask |= location.isin(OSM_LOCATION_COVERED_VALUES)
    return mask


def find_raw_file(pattern: str) -> Path | None:
    for path in RAW_DIR.rglob(pattern):
        if path.is_file():
            return path
    return None


def require_raw_file(pattern: str) -> Path:
    path = find_raw_file(pattern)
    if path is None:
        raise FileNotFoundError(f"raw file not found: {pattern}")
    return path


def nearest_distance_and_position(
    target_gdf: gpd.GeoDataFrame | gpd.GeoSeries,
    target_sindex,
    geom,
    *,
    max_distance: float | None = None,
) -> tuple[float, int | None]:
    if target_gdf.empty or geom is None or geom.is_empty:
        return float("inf"), None

    indices, distances = target_sindex.nearest(
        geom,
        return_all=False,
        max_distance=max_distance,
        return_distance=True,
    )
    if len(distances) == 0:
        return float("inf"), None

    position = int(indices[1][0])
    return float(distances[0]), position


def nearest_distance_and_index(
    target_gdf: gpd.GeoDataFrame | gpd.GeoSeries,
    target_sindex,
    geom,
    *,
    max_distance: float | None = None,
) -> tuple[float, object | None]:
    dist, position = nearest_distance_and_position(
        target_gdf,
        target_sindex,
        geom,
        max_distance=max_distance,
    )
    if position is None:
        return dist, None
    return dist, target_gdf.index[position]


def nearest_point_on_geometry(
    target_gdf: gpd.GeoDataFrame | gpd.GeoSeries,
    target_sindex,
    point: Point,
    *,
    max_distance: float,
) -> tuple[Point | None, float]:
    dist, position = nearest_distance_and_position(
        target_gdf,
        target_sindex,
        point,
        max_distance=max_distance,
    )
    if position is None:
        return None, float("inf")

    target_geom = target_gdf.iloc[position]
    if hasattr(target_geom, "geometry"):
        target_geom = target_geom.geometry
    point_on_target, _ = nearest_points(target_geom, point)
    return point_on_target, dist


def nearest_point_and_index_on_geometry(
    target_gdf: gpd.GeoDataFrame | gpd.GeoSeries,
    target_sindex,
    point: Point,
    *,
    max_distance: float,
) -> tuple[Point | None, float, object | None]:
    dist, position = nearest_distance_and_position(
        target_gdf,
        target_sindex,
        point,
        max_distance=max_distance,
    )
    if position is None:
        return None, float("inf"), None

    target_geom = target_gdf.iloc[position]
    if hasattr(target_geom, "geometry"):
        target_geom = target_geom.geometry
    point_on_target, _ = nearest_points(target_geom, point)
    return point_on_target, dist, target_gdf.index[position]


def split_edges_at_points(
    edges_gdf: gpd.GeoDataFrame,
    split_points_by_edge: dict[object, list[Point]],
    *,
    min_segment_m: float = 0.05,
) -> gpd.GeoDataFrame:
    """Split host edges at synthetic snap points so snapped links are routable."""
    if not split_points_by_edge:
        return edges_gdf

    split_rows = []
    drop_indices = []
    for edge_idx, split_points in split_points_by_edge.items():
        if edge_idx not in edges_gdf.index:
            continue
        row = edges_gdf.loc[edge_idx]
        geom = row.geometry
        if geom is None or geom.is_empty or geom.length <= 0:
            continue

        distances = []
        for point in split_points:
            distance = float(geom.project(point))
            if min_segment_m < distance < geom.length - min_segment_m:
                distances.append(distance)
        unique_distances = sorted({round(distance, 6) for distance in distances})
        if not unique_distances:
            continue

        start = 0.0
        segments = []
        for end in unique_distances + [float(geom.length)]:
            segment = substring(geom, start, end)
            start = end
            if isinstance(segment, LineString) and segment.length >= min_segment_m:
                segments.append(segment)
        if len(segments) < 2:
            continue

        drop_indices.append(edge_idx)
        for segment in segments:
            split_row = row.copy()
            split_row["geometry"] = segment
            split_row["length_m"] = float(segment.length)
            if "length" in split_row.index:
                split_row["length"] = float(segment.length)
            split_rows.append(split_row)

    if not split_rows:
        return edges_gdf

    retained = edges_gdf.drop(index=drop_indices)
    split_gdf = gpd.GeoDataFrame(split_rows, geometry="geometry", crs=edges_gdf.crs)
    return pd.concat([retained, split_gdf], ignore_index=True)


def load_transit_reference_points() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    bus_path = find_raw_file("bus_stops.json")
    mrt_path = find_raw_file("mrt_lrt_exits.geojson")

    bus_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    if bus_path is not None:
        with open(bus_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("value", payload) if isinstance(payload, dict) else payload
        bus_df = pd.DataFrame(rows)
        if {"Longitude", "Latitude"}.issubset(bus_df.columns):
            bus_gdf = gpd.GeoDataFrame(
                bus_df,
                geometry=gpd.points_from_xy(bus_df["Longitude"], bus_df["Latitude"]),
                crs="EPSG:4326",
            ).to_crs(epsg=3414)

    mrt_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    if mrt_path is not None:
        mrt_gdf = gpd.read_file(mrt_path).to_crs(epsg=3414)

    return bus_gdf, mrt_gdf


def compute_polygon_match_ratio(
    edges_gdf: gpd.GeoDataFrame,
    polygon_gdf: gpd.GeoDataFrame,
    *,
    buffer_m: float = 3.0,
    label: str = "shelter polygons",
) -> pd.Series:
    ratios = pd.Series(0.0, index=edges_gdf.index, dtype=float)
    if edges_gdf.empty or polygon_gdf.empty:
        return ratios

    polygon_buffers = gpd.GeoSeries(polygon_gdf.geometry.buffer(buffer_m), crs=polygon_gdf.crs)
    print(f"Querying edge/{label} buffer intersections...")
    pairs = polygon_buffers.sindex.query(edges_gdf.geometry, predicate="intersects")
    if pairs.size == 0:
        return ratios

    pair_df = pd.DataFrame({"edge_pos": pairs[0], "polygon_pos": pairs[1]})
    matched_edges = pair_df["edge_pos"].nunique()
    print(f"Computing {label} coverage ratios for {matched_edges} candidate edges...")

    for count, (edge_pos, group) in enumerate(pair_df.groupby("edge_pos", sort=False), start=1):
        if count % 25000 == 0:
            print(f"  {label} coverage progress: {count}/{matched_edges} candidate edges")

        edge_geom = edges_gdf.geometry.iloc[int(edge_pos)]
        edge_len = edge_geom.length
        if edge_len <= 0:
            continue

        candidate_union = polygon_buffers.iloc[group["polygon_pos"].to_numpy()].union_all()
        covered_len = edge_geom.intersection(candidate_union).length
        ratios.iat[int(edge_pos)] = min(1.0, covered_len / edge_len)

    return ratios


def compute_lta_match_ratio(
    edges_gdf: gpd.GeoDataFrame,
    lta_gdf: gpd.GeoDataFrame,
) -> pd.Series:
    return compute_polygon_match_ratio(
        edges_gdf,
        lta_gdf,
        buffer_m=3.0,
        label="LTA shelter",
    )


def _blank_text_mask(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().isin(["", "nan", "None"])


def ensure_columns(frame: gpd.GeoDataFrame, defaults: dict[str, object]) -> None:
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default


def apply_polygon_coverage_attribution(
    edges_gdf: gpd.GeoDataFrame,
    polygon_gdf: gpd.GeoDataFrame,
    *,
    source_layer: str,
    ratio_threshold: float,
    buffer_m: float = 3.0,
    label: str,
    overwrite_sources: set[str] | None = None,
) -> tuple[pd.Series, float]:
    """Mark graph edges covered by source polygons and preserve why they are covered."""
    match_ratio = compute_polygon_match_ratio(
        edges_gdf,
        polygon_gdf,
        buffer_m=buffer_m,
        label=label,
    )
    match_mask = match_ratio >= ratio_threshold
    if not bool(match_mask.any()):
        return match_mask, 0.0

    ensure_columns(
        edges_gdf,
        {
            "is_covered": 0,
            "covered": "",
            "source_layer": "",
            "confidence": "",
        },
    )
    edges_gdf.loc[match_mask, "is_covered"] = 1
    covered_blank = _blank_text_mask(edges_gdf["covered"])
    edges_gdf.loc[match_mask & covered_blank, "covered"] = "yes"

    source_blank = _blank_text_mask(edges_gdf["source_layer"])
    if overwrite_sources:
        overwrite_mask = edges_gdf["source_layer"].astype(str).isin(overwrite_sources)
        source_blank = source_blank | overwrite_mask
    edges_gdf.loc[match_mask & source_blank, "source_layer"] = source_layer

    confidence_blank = _blank_text_mask(edges_gdf["confidence"])
    edges_gdf.loc[match_mask & confidence_blank, "confidence"] = "source_polygon_match"
    return match_mask, float(edges_gdf.loc[match_mask, "geometry"].length.sum())


def apply_hdb_precinct_footway_coverage(
    edges_gdf: gpd.GeoDataFrame,
    hdb_footprints_gdf: gpd.GeoDataFrame,
    *,
    footprint_buffer_m: float = 14.0,
    min_match_ratio: float = 0.70,
) -> tuple[pd.Series, dict[str, object]]:
    """Mark existing pedestrian graph edges that sit inside HDB precinct shelter buffers.

    This does not add new geometry. It only upgrades already-routable pedestrian
    edges where HDB building-footprint evidence strongly overlaps the edge.
    """
    empty_mask = pd.Series(False, index=edges_gdf.index)
    report: dict[str, object] = {
        "candidate_buildings": len(hdb_footprints_gdf),
        "eligible_edge_count": 0,
        "marked_edges": 0,
        "length_m": 0.0,
        "footprint_buffer_m": footprint_buffer_m,
        "min_match_ratio": min_match_ratio,
        "allowed_highways": sorted(HDB_PRECINCT_COVERED_HIGHWAYS),
        "source": "hdb_points_plus_osm_residential_footprints",
        "confidence": "inferred_existing_pedestrian_edge",
    }
    if edges_gdf.empty or hdb_footprints_gdf.empty or "highway" not in edges_gdf.columns:
        return empty_mask, report

    highway = edges_gdf["highway"].fillna("").astype(str).str.strip().str.lower()
    eligible_mask = highway.isin(HDB_PRECINCT_COVERED_HIGHWAYS)
    report["eligible_edge_count"] = int(eligible_mask.sum())
    if not bool(eligible_mask.any()):
        return empty_mask, report

    eligible_edges = edges_gdf.loc[eligible_mask].copy()
    buffered_hdb = hdb_footprints_gdf.copy()
    buffered_hdb["geometry"] = buffered_hdb.geometry.buffer(footprint_buffer_m)
    match_ratio = compute_polygon_match_ratio(
        eligible_edges,
        buffered_hdb,
        buffer_m=0.0,
        label="HDB precinct pedestrian-edge",
    )
    local_mask = match_ratio >= min_match_ratio
    if not bool(local_mask.any()):
        return empty_mask, report

    marked_index = local_mask[local_mask].index
    full_mask = pd.Series(False, index=edges_gdf.index)
    full_mask.loc[marked_index] = True

    ensure_columns(
        edges_gdf,
        {
            "is_covered": 0,
            "covered": "",
            "source_layer": "",
            "confidence": "",
        },
    )
    edges_gdf.loc[full_mask, "is_covered"] = 1
    covered_blank = _blank_text_mask(edges_gdf["covered"])
    edges_gdf.loc[full_mask & covered_blank, "covered"] = "yes"
    source_blank = _blank_text_mask(edges_gdf["source_layer"])
    edges_gdf.loc[full_mask & source_blank, "source_layer"] = "inferred_hdb_precinct_footway"
    confidence_blank = _blank_text_mask(edges_gdf["confidence"])
    edges_gdf.loc[full_mask & confidence_blank, "confidence"] = "inferred_existing_pedestrian_edge"

    report["marked_edges"] = int(full_mask.sum())
    report["length_m"] = float(edges_gdf.loc[full_mask, "geometry"].length.sum())
    return full_mask, report


def apply_hdb_point_footway_coverage(
    edges_gdf: gpd.GeoDataFrame,
    hdb_points_gdf: gpd.GeoDataFrame,
    *,
    point_buffer_m: float = 18.0,
    min_match_ratio: float = 0.65,
) -> tuple[pd.Series, dict[str, object]]:
    """Fallback HDB coverage for existing pedestrian edges near official HDB points.

    This is intentionally weaker than footprint evidence: it marks only existing
    pedestrian edges and records a separate point-proxy source layer.
    """
    empty_mask = pd.Series(False, index=edges_gdf.index)
    report: dict[str, object] = {
        "candidate_hdb_points": len(hdb_points_gdf),
        "eligible_edge_count": 0,
        "marked_edges": 0,
        "newly_marked_edges": 0,
        "length_m": 0.0,
        "point_buffer_m": point_buffer_m,
        "min_match_ratio": min_match_ratio,
        "allowed_highways": sorted(HDB_PRECINCT_COVERED_HIGHWAYS),
        "source": "hdb_building_points",
        "confidence": "inferred_existing_pedestrian_edge_point_proxy",
    }
    if edges_gdf.empty or hdb_points_gdf.empty or "highway" not in edges_gdf.columns:
        return empty_mask, report

    highway = edges_gdf["highway"].fillna("").astype(str).str.strip().str.lower()
    eligible_mask = highway.isin(HDB_PRECINCT_COVERED_HIGHWAYS)
    report["eligible_edge_count"] = int(eligible_mask.sum())
    if not bool(eligible_mask.any()):
        return empty_mask, report

    eligible_edges = edges_gdf.loc[eligible_mask].copy()
    point_buffers = hdb_points_gdf.copy()
    point_buffers["geometry"] = point_buffers.geometry.buffer(point_buffer_m)
    match_ratio = compute_polygon_match_ratio(
        eligible_edges,
        point_buffers,
        buffer_m=0.0,
        label="HDB point pedestrian-edge",
    )
    local_mask = match_ratio >= min_match_ratio
    if not bool(local_mask.any()):
        return empty_mask, report

    marked_index = local_mask[local_mask].index
    full_mask = pd.Series(False, index=edges_gdf.index)
    full_mask.loc[marked_index] = True

    ensure_columns(
        edges_gdf,
        {
            "is_covered": 0,
            "covered": "",
            "source_layer": "",
            "confidence": "",
        },
    )
    was_uncovered = edges_gdf["is_covered"].fillna(0).astype(float) <= 0
    edges_gdf.loc[full_mask, "is_covered"] = 1
    covered_blank = _blank_text_mask(edges_gdf["covered"])
    edges_gdf.loc[full_mask & covered_blank, "covered"] = "yes"
    source_blank = _blank_text_mask(edges_gdf["source_layer"])
    edges_gdf.loc[full_mask & source_blank, "source_layer"] = "inferred_hdb_point_footway"
    confidence_blank = _blank_text_mask(edges_gdf["confidence"])
    edges_gdf.loc[full_mask & confidence_blank, "confidence"] = (
        "inferred_existing_pedestrian_edge_point_proxy"
    )

    report["marked_edges"] = int(full_mask.sum())
    report["newly_marked_edges"] = int((full_mask & was_uncovered).sum())
    report["length_m"] = float(edges_gdf.loc[full_mask, "geometry"].length.sum())
    return full_mask, report


def value_counts(series: pd.Series) -> Counter[str]:
    return Counter(
        str(value).strip()
        for value in series.dropna()
        if str(value).strip() and str(value).strip().lower() not in {"nan", "none"}
    )


def format_top_counts(counts: Counter[str], limit: int = 3) -> str:
    return ", ".join(f"{key}={value}" for key, value in counts.most_common(limit))


def report_float(report: dict[str, object], key: str) -> float:
    value = report.get(key, 0.0)
    if isinstance(value, int | float):
        return float(value)
    return float(str(value))


def extract_longest_linestring(geom):
    if isinstance(geom, LineString):
        return geom
    elif isinstance(geom, MultiLineString):
        lines = list(geom.geoms)
        if not lines:
            return None
        return max(lines, key=lambda line: line.length)
    return None


def graph_nodes_from_edges(edges_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    nodes: list[tuple[float, float]] = []
    for geom in edges_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        coords = list(geom.coords)
        if not coords:
            continue
        nodes.append((round(coords[0][0], 2), round(coords[0][1], 2)))
        nodes.append((round(coords[-1][0], 2), round(coords[-1][1], 2)))
    node_df = pd.DataFrame({"node": nodes}).drop_duplicates("node")
    return gpd.GeoDataFrame(
        node_df,
        geometry=[Point(x, y) for x, y in node_df["node"]],
        crs="EPSG:3414",
    )


def six_digit_postcode(series: pd.Series) -> pd.Series:
    return series.astype("string").str.extract(r"(\d{6})", expand=False)


def correction_value_is_true(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return str(value).strip().lower() in COVERED_CORRECTION_VALUES


def load_audited_shelter_corrections(
    path: Path = AUDITED_SHELTER_CORRECTIONS_PATH,
) -> gpd.GeoDataFrame:
    """Load only source-backed, approved covered correction lines."""
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    if not path.is_file():
        return empty

    corrections = gpd.read_file(path)
    if corrections.empty:
        return empty
    if corrections.crs is None:
        corrections = corrections.set_crs(epsg=4326)
    corrections = corrections.to_crs(epsg=3414)

    status = corrections.get("status", pd.Series("", index=corrections.index))
    covered = corrections.get(
        "is_covered",
        corrections.get("covered", pd.Series(False, index=corrections.index)),
    )
    geom_type = corrections.geometry.geom_type
    approved_mask = status.astype(str).str.strip().str.lower().isin(APPROVED_CORRECTION_STATUSES)
    covered_mask = covered.map(correction_value_is_true)
    line_mask = geom_type.isin(["LineString", "MultiLineString"])
    return corrections.loc[approved_mask & covered_mask & line_mask].copy()


def build_audited_correction_edges(
    corrections_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    *,
    snap_max_m: float = 8.0,
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    """Convert approved correction lines into covered graph edges snapped to existing nodes."""
    empty_edges = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    approved_features = len(corrections_gdf)
    candidate_lines = 0
    added_edges = 0
    skipped_edges = 0

    def report() -> dict[str, object]:
        return {
            "approved_features": approved_features,
            "candidate_lines": candidate_lines,
            "added_edges": added_edges,
            "skipped_edges": skipped_edges,
            "snap_max_m": float(snap_max_m),
        }

    if corrections_gdf.empty or nodes_gdf.empty:
        return empty_edges, report()

    nodes_sindex = nodes_gdf.sindex
    correction_edges = []
    for idx, row in corrections_gdf.iterrows():
        geom = row.geometry
        lines = list(geom.geoms) if isinstance(geom, MultiLineString) else [geom]
        for line_index, line in enumerate(lines):
            candidate_lines += 1
            if not isinstance(line, LineString) or line.is_empty or line.length <= 0:
                skipped_edges += 1
                continue

            coords = list(line.coords)
            start_pt, end_pt = Point(coords[0]), Point(coords[-1])
            snapped_start, _ = nearest_point_on_geometry(
                nodes_gdf.geometry, nodes_sindex, start_pt, max_distance=snap_max_m
            )
            snapped_end, _ = nearest_point_on_geometry(
                nodes_gdf.geometry, nodes_sindex, end_pt, max_distance=snap_max_m
            )

            if snapped_start is None or snapped_end is None:
                skipped_edges += 1
                continue

            coords[0] = (snapped_start.x, snapped_start.y)
            coords[-1] = (snapped_end.x, snapped_end.y)
            snapped_line = LineString(coords)
            if snapped_line.length <= 0:
                skipped_edges += 1
                continue

            audit_id = row.get("audit_id", row.get("id", idx))
            if len(lines) > 1:
                audit_id = f"{audit_id}:{line_index}"
            correction_edges.append(
                {
                    "geometry": snapped_line,
                    "is_covered": 1,
                    "is_synthesized": 1,
                    "length_m": snapped_line.length,
                    "u": -1,
                    "v": -1,
                    "covered": "yes",
                    "highway": "audited_shelter_correction",
                    "synth_class": "AUDITED_SHELTER_CORRECTION",
                    "audit_id": audit_id,
                    "audit_source": row.get("source", row.get("evidence_url", "")),
                }
            )
            added_edges += 1

    if not correction_edges:
        return empty_edges, report()
    return gpd.GeoDataFrame(correction_edges, geometry="geometry", crs="EPSG:3414"), report()


def load_first_shapefile_from_zip(zip_path: Path) -> gpd.GeoDataFrame:
    tmp_dir = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp_dir)
    shp_files = list(tmp_dir.rglob("*.shp"))
    if not shp_files:
        raise FileNotFoundError(f"zip contains no shapefile: {zip_path}")
    return gpd.read_file(shp_files[0])


def load_overhead_bridge_underpass_polygons(
    pa_boundary: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    zip_path = find_raw_file("overhead_bridge_underpass.zip")
    if zip_path is None:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    bridge_gdf = load_first_shapefile_from_zip(zip_path).to_crs(epsg=3414)
    bridge_gdf = gpd.sjoin(
        bridge_gdf,
        pa_boundary[["PLN_AREA_N", "geometry"]],
        how="inner",
        predicate="intersects",
    )
    bridge_gdf["source_layer"] = "overhead_bridge_underpass"
    return bridge_gdf


def load_nparks_shade_proxy_geometries(
    union_poly,
) -> tuple[gpd.GeoDataFrame, dict[str, dict[str, object]]]:
    frames: list[gpd.GeoDataFrame] = []
    report: dict[str, dict[str, object]] = {}
    for source_key in sorted(NPARKS_SHADE_SOURCE_KEYS):
        path = find_raw_file(f"{source_key}.geojson")
        if path is None:
            report[source_key] = {
                "status": "missing",
                "features_raw": 0,
                "features_in_scope": 0,
                "proxy_polygons": 0,
            }
            continue
        try:
            features = gpd.read_file(path)
            if features.crs is None:
                features = features.set_crs(epsg=4326)
            features = features.to_crs(epsg=3414)
            in_scope = features[features.geometry.intersects(union_poly)].copy()
            proxy = prepare_shade_proxy_geometries(in_scope, source_key=source_key)
        except Exception as exc:  # noqa: BLE001 - bad upstream geometry should not fake shade.
            report[source_key] = {
                "status": "error",
                "error": str(exc),
                "features_raw": 0,
                "features_in_scope": 0,
                "proxy_polygons": 0,
            }
            continue
        report[source_key] = {
            "status": "loaded",
            "path": str(path.relative_to(PROJECT_ROOT)),
            "features_raw": int(len(features)),
            "features_in_scope": int(len(in_scope)),
            "proxy_polygons": int(len(proxy)),
        }
        if not proxy.empty:
            frames.append(proxy)

    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:3414"), report
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:3414"), report


def load_hdb_building_points(union_poly) -> gpd.GeoDataFrame:
    path = find_raw_file("building_points.geojson")
    if path is None:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    hdb_gdf = gpd.read_file(path).to_crs(epsg=3414)
    hdb_gdf = hdb_gdf[hdb_gdf.geometry.intersects(union_poly)].copy()
    if "POSTAL_COD" in hdb_gdf.columns:
        hdb_gdf["postal_code"] = six_digit_postcode(hdb_gdf["POSTAL_COD"])
    else:
        hdb_gdf["postal_code"] = pd.NA
    return hdb_gdf


def split_osm_building_shelter_layers(
    osm_buildings_gdf: gpd.GeoDataFrame | None,
    hdb_points_gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    if osm_buildings_gdf is None or osm_buildings_gdf.empty:
        return empty, empty

    buildings = osm_buildings_gdf.copy()
    if buildings.crs is None:
        buildings = buildings.set_crs(epsg=4326)
    buildings = buildings.to_crs(epsg=3414)
    building_tag = buildings.get("building", pd.Series("", index=buildings.index))
    building_tag = building_tag.astype(str).str.strip().str.lower()
    polygon_mask = buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])

    roof_gdf = buildings[polygon_mask & building_tag.isin(OSM_ROOF_SHELTER_TAGS)].copy()
    roof_gdf["source_layer"] = "osm_building_roof"

    if hdb_points_gdf.empty:
        return roof_gdf, empty

    hdb_postcodes = set(hdb_points_gdf["postal_code"].dropna().astype(str))
    postcode = (
        six_digit_postcode(buildings["addr:postcode"])
        if "addr:postcode" in buildings.columns
        else pd.Series(pd.NA, index=buildings.index)
    )
    residential_mask = building_tag.isin(HDB_VOID_DECK_BUILDING_TAGS)
    postcode_match = postcode.isin(hdb_postcodes)
    hdb_by_postcode = buildings[polygon_mask & residential_mask & postcode_match].copy()

    hdb_by_point = empty
    if not hdb_points_gdf.empty:
        joined = gpd.sjoin(
            buildings[polygon_mask & residential_mask],
            hdb_points_gdf[["postal_code", "geometry"]],
            how="inner",
            predicate="contains",
        )
        if not joined.empty:
            hdb_by_point = joined.drop(columns=["index_right"]).copy()

    hdb_footprints = pd.concat([hdb_by_postcode, hdb_by_point], ignore_index=False)
    if hdb_footprints.empty:
        return roof_gdf, empty
    hdb_footprints = hdb_footprints[~hdb_footprints.index.duplicated(keep="first")].copy()
    hdb_footprints["source_layer"] = "inferred_hdb_void_deck"
    hdb_footprints["postal_code"] = postcode.loc[hdb_footprints.index].values
    return roof_gdf, hdb_footprints


def build_hdb_void_deck_edges(
    hdb_footprints_gdf: gpd.GeoDataFrame,
    graph_nodes_gdf: gpd.GeoDataFrame,
    *,
    node_search_m: float = 8.0,
    min_line_m: float = 12.0,
    max_line_m: float = 95.0,
    min_inside_ratio: float = 0.65,
    max_edges_per_building: int = 2,
    max_candidate_nodes: int = 12,
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    empty_edges = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    report: dict[str, object] = {
        "candidate_buildings": len(hdb_footprints_gdf),
        "buildings_with_edges": 0,
        "added_edges": 0,
        "length_m": 0.0,
        "node_search_m": node_search_m,
        "min_inside_ratio": min_inside_ratio,
        "source": "hdb_points_plus_osm_residential_footprints",
        "confidence": "inferred",
    }
    if hdb_footprints_gdf.empty or graph_nodes_gdf.empty:
        return empty_edges, report

    node_sindex = graph_nodes_gdf.sindex
    void_edges = []
    buildings_with_edges = 0
    for idx, row in hdb_footprints_gdf.iterrows():
        footprint = row.geometry
        if footprint is None or footprint.is_empty or footprint.area < 200 or footprint.area > 6000:
            continue

        possible = node_sindex.query(footprint.buffer(node_search_m), predicate="intersects")
        if len(possible) < 2:
            continue
        candidates = graph_nodes_gdf.iloc[possible].copy()
        candidates["dist"] = candidates.geometry.distance(footprint)
        candidates = candidates[candidates["dist"] <= node_search_m].sort_values("dist")
        candidates = candidates.head(max_candidate_nodes)
        if len(candidates) < 2:
            continue

        pair_candidates = []
        candidate_rows = list(candidates.itertuples())
        for left_index, left in enumerate(candidate_rows):
            for right_index, right in enumerate(candidate_rows):
                if right_index <= left_index:
                    continue
                line = LineString([left.geometry, right.geometry])
                length_m = line.length
                if length_m < min_line_m or length_m > max_line_m:
                    continue
                inside_ratio = line.intersection(footprint.buffer(1.5)).length / length_m
                if inside_ratio >= min_inside_ratio:
                    pair_candidates.append((inside_ratio, length_m, left.node, right.node, line))

        used_nodes = set()
        building_edge_count = 0
        for inside_ratio, length_m, u_node, v_node, line in sorted(
            pair_candidates, key=lambda item: (-item[0], -item[1])
        ):
            if u_node in used_nodes or v_node in used_nodes:
                continue
            void_edges.append(
                {
                    "geometry": line,
                    "is_covered": 1,
                    "is_synthesized": 1,
                    "length_m": length_m,
                    "u": -1,
                    "v": -1,
                    "covered": "yes",
                    "highway": "inferred_hdb_void_deck",
                    "synth_class": "INFERRED_HDB_VOID_DECK",
                    "source_layer": "inferred_hdb_void_deck",
                    "confidence": "inferred",
                    "postal_code": row.get("postal_code", ""),
                    "osm_building_id": row.get("id", idx),
                    "inside_ratio": float(inside_ratio),
                }
            )
            used_nodes.update([u_node, v_node])
            building_edge_count += 1
            if building_edge_count >= max_edges_per_building:
                break

        if building_edge_count:
            buildings_with_edges += 1

    if not void_edges:
        report["buildings_with_edges"] = buildings_with_edges
        return empty_edges, report
    edges_gdf = gpd.GeoDataFrame(void_edges, geometry="geometry", crs="EPSG:3414")
    report["buildings_with_edges"] = buildings_with_edges
    report["added_edges"] = len(edges_gdf)
    report["length_m"] = float(edges_gdf.geometry.length.sum())
    return edges_gdf, report


def build_hdb_void_deck_anchor_edges(
    hdb_footprints_gdf: gpd.GeoDataFrame,
    graph_nodes_gdf: gpd.GeoDataFrame,
    *,
    node_search_m: float = 8.0,
    coverage_buffer_m: float = 3.0,
    min_line_m: float = 2.0,
    max_line_m: float = 45.0,
    min_inside_ratio: float = 0.60,
    max_edges_per_building: int = 2,
    max_candidate_nodes: int = 10,
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    empty_edges = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    report: dict[str, object] = {
        "candidate_buildings": len(hdb_footprints_gdf),
        "buildings_with_edges": 0,
        "added_edges": 0,
        "length_m": 0.0,
        "node_search_m": node_search_m,
        "coverage_buffer_m": coverage_buffer_m,
        "min_inside_ratio": min_inside_ratio,
        "source": "hdb_points_plus_residential_footprints",
        "confidence": "inferred",
    }
    if hdb_footprints_gdf.empty or graph_nodes_gdf.empty:
        return empty_edges, report

    node_sindex = graph_nodes_gdf.sindex
    anchor_edges = []
    buildings_with_edges = 0
    for idx, row in hdb_footprints_gdf.iterrows():
        footprint = row.geometry
        if footprint is None or footprint.is_empty or footprint.area < 200 or footprint.area > 6000:
            continue

        anchor = footprint.representative_point()
        sheltered_area = footprint.buffer(coverage_buffer_m)
        possible = node_sindex.query(
            sheltered_area.buffer(node_search_m),
            predicate="intersects",
        )
        if len(possible) == 0:
            continue

        candidates = graph_nodes_gdf.iloc[possible].copy()
        candidates["dist"] = candidates.geometry.distance(footprint)
        candidates = candidates[candidates["dist"] <= node_search_m].sort_values("dist")
        candidates = candidates.head(max_candidate_nodes)
        if candidates.empty:
            continue

        building_edge_count = 0
        for candidate in candidates.itertuples():
            line = LineString([anchor, candidate.geometry])
            length_m = float(line.length)
            if length_m < min_line_m or length_m > max_line_m:
                continue
            inside_ratio = line.intersection(sheltered_area).length / length_m
            if inside_ratio < min_inside_ratio:
                continue

            anchor_edges.append(
                {
                    "geometry": line,
                    "is_covered": 1,
                    "is_synthesized": 1,
                    "length_m": length_m,
                    "u": -1,
                    "v": -1,
                    "covered": "yes",
                    "highway": "inferred_hdb_void_deck_anchor",
                    "synth_class": "INFERRED_HDB_VOID_DECK_ANCHOR",
                    "source_layer": "inferred_hdb_void_deck",
                    "confidence": "inferred",
                    "postal_code": row.get("postal_code", ""),
                    "osm_building_id": row.get("id", idx),
                    "inside_ratio": float(inside_ratio),
                }
            )
            building_edge_count += 1
            if building_edge_count >= max_edges_per_building:
                break

        if building_edge_count:
            buildings_with_edges += 1

    if not anchor_edges:
        report["buildings_with_edges"] = buildings_with_edges
        return empty_edges, report

    edges_gdf = gpd.GeoDataFrame(anchor_edges, geometry="geometry", crs="EPSG:3414")
    report["buildings_with_edges"] = buildings_with_edges
    report["added_edges"] = len(edges_gdf)
    report["length_m"] = float(edges_gdf.geometry.length.sum())
    return edges_gdf, report


def build_hdb_precinct_connector_edges(
    hdb_footprints_gdf: gpd.GeoDataFrame,
    graph_nodes_gdf: gpd.GeoDataFrame,
    *,
    coverage_buffer_m: float = 20.0,
    max_pair_m: float = 55.0,
    min_line_m: float = 4.0,
    min_inside_ratio: float = 0.75,
    nearest_neighbours: int = 3,
    max_candidate_nodes: int = 80,
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    empty_edges = gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    report: dict[str, object] = {
        "candidate_buildings": len(hdb_footprints_gdf),
        "buildings_with_edges": 0,
        "added_edges": 0,
        "length_m": 0.0,
        "coverage_buffer_m": coverage_buffer_m,
        "max_pair_m": max_pair_m,
        "min_inside_ratio": min_inside_ratio,
        "nearest_neighbours": nearest_neighbours,
        "source": "hdb_points_plus_residential_footprints",
        "confidence": "inferred",
    }
    if hdb_footprints_gdf.empty or graph_nodes_gdf.empty:
        return empty_edges, report

    node_sindex = graph_nodes_gdf.sindex
    precinct_edges: dict[tuple[tuple[float, float], tuple[float, float]], dict[str, object]] = {}
    buildings_with_edges = 0
    for idx, row in hdb_footprints_gdf.iterrows():
        footprint = row.geometry
        if footprint is None or footprint.is_empty or footprint.area < 200 or footprint.area > 6000:
            continue

        sheltered_area = footprint.buffer(coverage_buffer_m)
        possible = node_sindex.query(sheltered_area, predicate="intersects")
        if len(possible) == 0:
            continue

        candidates = graph_nodes_gdf.iloc[possible].copy()
        candidates["dist"] = candidates.geometry.distance(footprint)
        candidates = candidates.sort_values("dist").head(max_candidate_nodes)

        local_nodes: list[tuple[tuple[float, float], Point]] = [
            (candidate.node, candidate.geometry) for candidate in candidates.itertuples()
        ]
        anchor = footprint.representative_point()
        local_nodes.append(((round(anchor.x, 2), round(anchor.y, 2)), anchor))
        if len(local_nodes) < 2:
            continue

        building_edge_count = 0
        for left_index, (_, left_point) in enumerate(local_nodes):
            neighbours = sorted(
                (
                    (
                        left_point.distance(right_point),
                        right_index,
                        right_node,
                        right_point,
                    )
                    for right_index, (right_node, right_point) in enumerate(local_nodes)
                    if right_index != left_index and left_point.distance(right_point) <= max_pair_m
                ),
                key=lambda item: (item[0], item[1]),
            )[:nearest_neighbours]

            for distance_m, right_index, right_node, right_point in neighbours:
                if right_index <= left_index:
                    continue
                if distance_m < min_line_m:
                    continue
                left_node = local_nodes[left_index][0]
                edge_key = (
                    (left_node, right_node) if left_node <= right_node else (right_node, left_node)
                )
                if edge_key in precinct_edges:
                    continue

                line = LineString([left_point, right_point])
                inside_ratio = line.intersection(sheltered_area).length / float(line.length)
                if inside_ratio < min_inside_ratio:
                    continue

                precinct_edges[edge_key] = {
                    "geometry": line,
                    "is_covered": 1,
                    "is_synthesized": 1,
                    "length_m": float(line.length),
                    "u": -1,
                    "v": -1,
                    "covered": "yes",
                    "highway": "inferred_hdb_precinct_connector",
                    "synth_class": "INFERRED_HDB_PRECINCT_CONNECTOR",
                    "source_layer": "inferred_hdb_precinct",
                    "confidence": "inferred",
                    "postal_code": row.get("postal_code", ""),
                    "osm_building_id": row.get("id", idx),
                    "inside_ratio": float(inside_ratio),
                }
                building_edge_count += 1

        if building_edge_count:
            buildings_with_edges += 1

    if not precinct_edges:
        report["buildings_with_edges"] = buildings_with_edges
        return empty_edges, report

    edges_gdf = gpd.GeoDataFrame(
        list(precinct_edges.values()),
        geometry="geometry",
        crs="EPSG:3414",
    )
    report["buildings_with_edges"] = buildings_with_edges
    report["added_edges"] = len(edges_gdf)
    report["length_m"] = float(edges_gdf.geometry.length.sum())
    return edges_gdf, report


def get_skeleton(poly):
    try:
        # Some very thin polygons cause Voronoi errors in centerline
        cl = Centerline(poly)
        return extract_longest_linestring(cl.geometry)
    except Exception:  # noqa: BLE001 - centerline can raise several geometry-library exceptions.
        # Fallback to straight segment between furthest points in polygon
        # This is a safe fallback for near-rectangular thin polygons that fail skeletonization
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return LineString([poly.centroid, poly.centroid])
        p1 = Point(coords[0])
        furthest = max([Point(c) for c in coords[1:]], key=lambda p: p1.distance(p))
        return LineString([p1, furthest])


def selected_planning_areas(
    pa_gdf: gpd.GeoDataFrame, scope: str
) -> tuple[gpd.GeoDataFrame, list[str]]:
    if scope == "pilot":
        selected = pa_gdf[
            pa_gdf["PLN_AREA_N"].str.upper().isin([pa.upper() for pa in PILOT_AREAS])
        ].copy()
        area_names = PILOT_AREAS
    elif scope == "island":
        selected = pa_gdf.copy()
        area_names = sorted(str(name) for name in selected["PLN_AREA_N"].dropna().unique())
    else:
        raise ValueError(f"unknown network scope: {scope}")

    return selected, area_names


def run_build(scope: str = "pilot"):
    if scope not in VALID_SCOPES:
        raise ValueError(f"unknown network scope: {scope}")

    QA_DIR.mkdir(exist_ok=True)
    print(f"Building pedestrian network scope: {scope}")
    dominant_component_count = DOMINANT_COMPONENTS_BY_SCOPE[scope]
    qa_path = QA_DIR / f"conflation_qa_{scope}.json"
    debug_path = QA_DIR / f"{scope}_debug.geojson"
    network_path = PROCESSED_DIR / (
        "network.parquet" if scope == "pilot" else "network_island.parquet"
    )

    # Load boundaries
    boundary_path = require_raw_file("planning_area_boundary.geojson")
    pa_gdf = gpd.read_file(boundary_path).to_crs(epsg=3414)
    pa_boundary, area_names = selected_planning_areas(pa_gdf, scope)
    union_poly = pa_boundary.geometry.union_all().buffer(500)
    print(
        f"Loaded {len(pa_boundary)} planning areas; dominant components={dominant_component_count}"
    )

    # Load LTA covered shelter polygons
    zip_path = require_raw_file("covered_linkway.zip")
    covered_linkway_gdf = load_first_shapefile_from_zip(zip_path).to_crs(epsg=3414)
    covered_linkway_gdf = gpd.sjoin(
        covered_linkway_gdf,
        pa_boundary[["PLN_AREA_N", "geometry"]],
        how="inner",
        predicate="intersects",
    )
    covered_linkway_gdf["source_layer"] = "covered_linkway"
    bridge_gdf = load_overhead_bridge_underpass_polygons(pa_boundary)
    lta_gdf = pd.concat([covered_linkway_gdf, bridge_gdf], ignore_index=True)
    print(
        "Loaded LTA shelter polygons in scope: "
        f"covered_linkways={len(lta_gdf[lta_gdf['source_layer'] == 'covered_linkway'])}, "
        f"overhead_underpass={len(bridge_gdf)}, total={len(lta_gdf)}"
    )

    # Load OSM
    osm_path = require_raw_file("*.osm.pbf")
    bbox_poly = gpd.GeoSeries([union_poly], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
    osm = OSM(str(osm_path), bounding_box=bbox_poly)

    nodes, edges = osm.get_network(
        nodes=True,
        network_type="walking",
        extra_attributes=["covered", "tunnel", "indoor", "layer", "level", "location"],
    )
    print(f"Loaded OSM walking network: nodes={len(nodes)}, edges={len(edges)}")
    try:
        osm_buildings = osm.get_buildings()
    except Exception as exc:  # noqa: BLE001 - pyrosm building extraction can fail on bad relations.
        print(f"Warning: failed to load OSM buildings for shelter inference: {exc}")
        osm_buildings = None

    edges_full_gdf = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)
    boundary_line = union_poly.boundary

    # Filter highway
    if "access" in edges.columns:
        if "foot" in edges.columns:
            mask = ~((edges["access"].isin(["private", "no"])) & (edges["foot"] != "yes"))
        else:
            mask = ~(edges["access"].isin(["private", "no"]))
        edges = edges[mask]

    if "highway" in edges.columns:
        exclude = ["motorway", "motorway_link", "trunk", "trunk_link", "construction"]
        # EXCLUDE ONLY foot-forbidden roads!
        if "foot" in edges.columns:
            edges = edges[~((edges["highway"].isin(exclude)) & (edges["foot"] != "yes"))]
        else:
            edges = edges[~edges["highway"].isin(exclude)]

    edges_gdf = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)
    graph_nodes_gdf = graph_nodes_from_edges(edges_gdf)
    print(f"Filtered OSM walking edges: {len(edges_gdf)}")
    print(f"Filtered OSM graph nodes from retained edges: {len(graph_nodes_gdf)}")
    dropped_edges_gdf = edges_full_gdf[~edges_full_gdf.index.isin(edges_gdf.index)].copy()
    print(
        f"Filtered access/foot-forbidden OSM edges retained for QA evidence: {len(dropped_edges_gdf)}"
    )

    hdb_points_gdf = load_hdb_building_points(union_poly)
    roof_gdf, hdb_footprints_gdf = split_osm_building_shelter_layers(
        (
            gpd.GeoDataFrame(osm_buildings, geometry="geometry", crs="EPSG:4326")
            if osm_buildings is not None and not osm_buildings.empty
            else None
        ),
        hdb_points_gdf,
    )
    print(
        "Loaded building shelter inference layers: "
        f"hdb_points={len(hdb_points_gdf)}, "
        f"osm_roof_canopy_polygons={len(roof_gdf)}, "
        f"hdb_void_deck_candidate_footprints={len(hdb_footprints_gdf)}"
    )

    bus_refs, mrt_refs = load_transit_reference_points()
    bus_sindex = bus_refs.sindex if not bus_refs.empty else None
    mrt_sindex = mrt_refs.sindex if not mrt_refs.empty else None
    print(
        f"Loaded transit reference points: bus_stops={len(bus_refs)}, mrt_lrt_exits={len(mrt_refs)}"
    )

    def component_area_name(comp_geom) -> str:
        centroid_gdf = gpd.GeoDataFrame(
            {"id": [0]},
            geometry=[comp_geom.centroid],
            crs="EPSG:3414",
        )
        joined = gpd.sjoin(
            centroid_gdf,
            pa_boundary[["PLN_AREA_N", "geometry"]],
            how="left",
            predicate="within",
        )
        area = joined.iloc[0].get("PLN_AREA_N")
        return str(area) if pd.notna(area) else "UNKNOWN"

    def component_edge_rows(comp, graph: nx.Graph, edges_df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        edge_indices = [data["idx"] for _, _, data in graph.edges(nbunch=list(comp), data=True)]
        if not edge_indices:
            return edges_df.iloc[0:0]
        return edges_df.loc[edge_indices]

    def classify_residual_component(
        comp,
        comp_geom,
        edges_df: gpd.GeoDataFrame,
        graph: nx.Graph,
        gap_dist: float,
        centroid_4326: Point,
    ) -> tuple[str, str]:
        area_name = component_area_name(comp_geom)
        sub_edges = component_edge_rows(comp, graph, edges_df)
        edge_count = max(len(sub_edges), 1)
        highway_counts = value_counts(sub_edges.get("highway", pd.Series(dtype=object)))
        access_counts = value_counts(sub_edges.get("access", pd.Series(dtype=object)))
        foot_counts = value_counts(sub_edges.get("foot", pd.Series(dtype=object)))
        service_counts = value_counts(sub_edges.get("service", pd.Series(dtype=object)))
        name_counts = value_counts(sub_edges.get("name", pd.Series(dtype=object)))
        name_text = " ".join(name_counts).lower()

        # Boundary distance
        dist_to_boundary = comp_geom.distance(boundary_line)
        if dist_to_boundary < 20.0:
            return "CLIP_EDGE", f"dist_to_boundary={dist_to_boundary:.2f}m (<20m)"

        # Filtered-out access-controlled edges touching the component.
        touching_dropped = []
        if "access" in dropped_edges_gdf.columns and not dropped_edges_gdf.empty:
            xmin, ymin, xmax, ymax = comp_geom.bounds
            possible_matches = dropped_edges_gdf.cx[xmin:xmax, ymin:ymax]
            for _, row_d in possible_matches.iterrows():
                if (
                    row_d.geometry
                    and not row_d.geometry.is_empty
                    and comp_geom.distance(row_d.geometry) < 1.0
                ):
                    touching_dropped.append(row_d)

        private_ways = [r for r in touching_dropped if r.get("access") in ["private", "no"]]
        if len(private_ways) >= 2:
            ways_info = []
            for r in private_ways[:2]:
                h = r.get("highway", "NA")
                a = r.get("access", "NA")
                ways_info.append(f"highway={h}/access={a}")
            return "PRIVATE_ESTATE", f"enclosed by: {', '.join(ways_info)}"

        private_access_values = {"private", "no", "customers", "permit"}
        private_access = [key for key in access_counts if key.lower() in private_access_values]
        private_foot = [key for key in foot_counts if key.lower() in {"private", "no"}]
        if private_access or private_foot:
            evidence_parts = []
            if private_access:
                evidence_parts.append(f"access tags: {format_top_counts(access_counts)}")
            if private_foot:
                evidence_parts.append(f"foot tags: {format_top_counts(foot_counts)}")
            evidence_parts.append(f"area={area_name}")
            return "PRIVATE_ESTATE", "; ".join(evidence_parts)

        service_ratio = highway_counts.get("service", 0) / edge_count
        internal_service_count = sum(
            service_counts.get(key, 0) for key in ["driveway", "parking_aisle"]
        )
        if service_ratio >= 0.75 and (gap_dist > 30.0 or internal_service_count >= 10):
            evidence = (
                f"service-road-only/internal component; area={area_name}; "
                f"highway tags: {format_top_counts(highway_counts)}"
            )
            if service_counts:
                evidence += f"; service tags: {format_top_counts(service_counts)}"
            return "PRIVATE_ESTATE", evidence

        if area_name == "CHANGI" and (
            "airport" in name_text
            or any(token in name_text for token in ["t3 ", "t4 ", "terminal"])
            or highway_counts.get("corridor", 0)
            or highway_counts.get("elevator", 0)
            or highway_counts.get("pedestrian", 0)
        ):
            evidence = (
                "Changi airport terminal/access-controlled component; "
                f"highway tags: {format_top_counts(highway_counts)}"
            )
            if name_counts:
                evidence += f"; names: {format_top_counts(name_counts)}"
            return "PRIVATE_ESTATE", evidence

        if area_name == "WESTERN WATER CATCHMENT" and (
            service_ratio >= 0.5 or highway_counts.get("unclassified", 0)
        ):
            return (
                "PRIVATE_ESTATE",
                (
                    f"Western Water Catchment service/military-access component; "
                    f"highway tags: {format_top_counts(highway_counts)}"
                ),
            )

        owner_overrides = {
            (1.32963, 103.81428): (
                "PRIVATE_ESTATE",
                "Owner-confirmed private residential road",
            ),
            (1.34287, 103.86311): (
                "PRIVATE_ESTATE",
                "Owner-confirmed private residential road",
            ),
            (1.33567, 103.81491): (
                "ISOLATED_NON_TRANSIT",
                "Owner-confirmed forested area without transit relevance",
            ),
            (1.34347, 103.87766): (
                "ISOLATED_NON_TRANSIT",
                "Owner-confirmed self-contained park loop",
            ),
        }
        for (oy, ox), (oclass, oevid) in owner_overrides.items():
            if abs(centroid_4326.y - oy) < 0.001 and abs(centroid_4326.x - ox) < 0.001:
                return oclass, oevid

        bus_dist = float("inf")
        mrt_dist = float("inf")
        if bus_sindex is not None:
            bus_dist, _ = nearest_distance_and_index(bus_refs, bus_sindex, comp_geom)
        if mrt_sindex is not None:
            mrt_dist, _ = nearest_distance_and_index(mrt_refs, mrt_sindex, comp_geom)

        if (
            math.isfinite(bus_dist)
            and math.isfinite(mrt_dist)
            and bus_dist > BUS_NON_TRANSIT_THRESHOLD_M
            and mrt_dist > MRT_NON_TRANSIT_THRESHOLD_M
        ):
            return (
                "ISOLATED_NON_TRANSIT",
                (
                    f"nearest_bus_stop={bus_dist:.1f}m "
                    f"(>{BUS_NON_TRANSIT_THRESHOLD_M:.0f}m), "
                    f"nearest_mrt_lrt_exit={mrt_dist:.1f}m "
                    f"(>{MRT_NON_TRANSIT_THRESHOLD_M:.0f}m)"
                ),
            )

        path_track_ratio = (
            highway_counts.get("path", 0)
            + highway_counts.get("track", 0)
            + highway_counts.get("footway", 0)
        ) / edge_count
        if any(token in name_text for token in PARK_NAME_TOKENS):
            return (
                "ISOLATED_NON_TRANSIT",
                (
                    f"named park/attraction path component in {area_name}; "
                    f"names: {format_top_counts(name_counts)}"
                ),
            )
        if area_name in PARK_NON_TRANSIT_AREAS and path_track_ratio >= 0.70 and gap_dist > 50.0:
            return (
                "ISOLATED_NON_TRANSIT",
                (
                    f"park/track component in {area_name}; "
                    f"highway tags: {format_top_counts(highway_counts)}"
                ),
            )
        if math.isfinite(bus_dist) and bus_dist > BUS_NON_TRANSIT_THRESHOLD_M and gap_dist > 100.0:
            return (
                "ISOLATED_NON_TRANSIT",
                (
                    "standalone component with no bus stop inside D3 range; "
                    f"nearest_bus_stop={bus_dist:.1f}m, "
                    f"gap_to_main={gap_dist:.1f}m, area={area_name}"
                ),
            )

        return "REAL_DISCONNECTION", ""

    edges_gdf["is_covered"] = 0
    native_covered_mask = native_osm_covered_mask(edges_gdf)
    edges_gdf.loc[native_covered_mask, "is_covered"] = 1

    roof_match_ratio = compute_polygon_match_ratio(
        edges_gdf,
        roof_gdf,
        buffer_m=3.0,
        label="OSM roof/canopy",
    )
    roof_match_mask = roof_match_ratio >= 0.50
    roof_match_edge_length = float(edges_gdf.loc[roof_match_mask, "geometry"].length.sum())
    edges_gdf.loc[roof_match_mask, "is_covered"] = 1
    print(
        f"OSM roof/canopy covered attribution: edges={int(roof_match_mask.sum())}, "
        f"length={roof_match_edge_length:.1f}m"
    )

    _hdb_footway_mask, hdb_footway_report = apply_hdb_precinct_footway_coverage(
        edges_gdf,
        hdb_footprints_gdf,
    )
    hdb_footway_length_m = report_float(hdb_footway_report, "length_m")
    print(
        "Inferred HDB precinct pedestrian-edge coverage: "
        f"eligible_edges={hdb_footway_report['eligible_edge_count']}, "
        f"marked={hdb_footway_report['marked_edges']}, "
        f"length={hdb_footway_length_m:.1f}m"
    )
    _hdb_point_footway_mask, hdb_point_footway_report = apply_hdb_point_footway_coverage(
        edges_gdf,
        hdb_points_gdf,
    )
    hdb_point_footway_length_m = report_float(hdb_point_footway_report, "length_m")
    print(
        "Inferred HDB point pedestrian-edge coverage: "
        f"eligible_edges={hdb_point_footway_report['eligible_edge_count']}, "
        f"marked={hdb_point_footway_report['marked_edges']}, "
        f"new={hdb_point_footway_report['newly_marked_edges']}, "
        f"length={hdb_point_footway_length_m:.1f}m"
    )

    hdb_void_edges_gdf, hdb_void_report = build_hdb_void_deck_edges(
        hdb_footprints_gdf,
        graph_nodes_gdf,
    )
    hdb_anchor_edges_gdf, hdb_anchor_report = build_hdb_void_deck_anchor_edges(
        hdb_footprints_gdf,
        graph_nodes_gdf,
    )
    hdb_precinct_edges_gdf, hdb_precinct_report = build_hdb_precinct_connector_edges(
        hdb_footprints_gdf,
        graph_nodes_gdf,
    )
    hdb_void_length_m = (
        float(hdb_void_edges_gdf.geometry.length.sum()) if not hdb_void_edges_gdf.empty else 0.0
    )
    hdb_anchor_length_m = (
        float(hdb_anchor_edges_gdf.geometry.length.sum()) if not hdb_anchor_edges_gdf.empty else 0.0
    )
    hdb_precinct_length_m = (
        float(hdb_precinct_edges_gdf.geometry.length.sum())
        if not hdb_precinct_edges_gdf.empty
        else 0.0
    )
    print(
        "Inferred HDB void-deck connectors: "
        f"candidate_buildings={hdb_void_report['candidate_buildings']}, "
        f"buildings_with_edges={hdb_void_report['buildings_with_edges']}, "
        f"added={hdb_void_report['added_edges']}, "
        f"length={hdb_void_length_m:.1f}m"
    )
    print(
        "Inferred HDB void-deck anchors: "
        f"candidate_buildings={hdb_anchor_report['candidate_buildings']}, "
        f"buildings_with_edges={hdb_anchor_report['buildings_with_edges']}, "
        f"added={hdb_anchor_report['added_edges']}, "
        f"length={hdb_anchor_length_m:.1f}m"
    )
    print(
        "Inferred HDB precinct connectors: "
        f"candidate_buildings={hdb_precinct_report['candidate_buildings']}, "
        f"buildings_with_edges={hdb_precinct_report['buildings_with_edges']}, "
        f"added={hdb_precinct_report['added_edges']}, "
        f"length={hdb_precinct_length_m:.1f}m"
    )
    if not hdb_void_edges_gdf.empty:
        edges_gdf = pd.concat([edges_gdf, hdb_void_edges_gdf], ignore_index=True)
        native_covered_mask = pd.concat(
            [native_covered_mask, pd.Series([False] * len(hdb_void_edges_gdf))],
            ignore_index=True,
        )
    if not hdb_anchor_edges_gdf.empty:
        edges_gdf = pd.concat([edges_gdf, hdb_anchor_edges_gdf], ignore_index=True)
        native_covered_mask = pd.concat(
            [native_covered_mask, pd.Series([False] * len(hdb_anchor_edges_gdf))],
            ignore_index=True,
        )
    if not hdb_precinct_edges_gdf.empty:
        edges_gdf = pd.concat([edges_gdf, hdb_precinct_edges_gdf], ignore_index=True)
        native_covered_mask = pd.concat(
            [native_covered_mask, pd.Series([False] * len(hdb_precinct_edges_gdf))],
            ignore_index=True,
        )

    correction_edges_gdf, correction_report = build_audited_correction_edges(
        load_audited_shelter_corrections(),
        graph_nodes_gdf,
    )
    correction_report["path"] = str(AUDITED_SHELTER_CORRECTIONS_PATH.relative_to(PROJECT_ROOT))
    correction_length_m = (
        float(correction_edges_gdf.geometry.length.sum()) if not correction_edges_gdf.empty else 0.0
    )
    print(
        "Audited shelter corrections: "
        f"approved={correction_report['approved_features']}, "
        f"added={correction_report['added_edges']}, "
        f"skipped={correction_report['skipped_edges']}, "
        f"length={correction_length_m:.1f}m"
    )
    if not correction_edges_gdf.empty:
        edges_gdf = pd.concat([edges_gdf, correction_edges_gdf], ignore_index=True)
        native_covered_mask = pd.concat(
            [native_covered_mask, pd.Series([False] * len(correction_edges_gdf))],
            ignore_index=True,
        )

    # SYNTHESIZE REAL OSM GAPS
    G_pre = nx.Graph()
    for idx, row in edges_gdf.iterrows():
        geom = row.geometry
        if geom and not geom.is_empty:
            c = geom.coords
            u = (round(c[0][0], 2), round(c[0][1], 2))
            v = (round(c[-1][0], 2), round(c[-1][1], 2))
            G_pre.add_edge(u, v, idx=idx)

    components_pre = sorted(nx.connected_components(G_pre), key=len, reverse=True)
    if len(components_pre) > dominant_component_count:
        main_comps = components_pre[:dominant_component_count]
        main_geom = gpd.GeoSeries([Point(n) for comp in main_comps for n in comp]).union_all()
        synthetic_edges = []
        bridge_threshold = 5.0 if scope == "pilot" else ISLAND_OWNER_APPROVED_BRIDGE_THRESHOLD_M
        print("\n--- SYNTHESIZING OSM GAPS ---")
        for comp in components_pre[dominant_component_count:]:
            if len(comp) > 50:
                comp_geom = gpd.GeoSeries([Point(n) for n in comp]).union_all()
                p_comp, p_main = nearest_points(comp_geom, main_geom)
                dist = p_comp.distance(p_main)
                p_comp_4326 = gpd.GeoSeries([p_comp], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
                centroid_4326 = (
                    gpd.GeoSeries([comp_geom.centroid], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
                )
                comp_class, evidence = classify_residual_component(
                    comp,
                    comp_geom,
                    edges_gdf,
                    G_pre,
                    dist,
                    centroid_4326,
                )

                if comp_class == "REAL_DISCONNECTION" and dist <= bridge_threshold:
                    bridge_reason = (
                        "auto"
                        if dist < 5.0
                        else f"owner-approved island residual bridge <= {bridge_threshold:.0f}m"
                    )
                    print(
                        f"Auto-bridging size {len(comp)} component at ({p_comp_4326.y:.5f}, {p_comp_4326.x:.5f}) with gap {dist:.2f}m ({bridge_reason})"
                    )
                    synthetic_edges.append(
                        {
                            "geometry": LineString([p_comp, p_main]),
                            "highway": "synthetic_osm_gap",
                            "is_covered": 0,
                            "covered": "no",
                        }
                    )
                elif comp_class != "REAL_DISCONNECTION":
                    print(
                        f"Skipping bridge for size {len(comp)} component at ({p_comp_4326.y:.5f}, {p_comp_4326.x:.5f}) classified {comp_class}: {evidence}"
                    )
                elif dist <= 15.0:
                    print(
                        f"NEEDS_MANUAL_REVIEW: REAL_DISCONNECTION candidate size {len(comp)} at ({p_comp_4326.y:.5f}, {p_comp_4326.x:.5f}) has gap {dist:.2f}m. Skipping auto-bridge."
                    )
        print("-----------------------------\n")
        if synthetic_edges:
            edges_gdf = pd.concat(
                [
                    edges_gdf,
                    gpd.GeoDataFrame(synthetic_edges, geometry="geometry", crs="EPSG:3414"),
                ],
                ignore_index=True,
            )
            native_covered_mask = pd.concat(
                [native_covered_mask, pd.Series([False] * len(synthetic_edges))], ignore_index=True
            )

    # 3 GATE
    def get_components(edges_df):
        G = nx.Graph()
        for idx, row in edges_df.iterrows():
            geom = row.geometry
            if geom and not geom.is_empty:
                c = geom.coords
                u = (round(c[0][0], 2), round(c[0][1], 2))
                v = (round(c[-1][0], 2), round(c[-1][1], 2))
                G.add_edge(u, v, idx=idx)

        components = sorted(nx.connected_components(G), key=len, reverse=True)
        sizes = [len(c) for c in components]

        main_comps = components[:dominant_component_count]
        main_geom = None
        if main_comps:
            main_geom = gpd.GeoSeries([Point(n) for comp in main_comps for n in comp]).union_all()

        residual_info = []
        if len(components) > dominant_component_count:
            for comp in components[dominant_component_count:]:
                if len(comp) > 50:
                    comp_geom = gpd.GeoSeries([Point(n) for n in comp]).union_all()

                    # Gap distance
                    gap_dist = float("inf")
                    if main_geom:
                        p_comp, p_main = nearest_points(comp_geom, main_geom)
                        gap_dist = p_comp.distance(p_main)

                    # Centroid
                    centroid_4326 = (
                        gpd.GeoSeries([comp_geom.centroid], crs="EPSG:3414")
                        .to_crs(epsg=4326)
                        .iloc[0]
                    )

                    c_class, evidence = classify_residual_component(
                        comp,
                        comp_geom,
                        edges_df,
                        G,
                        gap_dist,
                        centroid_4326,
                    )

                    residual_info.append(
                        {
                            "size": len(comp),
                            "coords": (centroid_4326.y, centroid_4326.x),
                            "gap": gap_dist,
                            "class": c_class,
                            "evidence": evidence,
                        }
                    )

        return sizes, G.number_of_nodes(), residual_info

    sizes_initial, total_nodes_initial, residuals_initial = get_components(edges_gdf)

    print("\n============================================================")
    print("3 GATE: OSM-only Graph Structure")
    print("============================================================")
    print(f"Nodes: {total_nodes_initial}, Edges: {len(edges_gdf)}")
    top_3_share_initial = (
        sum(sizes_initial[:3]) / total_nodes_initial if total_nodes_initial > 0 else 0
    )
    print(f"Top 3 component node share: {top_3_share_initial*100:.2f}%")
    print(f"Top 5 component sizes: {sizes_initial[:5]}")

    print("\nResidual components > 50 nodes:")
    if residuals_initial:
        for r in residuals_initial:
            print(
                f"  Size {r['size']} at ({r['coords'][0]:.5f}, {r['coords'][1]:.5f}) | Gap: {r['gap']:.2f}m | {r['class']} | {r['evidence']}"
            )
    else:
        print("  None")

    real_disconnections = [r for r in residuals_initial if r["class"] == "REAL_DISCONNECTION"]
    print(f"\nREAL_DISCONNECTION components: {len(real_disconnections)}")
    if len(real_disconnections) == 0:
        print("GATE 3 PASS: Zero REAL_DISCONNECTION components.")
    else:
        print("GATE 3 FAIL: Unexplained REAL_DISCONNECTION components remain.")

    print(f"\nMean edge length: {edges_gdf.geometry.length.mean():.2f}m")

    # Also 60% intersection logic for attribution (used for downstream scoring, NOT classification)
    covered_linkway_match_mask, _ = apply_polygon_coverage_attribution(
        edges_gdf,
        covered_linkway_gdf,
        source_layer="covered_linkway",
        ratio_threshold=0.60,
        buffer_m=3.0,
        label="LTA covered-linkway",
    )
    bridge_match_mask, bridge_match_edge_length = apply_polygon_coverage_attribution(
        edges_gdf,
        bridge_gdf,
        source_layer="overhead_bridge_underpass",
        ratio_threshold=0.45,
        buffer_m=4.0,
        label="LTA overhead bridge/underpass",
        overwrite_sources={"covered_linkway"},
    )
    lta_match_mask = covered_linkway_match_mask | bridge_match_mask
    lta_match_edge_length = edges_gdf.loc[lta_match_mask, "geometry"].length.sum()
    print(
        "LTA polygon covered attribution: "
        f"covered_linkway_edges={int(covered_linkway_match_mask.sum())}, "
        f"bridge_underpass_edges={int(bridge_match_mask.sum())}, "
        f"union_length={float(lta_match_edge_length):.1f}m"
    )
    native_covered_edge_length = edges_gdf.loc[native_covered_mask, "geometry"].length.sum()

    # Classification uses NATIVE covered osm
    native_covered_osm = edges_gdf[native_covered_mask].copy()
    native_covered_sindex = native_covered_osm.sindex if not native_covered_osm.empty else None

    # Check underground tags for all edges
    ug_mask = pd.Series(False, index=edges_gdf.index)
    if "layer" in edges_gdf.columns:
        ug_mask |= pd.to_numeric(edges_gdf["layer"], errors="coerce") < 0
    if "level" in edges_gdf.columns:
        ug_mask |= edges_gdf["level"].str.startswith("-", na=False)
    if "tunnel" in edges_gdf.columns:
        ug_mask |= edges_gdf["tunnel"].isin(["yes"])
    if "indoor" in edges_gdf.columns:
        ug_mask |= edges_gdf["indoor"].isin(["yes"])

    ug_osm = edges_gdf[ug_mask].copy()
    ug_sindex = ug_osm.sindex if not ug_osm.empty else None

    # Classification
    lta_gdf["class"] = "UNKNOWN"
    lta_gdf["dist_to_covered"] = 999.0
    lta_gdf["perimeter_div_2"] = lta_gdf.geometry.length / 2.0

    print(f"Classifying {len(lta_gdf)} LTA linkway polygons by nearest represented shelter...")
    for pos, (idx, row) in enumerate(lta_gdf.iterrows(), start=1):
        if pos % 1000 == 0:
            print(f"  LTA classification progress: {pos}/{len(lta_gdf)}")

        if native_covered_sindex is None:
            dist = 999.0
        else:
            dist, _ = nearest_distance_and_index(
                native_covered_osm,
                native_covered_sindex,
                row.geometry,
                max_distance=10.0,
            )
            if not math.isfinite(dist):
                dist = 999.0
        lta_gdf.at[idx, "dist_to_covered"] = dist

        if dist <= 3.0:
            lta_gdf.at[idx, "class"] = "ALIGNED"
        elif dist <= 10.0:
            lta_gdf.at[idx, "class"] = "OFFSET"
        else:
            # Unrepresented -> Check underground
            if ug_sindex is not None:
                ug_dist, nearest_ug_idx = nearest_distance_and_index(
                    ug_osm,
                    ug_sindex,
                    row.geometry,
                    max_distance=10.0,
                )
                if ug_dist <= 10.0:
                    lta_gdf.at[idx, "class"] = "UNDERGROUND_OR_INDOOR"
                    if nearest_ug_idx is not None:
                        edges_gdf.at[nearest_ug_idx, "is_covered"] = 1
                else:
                    lta_gdf.at[idx, "class"] = "UNREPRESENTED-surface"
            else:
                lta_gdf.at[idx, "class"] = "UNREPRESENTED-surface"

    # Synthesize edges
    synth_edges = []
    synth_edge_split_points: dict[object, list[Point]] = {}
    unsnapped_count = 0
    needs_manual_count = 0
    # Snap synthesized shelter endpoints only to nodes that exist in the current
    # routable graph. Raw OSM nodes can sit mid-edge after filtering; those must
    # use the edge-snap path so the host edge is split into a real route node.
    snap_nodes_gdf = graph_nodes_from_edges(edges_gdf)
    nodes_sindex = snap_nodes_gdf.sindex
    edges_sindex = edges_gdf.sindex

    synth_candidates = lta_gdf[lta_gdf["class"].isin(["OFFSET", "UNREPRESENTED-surface"])]
    print(f"Synthesizing/snapping {len(synth_candidates)} eligible linkway polygons...")
    for pos, (idx, row) in enumerate(synth_candidates.iterrows(), start=1):
        if pos % 250 == 0:
            print(f"  Synthesis progress: {pos}/{len(synth_candidates)}")

        if row["class"] not in ["OFFSET", "UNREPRESENTED-surface"]:
            continue

        line = get_skeleton(row.geometry)
        if not isinstance(line, LineString) or line.is_empty:
            lta_gdf.at[idx, "class"] = "NEEDS_MANUAL"
            needs_manual_count += 1
            unsnapped_count += 1
            continue

        coords = list(line.coords)
        start_pt, end_pt = Point(coords[0]), Point(coords[-1])
        polygon_source_layer = str(row.get("source_layer", "")).strip()
        is_bridge_underpass = polygon_source_layer == "overhead_bridge_underpass"

        if row["class"] == "OFFSET":
            cap = min(11.0, row["dist_to_covered"] + 1.0)

            # If nodes are too far, try to snap to nearest edge and project
            p_nearest_s, d_s_node = nearest_point_on_geometry(
                snap_nodes_gdf.geometry, nodes_sindex, start_pt, max_distance=cap
            )
            p_nearest_e, d_e_node = nearest_point_on_geometry(
                snap_nodes_gdf.geometry, nodes_sindex, end_pt, max_distance=cap
            )
            p_edge_s, d_s_edge, edge_idx_s = nearest_point_and_index_on_geometry(
                edges_gdf.geometry, edges_sindex, start_pt, max_distance=cap
            )
            p_edge_e, d_e_edge, edge_idx_e = nearest_point_and_index_on_geometry(
                edges_gdf.geometry, edges_sindex, end_pt, max_distance=cap
            )

            snapped_s = False
            snapped_e = False

            if p_nearest_s is not None and d_s_node <= cap:
                coords[0] = (p_nearest_s.x, p_nearest_s.y)
                snapped_s = True
            elif p_edge_s is not None and d_s_edge <= cap:
                coords[0] = (p_edge_s.x, p_edge_s.y)
                if edge_idx_s is not None:
                    synth_edge_split_points.setdefault(edge_idx_s, []).append(p_edge_s)
                snapped_s = True

            if p_nearest_e is not None and d_e_node <= cap:
                coords[-1] = (p_nearest_e.x, p_nearest_e.y)
                snapped_e = True
            elif p_edge_e is not None and d_e_edge <= cap:
                coords[-1] = (p_edge_e.x, p_edge_e.y)
                if edge_idx_e is not None:
                    synth_edge_split_points.setdefault(edge_idx_e, []).append(p_edge_e)
                snapped_e = True

            if not snapped_s or not snapped_e:
                lta_gdf.at[idx, "class"] = "NEEDS_MANUAL"
                unsnapped_count += 1
                needs_manual_count += 1
            else:
                snapped = LineString(coords)
                synth_edges.append(
                    {
                        "geometry": snapped,
                        "is_covered": 1,
                        "is_synthesized": 1,
                        "length_m": snapped.length,
                        "u": -1,
                        "v": -1,
                        "synth_class": "OFFSET",
                        "source_layer": polygon_source_layer,
                        "covered": "yes",
                        "confidence": "source_polygon_endpoint_snap",
                    }
                )

        elif row["class"] == "UNREPRESENTED-surface":
            # Wider endpoint snap only for LTA overhead bridge/underpass polygons.
            node_snap_m = 4.0 if is_bridge_underpass else 2.0
            edge_snap_m = 8.0 if is_bridge_underpass else 5.0
            p_nearest_s, d_s_node = nearest_point_on_geometry(
                snap_nodes_gdf.geometry, nodes_sindex, start_pt, max_distance=node_snap_m
            )
            p_nearest_e, d_e_node = nearest_point_on_geometry(
                snap_nodes_gdf.geometry, nodes_sindex, end_pt, max_distance=node_snap_m
            )
            p_edge_s, d_s_edge, edge_idx_s = nearest_point_and_index_on_geometry(
                edges_gdf.geometry, edges_sindex, start_pt, max_distance=edge_snap_m
            )
            p_edge_e, d_e_edge, edge_idx_e = nearest_point_and_index_on_geometry(
                edges_gdf.geometry, edges_sindex, end_pt, max_distance=edge_snap_m
            )

            snapped_s = False
            snapped_e = False

            if p_nearest_s is not None and d_s_node <= node_snap_m:
                coords[0] = (p_nearest_s.x, p_nearest_s.y)
                snapped_s = True
            elif p_edge_s is not None and d_s_edge <= edge_snap_m:
                coords[0] = (p_edge_s.x, p_edge_s.y)
                if edge_idx_s is not None:
                    synth_edge_split_points.setdefault(edge_idx_s, []).append(p_edge_s)
                snapped_s = True

            if p_nearest_e is not None and d_e_node <= node_snap_m:
                coords[-1] = (p_nearest_e.x, p_nearest_e.y)
                snapped_e = True
            elif p_edge_e is not None and d_e_edge <= edge_snap_m:
                coords[-1] = (p_edge_e.x, p_edge_e.y)
                if edge_idx_e is not None:
                    synth_edge_split_points.setdefault(edge_idx_e, []).append(p_edge_e)
                snapped_e = True

            if not snapped_s or not snapped_e:
                unsnapped_count += 1
                needs_manual_count += 1
                lta_gdf.at[idx, "class"] = "NEEDS_MANUAL"
            else:
                snapped = LineString(coords)
                synth_edges.append(
                    {
                        "geometry": snapped,
                        "is_covered": 1,
                        "is_synthesized": 1,
                        "length_m": snapped.length,
                        "u": -1,
                        "v": -1,
                        "synth_class": "UNREPRESENTED-surface",
                        "source_layer": polygon_source_layer,
                        "covered": "yes",
                        "confidence": "source_polygon_endpoint_snap",
                    }
                )

    if synth_edges:
        split_count_before = len(edges_gdf)
        edges_gdf = split_edges_at_points(edges_gdf, synth_edge_split_points)
        split_count_delta = len(edges_gdf) - split_count_before
        print(
            "Split host edges for synthesized shelter snaps: "
            f"host_edges={len(synth_edge_split_points)}, added_segments_delta={split_count_delta}"
        )
        synth_gdf = gpd.GeoDataFrame(synth_edges, crs="EPSG:3414")
        edges_gdf = pd.concat([edges_gdf, synth_gdf], ignore_index=True)

    shade_proxy_gdf, shade_report = load_nparks_shade_proxy_geometries(union_poly)
    edges_gdf["shade_ratio"] = 0.0
    edges_gdf["shade_source"] = ""
    edges_gdf["shade_confidence"] = ""
    shade_edge_count = 0
    shade_weighted_length_m = 0.0
    if not shade_proxy_gdf.empty:
        shade_ratios = compute_edge_shade_ratio(edges_gdf[["geometry"]].copy(), shade_proxy_gdf)
        shade_ratios = shade_ratios.fillna(0.0).clip(lower=0.0, upper=1.0)
        shade_mask = shade_ratios > 0
        edges_gdf.loc[:, "shade_ratio"] = shade_ratios.round(3)
        edges_gdf.loc[shade_mask, "shade_source"] = "nparks_greenery_proxy"
        edges_gdf.loc[shade_mask, "shade_confidence"] = "proxy_heat_only"
        shade_edge_count = int(shade_mask.sum())
        shade_weighted_length_m = float((edges_gdf.geometry.length * shade_ratios).sum())
    print(
        "NParks shade proxy attribution: "
        f"sources_loaded={sum(1 for item in shade_report.values() if item['status'] == 'loaded')}, "
        f"proxy_polygons={len(shade_proxy_gdf)}, "
        f"shaded_edges={shade_edge_count}, "
        f"weighted_length={shade_weighted_length_m:.1f}m"
    )

    sizes, total_nodes, residuals = get_components(edges_gdf)
    top_3_share = sum(sizes[:3]) / total_nodes if total_nodes > 0 else 0
    final_real_disconnections = [r for r in residuals if r["class"] == "REAL_DISCONNECTION"]

    print("\n============================================================")
    print("4 GATE: Classification Counts")
    print("============================================================")
    per_area_classification = {}
    per_area_match_pct = {}
    for area in area_names:
        a_gdf = lta_gdf[lta_gdf["PLN_AREA_N"].str.upper() == area.upper()]
        counts = a_gdf["class"].value_counts()
        count_dict = {
            "ALIGNED": int(counts.get("ALIGNED", 0)),
            "OFFSET": int(counts.get("OFFSET", 0)),
            "UNREPRESENTED-surface": int(counts.get("UNREPRESENTED-surface", 0)),
            "UNDERGROUND_OR_INDOOR": int(counts.get("UNDERGROUND_OR_INDOOR", 0)),
            "NEEDS_MANUAL": int(counts.get("NEEDS_MANUAL", 0)),
        }
        per_area_classification[area] = count_dict
        area_total_len = float(a_gdf["perimeter_div_2"].sum())
        area_aligned_len = float(a_gdf.loc[a_gdf["class"] == "ALIGNED", "perimeter_div_2"].sum())
        per_area_match_pct[area] = (
            area_aligned_len / area_total_len * 100.0 if area_total_len else 0.0
        )
        print(f"[{area}]")
        print(f"  ALIGNED: {count_dict['ALIGNED']}")
        print(f"  OFFSET: {count_dict['OFFSET']}")
        print(f"  UNREPRESENTED-surface: {count_dict['UNREPRESENTED-surface']}")
        print(f"  UNDERGROUND_OR_INDOOR: {count_dict['UNDERGROUND_OR_INDOOR']}")
        print(f"  NEEDS_MANUAL: {count_dict['NEEDS_MANUAL']}")

        # Explanation logic
        ug_count = counts.get("UNDERGROUND_OR_INDOOR", 0)
        unrep = counts.get("UNREPRESENTED-surface", 0) + ug_count
        print(
            f"  Explanation: {unrep} UNREPRESENTED of which {ug_count} are UNDERGROUND_OR_INDOOR."
        )

    synth_len = sum(e["length_m"] for e in synth_edges)
    total_len = lta_gdf["perimeter_div_2"].sum()

    print("\n============================================================")
    print("5 GATE: Synthesis metrics")
    print("============================================================")
    print(
        f"Total synthesized-surface length: {synth_len:.2f}m ({synth_len/total_len*100:.1f}% of total)"
    )
    print(f"Unsnapped / NEEDS_MANUAL counts: {needs_manual_count}")
    print(f"Top 3 component node share: {top_3_share*100:.2f}%")
    print(f"Top 5 component sizes: {sizes[:5]}")

    # 6: QA Report
    covered_union_length = edges_gdf.loc[edges_gdf["is_covered"] == 1, "geometry"].length.sum()
    flags = []
    if real_disconnections:
        flags.append("osm_only_real_disconnections_present")
    if final_real_disconnections:
        flags.append("final_real_disconnections_present")
    if synth_len / total_len * 100 > 15.0:
        flags.append("synthesized_surface_length_above_15_pct")

    def serialize_residuals(residual_list):
        return [
            {
                "size": int(item["size"]),
                "lat": float(item["coords"][0]),
                "lon": float(item["coords"][1]),
                "gap_m": float(item["gap"]),
                "class": item["class"],
                "evidence": item["evidence"],
            }
            for item in residual_list
        ]

    qa_report = {
        "nodes": total_nodes,
        "edges": len(edges_gdf),
        "mean_edge_length_m": edges_gdf.geometry.length.mean(),
        "connected_components_count": len(sizes),
        "top_5_component_sizes": sizes[:5],
        "osm_only_top_3_component_node_share_pct": top_3_share_initial * 100,
        "top_3_component_node_share_pct": top_3_share * 100,
        "residual_components_gt_50_osm_only": serialize_residuals(residuals_initial),
        "residual_components_gt_50_final": serialize_residuals(residuals),
        "real_disconnection_count_osm_only": len(real_disconnections),
        "real_disconnection_count_final": len(final_real_disconnections),
        "per_area_classification_counts": per_area_classification,
        "per_area_match_pct": per_area_match_pct,
        "linkway_total_length_m": total_len,
        "synthesized_length_m": synth_len,
        "synthesized_pct_of_total": synth_len / total_len * 100 if total_len else 0,
        "unsnapped_endpoints_count": unsnapped_count,
        "needs_manual_count": needs_manual_count,
        "covered_edge_length_m_osm_tags": float(native_covered_edge_length),
        "covered_edge_length_m_lta_match": float(lta_match_edge_length),
        "covered_edge_length_m_lta_bridge_underpass_match": bridge_match_edge_length,
        "covered_edge_length_m_osm_roof_canopy": roof_match_edge_length,
        "covered_edge_length_m_inferred_hdb_precinct_footways": hdb_footway_length_m,
        "covered_edge_length_m_inferred_hdb_point_footways": hdb_point_footway_length_m,
        "covered_edge_length_m_inferred_hdb_void_deck": hdb_void_length_m,
        "covered_edge_length_m_inferred_hdb_void_deck_anchors": hdb_anchor_length_m,
        "covered_edge_length_m_inferred_hdb_precinct_connectors": hdb_precinct_length_m,
        "covered_edge_length_m_audited_corrections": correction_length_m,
        "covered_edge_length_m_union": float(covered_union_length),
        "shade_proxy_edge_count": shade_edge_count,
        "shade_proxy_weighted_length_m": shade_weighted_length_m,
        "shade_proxy_sources": shade_report,
        "inferred_hdb_void_deck": hdb_void_report,
        "inferred_hdb_void_deck_anchors": hdb_anchor_report,
        "inferred_hdb_precinct_connectors": hdb_precinct_report,
        "inferred_hdb_precinct_footways": hdb_footway_report,
        "inferred_hdb_point_footways": hdb_point_footway_report,
        "audited_shelter_corrections": correction_report,
        "flags": flags,
    }
    with open(qa_path, "w") as f:
        json.dump(qa_report, f, indent=2)

    debug_export = lta_gdf[["geometry", "class"]].copy().to_crs(epsg=4326)
    if synth_edges:
        se = gpd.GeoDataFrame(synth_edges, crs="EPSG:3414").to_crs(epsg=4326)
        se["class"] = "SYNTHESIZED: " + se["synth_class"]
        debug_export = pd.concat([debug_export, se[["geometry", "class"]]], ignore_index=True)
    if not correction_edges_gdf.empty:
        ce = correction_edges_gdf.to_crs(epsg=4326)
        ce["class"] = "AUDITED_SHELTER_CORRECTION"
        debug_export = pd.concat([debug_export, ce[["geometry", "class"]]], ignore_index=True)
    if not hdb_void_edges_gdf.empty:
        he = hdb_void_edges_gdf.to_crs(epsg=4326)
        he["class"] = "INFERRED_HDB_VOID_DECK"
        debug_export = pd.concat([debug_export, he[["geometry", "class"]]], ignore_index=True)
    if not hdb_anchor_edges_gdf.empty:
        hae = hdb_anchor_edges_gdf.to_crs(epsg=4326)
        hae["class"] = "INFERRED_HDB_VOID_DECK_ANCHOR"
        debug_export = pd.concat([debug_export, hae[["geometry", "class"]]], ignore_index=True)
    if not hdb_precinct_edges_gdf.empty:
        hpe = hdb_precinct_edges_gdf.to_crs(epsg=4326)
        hpe["class"] = "INFERRED_HDB_PRECINCT_CONNECTOR"
        debug_export = pd.concat([debug_export, hpe[["geometry", "class"]]], ignore_index=True)
    if "source_layer" in edges_gdf.columns:
        debug_source_layer = edges_gdf["source_layer"].astype(str)
    else:
        debug_source_layer = pd.Series("", index=edges_gdf.index, dtype="string")
    hdb_footway_debug_mask = debug_source_layer.eq("inferred_hdb_precinct_footway")
    hdb_point_footway_debug_mask = debug_source_layer.eq("inferred_hdb_point_footway")
    if bool(hdb_footway_debug_mask.any()):
        hfw = edges_gdf.loc[hdb_footway_debug_mask, ["geometry"]].copy().to_crs(epsg=4326)
        hfw["class"] = "INFERRED_HDB_PRECINCT_FOOTWAY"
        debug_export = pd.concat([debug_export, hfw[["geometry", "class"]]], ignore_index=True)
    if bool(hdb_point_footway_debug_mask.any()):
        hpf = edges_gdf.loc[hdb_point_footway_debug_mask, ["geometry"]].copy().to_crs(epsg=4326)
        hpf["class"] = "INFERRED_HDB_POINT_FOOTWAY"
        debug_export = pd.concat([debug_export, hpf[["geometry", "class"]]], ignore_index=True)
    if "shade_ratio" in edges_gdf.columns:
        shade_debug_mask = pd.to_numeric(edges_gdf["shade_ratio"], errors="coerce").fillna(0) > 0
        if bool(shade_debug_mask.any()):
            shade_debug = (
                edges_gdf.loc[shade_debug_mask, ["geometry", "shade_ratio"]]
                .copy()
                .to_crs(epsg=4326)
            )
            shade_debug["class"] = "NPARKS_SHADE_PROXY"
            debug_export = pd.concat(
                [debug_export, shade_debug[["geometry", "class"]]], ignore_index=True
            )
    if not roof_gdf.empty:
        re = roof_gdf[["geometry", "source_layer"]].copy().to_crs(epsg=4326)
        re["class"] = "OSM_ROOF_CANOPY"
        debug_export = pd.concat([debug_export, re[["geometry", "class"]]], ignore_index=True)
    debug_export.to_file(debug_path, driver="GeoJSON")

    # Save network for routing!
    edges_export = pd.DataFrame(edges_gdf.copy())
    if "geometry" in edges_export.columns:
        edges_export["geometry"] = edges_export["geometry"].apply(lambda x: x.wkt if x else None)
    edges_export.to_parquet(network_path)


if __name__ == "__main__":
    run_build()
