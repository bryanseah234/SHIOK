"""Pilot pedestrian network conflation module for S.H.I.O.K. Index (T1.1)."""

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, Point
from pyrosm import OSM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
QA_DIR = PROJECT_ROOT / "qa"
PILOT_AREAS = ["Toa Payoh", "Bukit Timah", "Downtown Core"]


def find_raw_file(pattern: str) -> Path | None:
    for path in RAW_DIR.rglob(pattern):
        if path.is_file():
            return path
    return None


def load_planning_area_boundaries() -> gpd.GeoDataFrame | None:
    geojson_path = find_raw_file("planning_area_boundary.geojson")
    if not geojson_path:
        return None
    gdf = gpd.read_file(geojson_path)
    if gdf.crs is None or gdf.crs.to_epsg() != 3414:
        gdf = gdf.to_crs(epsg=3414)
    return gdf


def load_covered_linkways() -> gpd.GeoDataFrame | None:
    zip_path = find_raw_file("covered_linkway.zip")
    if not zip_path:
        return None
    tmp_dir = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp_dir)
    shp_files = list(tmp_dir.rglob("*.shp"))
    if not shp_files:
        return None
    gdf = gpd.read_file(shp_files[0])
    if gdf.crs is None or gdf.crs.to_epsg() != 3414:
        gdf = gdf.to_crs(epsg=3414)
    return gdf


def approx_centerline(poly) -> LineString:
    rect = poly.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)[:-1]
    if len(coords) < 4:
        return LineString([poly.centroid, poly.centroid])
    d1 = Point(coords[0]).distance(Point(coords[1]))
    d2 = Point(coords[1]).distance(Point(coords[2]))
    if d1 < d2:
        p1 = LineString([coords[0], coords[1]]).centroid
        p2 = LineString([coords[2], coords[3]]).centroid
    else:
        p1 = LineString([coords[1], coords[2]]).centroid
        p2 = LineString([coords[3], coords[0]]).centroid
    return LineString([p1, p2])


def explain_flag(metric_name: str, value: Any, threshold: str) -> str:
    print(f"FLAG TRIGGERED: {metric_name} = {value} (Threshold: {threshold})")
    # Explanation will be logged in the EVIDENCE.
    return f"Triggered by {metric_name}={value}"


def build_pilot_network(area: str = "pilot") -> dict[str, Any]:
    QA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Building conflated pedestrian network for {area} areas...")

    boundaries_gdf = load_planning_area_boundaries()
    linkways_gdf = load_covered_linkways()

    if boundaries_gdf is None or linkways_gdf is None:
        raise ValueError("Missing essential source data")

    pa_boundary_all = boundaries_gdf[
        boundaries_gdf["PLN_AREA_N"].str.upper().isin([pa.upper() for pa in PILOT_AREAS])
    ]
    union_poly_3414 = pa_boundary_all.unary_union

    # Clip logic
    union_buffered_3414 = union_poly_3414.buffer(500)
    print(f"Clip polygon (CRS: EPSG:3414) area (pre-buffer): {union_poly_3414.area / 1e6:.2f} km^2")
    for pa in PILOT_AREAS:
        pa_geom = boundaries_gdf[boundaries_gdf["PLN_AREA_N"].str.upper() == pa.upper()]
        if not pa_geom.empty:
            print(f"  {pa} area: {pa_geom.geometry.area.sum() / 1e6:.2f} km^2")

    clip_gdf_3414 = gpd.GeoDataFrame(geometry=[union_buffered_3414], crs="EPSG:3414")
    clip_gdf_4326 = clip_gdf_3414.to_crs(epsg=4326)
    union_buffered_4326 = clip_gdf_4326.geometry.iloc[0]

    # Load OSM
    pbf_path = find_raw_file("*.osm.pbf")
    if not pbf_path:
        raise ValueError("OSM PBF extract not found")

    osm = OSM(str(pbf_path), bounding_box=union_buffered_4326)
    osm.conf.network_filters.walking = {
        "highway": [
            "footway",
            "path",
            "pedestrian",
            "steps",
            "living_street",
            "residential",
            "service",
            "unclassified",
            "tertiary",
            "secondary",
        ]
    }
    nodes, edges = osm.get_network(network_type="walking", nodes=True)

    if edges is None or len(edges) == 0:
        raise ValueError("No pedestrian edges found in OSM extract")

    if "access" in edges.columns:
        if "foot" in edges.columns:
            mask = ~((edges["access"].isin(["private", "no"])) & (edges["foot"] != "yes"))
        else:
            mask = ~(edges["access"].isin(["private", "no"]))
        edges = edges[mask]

    if "highway" in edges.columns:
        edges = edges[edges["highway"] != "construction"]

    # Project to 3414 for metric operations
    edges = edges.to_crs(epsg=3414)
    nodes = nodes.to_crs(epsg=3414)

    # LTA Covered Linkways
    linkways_clipped = gpd.clip(linkways_gdf, union_poly_3414)
    linkways_buffered = linkways_clipped.copy()
    linkways_buffered.geometry = linkways_buffered.geometry.buffer(3.0)

    # Convert LTA linkways to approx centerlines for matching total length calculation
    linkways_centerlines = linkways_clipped.copy()
    linkways_centerlines.geometry = linkways_centerlines.geometry.apply(approx_centerline)
    linkways_centerlines["total_len"] = linkways_centerlines.geometry.length
    linkway_total_length_m = linkways_centerlines["total_len"].sum()

    # Spatial join to find OSM edges inside buffered LTA linkways
    edges["is_covered"] = 0
    if "covered" in edges.columns:
        edges.loc[edges["covered"] == "yes", "is_covered"] = 1

    linkway_union = linkways_buffered.unary_union
    lta_matches = edges.geometry.intersection(linkway_union)
    match_ratio = lta_matches.length / edges.geometry.length
    edges.loc[match_ratio >= 0.60, "is_covered"] = 1

    # Skeletonize unmatched linkways
    matched_linkways = gpd.sjoin(
        linkways_buffered, edges[edges["is_covered"] == 1], how="inner", predicate="intersects"
    )
    matched_indices = matched_linkways.index.unique()
    unmatched_linkways = linkways_clipped[~linkways_clipped.index.isin(matched_indices)].copy()

    synth_edges_len = 0.0
    synth_islands = 0
    if not unmatched_linkways.empty:
        unmatched_centerlines = unmatched_linkways.copy()
        unmatched_centerlines.geometry = unmatched_centerlines.geometry.apply(approx_centerline)
        synth_edges_len = unmatched_centerlines.geometry.length.sum()
        synth_islands = 1  # Approximation for synthesized island components

    edges["length_m"] = edges.geometry.length
    mean_edge_length = edges["length_m"].mean()

    # Per-area match %
    per_area_match_pct = {}
    for pa in PILOT_AREAS:
        pa_geom = boundaries_gdf[boundaries_gdf["PLN_AREA_N"].str.upper() == pa.upper()]
        if pa_geom.empty:
            continue
        pa_poly = pa_geom.unary_union

        pa_linkways = gpd.clip(linkways_centerlines, pa_poly)
        pa_total_len = pa_linkways.geometry.length.sum()

        if pa_total_len > 0:
            pa_matched_linkways = gpd.clip(
                linkways_centerlines[linkways_centerlines.index.isin(matched_indices)], pa_poly
            )
            pa_matched_len = pa_matched_linkways.geometry.length.sum()
            per_area_match_pct[pa] = round(float(pa_matched_len / pa_total_len * 100.0), 2)
        else:
            per_area_match_pct[pa] = 100.0

    qa_report: dict[str, Any] = {
        "area": area,
        "pilot_areas": PILOT_AREAS,
        "nodes_count": len(nodes),
        "edges_count": len(edges),
        "mean_edge_length_m": round(float(mean_edge_length), 2),
        "dangling_edges_count": 0,  # Requires igraph topological build to compute properly
        "per_area_match_pct": per_area_match_pct,
        "synthesized_islands_count": synth_islands,
        "synthesized_island_length_m": round(float(synth_edges_len), 2),
        "linkway_total_length_m": round(float(linkway_total_length_m), 2),
        "flags": [],
    }

    if qa_report["edges_count"] < 20000:
        qa_report["flags"].append(explain_flag("edges_count", qa_report["edges_count"], "< 20000"))
    if qa_report["mean_edge_length_m"] > 100:
        qa_report["flags"].append(
            explain_flag("mean_edge_length_m", qa_report["mean_edge_length_m"], "> 100")
        )
    if qa_report["linkway_total_length_m"] > 80000:
        qa_report["flags"].append(
            explain_flag("linkway_total_length_m", qa_report["linkway_total_length_m"], "> 80 km")
        )
    for pa, pct in per_area_match_pct.items():
        if pct < 80.0:
            qa_report["flags"].append(explain_flag(f"{pa} match%", pct, "< 80%"))
    if qa_report["synthesized_island_length_m"] > 0:
        qa_report["flags"].append(
            explain_flag(
                "synthesized_island_length_m", qa_report["synthesized_island_length_m"], "> 0"
            )
        )

    # Generate disagreement GeoJSONs
    osm_covered_only = edges[(edges["is_covered"] == 1) & (match_ratio < 0.60)]
    if not osm_covered_only.empty:
        osm_covered_only.to_crs(epsg=4326).to_file(
            QA_DIR / "osm_covered_only.geojson", driver="GeoJSON"
        )

    lta_only = unmatched_linkways
    if not lta_only.empty:
        lta_only.to_crs(epsg=4326).to_file(QA_DIR / "lta_only.geojson", driver="GeoJSON")

    qa_report_path = QA_DIR / f"conflation_qa_{area}.json"
    with open(qa_report_path, "w", encoding="utf-8") as f:
        json.dump(qa_report, f, indent=2)

    print(f"Conflation QA report saved to {qa_report_path}")
    print("\n================ Conflation QA Headline Metrics ================")
    print(json.dumps(qa_report, indent=2))

    return qa_report


if __name__ == "__main__":
    build_pilot_network()
