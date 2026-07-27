"""Pilot pedestrian network conflation module for S.H.I.O.K. Index (T1.1/T1.2)."""

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import warnings

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyrosm import OSM
from shapely.errors import ShapelyDeprecationWarning
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)

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

    clip_gdf_3414 = gpd.GeoDataFrame(geometry=[union_buffered_3414], crs="EPSG:3414")
    clip_gdf_4326 = clip_gdf_3414.to_crs(epsg=4326)
    union_buffered_4326 = clip_gdf_4326.geometry.iloc[0]

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

    if "access" in edges.columns:
        if "foot" in edges.columns:
            mask = ~((edges["access"].isin(["private", "no"])) & (edges["foot"] != "yes"))
        else:
            mask = ~(edges["access"].isin(["private", "no"]))
        edges = edges[mask]

    if "highway" in edges.columns:
        edges = edges[edges["highway"] != "construction"]

    edges = edges.to_crs(epsg=3414)
    nodes = nodes.to_crs(epsg=3414)

    # D orders Buffer Hard Cap
    BUFFER_M = 8.0

    # LTA Covered Linkways
    linkways_clipped = gpd.clip(linkways_gdf, union_poly_3414)
    linkways_clipped = gpd.sjoin(
        linkways_clipped,
        boundaries_gdf[["PLN_AREA_N", "geometry"]],
        how="inner",
        predicate="intersects",
    )
    linkways_clipped["perimeter_div_2"] = linkways_clipped.geometry.length / 2.0
    linkway_total_length_m = linkways_clipped["perimeter_div_2"].sum()

    # Identify covered OSM edges
    edges["is_covered"] = 0
    if "covered" in edges.columns:
        edges.loc[edges["covered"] == "yes", "is_covered"] = 1
    if "tunnel" in edges.columns:
        edges.loc[edges["tunnel"].isin(["yes", "building_passage"]), "is_covered"] = 1
    if "indoor" in edges.columns:
        edges.loc[edges["indoor"] == "yes", "is_covered"] = 1

    covered_osm = edges[edges["is_covered"] == 1]
    if not covered_osm.empty:
        osm_buffer = covered_osm.geometry.union_all().buffer(BUFFER_M)
    else:
        osm_buffer = gpd.GeoSeries([])

    # Matcher redefined area rule
    linkways_clipped["intersection_area"] = linkways_clipped.geometry.intersection(osm_buffer).area
    linkways_clipped["matched"] = (
        linkways_clipped["intersection_area"] / linkways_clipped.geometry.area
    ) >= 0.5

    matched_linkways = linkways_clipped[linkways_clipped["matched"]]
    unmatched_linkways = linkways_clipped[~linkways_clipped["matched"]]

    # Enrich OSM coverage via overlapping buffered LTA
    lta_buffer = linkways_clipped.geometry.union_all().buffer(BUFFER_M)
    lta_matches = edges.geometry.intersection(lta_buffer)
    match_ratio = lta_matches.length / edges.geometry.length
    edges.loc[match_ratio >= 0.60, "is_covered"] = 1

    synth_edges_len = 0.0
    synth_islands = 0
    synth_edges = []

    nodes_geom = nodes.geometry.union_all()

    if not unmatched_linkways.empty:
        unmatched_centerlines = unmatched_linkways.copy()
        unmatched_centerlines.geometry = unmatched_centerlines.geometry.apply(approx_centerline)
        synth_edges_len = unmatched_centerlines.geometry.length.sum()
        synth_islands = len(unmatched_centerlines)  # D.3 metric approximation

        # Snapping logic: Endpoints 2m to nodes
        for idx, row in unmatched_centerlines.iterrows():
            line = row.geometry
            if not isinstance(line, LineString) or line.is_empty:
                continue

            # Snap endpoints to graph
            coords = list(line.coords)
            start_pt, end_pt = Point(coords[0]), Point(coords[-1])

            p_nearest_start, _ = nearest_points(nodes_geom, start_pt)
            p_nearest_end, _ = nearest_points(nodes_geom, end_pt)

            if start_pt.distance(p_nearest_start) <= 2.0:
                coords[0] = (p_nearest_start.x, p_nearest_start.y)
            if end_pt.distance(p_nearest_end) <= 2.0:
                coords[-1] = (p_nearest_end.x, p_nearest_end.y)

            snapped_line = LineString(coords)
            synth_edges.append(
                {
                    "geometry": snapped_line,
                    "is_covered": 1,
                    "is_synthesized": 1,
                    "length": snapped_line.length,
                    "u": -1,
                    "v": -1,
                }
            )

    if synth_edges:
        synth_gdf = gpd.GeoDataFrame(synth_edges, crs="EPSG:3414")
        edges = pd.concat([edges, synth_gdf], ignore_index=True)

    edges["length_m"] = edges.geometry.length
    mean_edge_length = edges["length_m"].mean()

    per_area_match_pct = {}
    for pa in PILOT_AREAS:
        pa_geom = boundaries_gdf[boundaries_gdf["PLN_AREA_N"].str.upper() == pa.upper()]
        if pa_geom.empty:
            continue

        pa_linkways = linkways_clipped[linkways_clipped["PLN_AREA_N"].str.upper() == pa.upper()]
        pa_total_len = pa_linkways["perimeter_div_2"].sum()

        if pa_total_len > 0:
            pa_matched_len = pa_linkways[pa_linkways["matched"]]["perimeter_div_2"].sum()
            per_area_match_pct[pa] = round(float(pa_matched_len / pa_total_len * 100.0), 2)
        else:
            per_area_match_pct[pa] = 100.0

    # Component metrics (D.4)
    G = nx.Graph()
    if "u" in edges.columns and "v" in edges.columns:
        valid_edges = edges[edges["u"].notna() & edges["v"].notna()]
        for idx, row in valid_edges.iterrows():
            G.add_edge(row["u"], row["v"], length=row["length_m"])

    components = list(nx.connected_components(G))
    cc_count = len(components)
    components_sorted = sorted(components, key=len, reverse=True)
    top_5_sizes = [len(c) for c in components_sorted[:5]]

    giant_edge_share = 0.0
    if components_sorted:
        giant = G.subgraph(components_sorted[0])
        total_e = G.number_of_edges()
        if total_e > 0:
            giant_edge_share = giant.number_of_edges() / total_e

    qa_report: dict[str, Any] = {
        "area": area,
        "pilot_areas": PILOT_AREAS,
        "nodes_count": len(nodes),
        "edges_count": len(edges),
        "mean_edge_length_m": round(float(mean_edge_length), 2),
        "connected_components_count": cc_count,
        "giant_component_edge_share_pct": round(giant_edge_share * 100.0, 2),
        "top_5_component_sizes": top_5_sizes,
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

    # Output generation
    osm_covered_only = edges[(edges["is_covered"] == 1) & (match_ratio < 0.60)]
    if not osm_covered_only.empty:
        osm_covered_only.to_crs(epsg=4326).to_file(
            QA_DIR / "osm_covered_only.geojson", driver="GeoJSON"
        )

    lta_only = unmatched_linkways
    if not lta_only.empty:
        lta_only.to_crs(epsg=4326).to_file(QA_DIR / "lta_only.geojson", driver="GeoJSON")

    debug_features = []
    if not matched_linkways.empty:
        matched_lta_export = matched_linkways.copy()
        matched_lta_export["debug_type"] = "LTA Matched"
        matched_lta_export["is_synthesized"] = False
        debug_features.append(
            matched_lta_export[["geometry", "debug_type", "matched", "is_synthesized"]]
        )

    if not unmatched_linkways.empty:
        unmatched_lta_export = unmatched_linkways.copy()
        unmatched_lta_export["debug_type"] = "LTA Unmatched"
        unmatched_lta_export["matched"] = False
        unmatched_lta_export["is_synthesized"] = False
        debug_features.append(
            unmatched_lta_export[["geometry", "debug_type", "matched", "is_synthesized"]]
        )

    if synth_edges:
        synth_export = gpd.GeoDataFrame(synth_edges, crs="EPSG:3414")
        synth_export["debug_type"] = "Synthesized Edge"
        synth_export["matched"] = False
        synth_export["is_synthesized"] = True
        debug_features.append(synth_export[["geometry", "debug_type", "matched", "is_synthesized"]])

    if debug_features:
        debug_gdf = pd.concat(debug_features, ignore_index=True)
        debug_gdf = gpd.GeoDataFrame(debug_gdf, crs="EPSG:3414").to_crs(epsg=4326)
        debug_gdf.to_file(QA_DIR / "pilot_debug.geojson", driver="GeoJSON")

    # Export network to parquet for T1.2
    # Ensure edges has necessary columns for routing (u, v, length_m, is_covered)
    edges_export = edges[["u", "v", "length_m", "is_covered", "geometry"]].copy()
    edges_export.to_parquet(QA_DIR.parent / "network.parquet")

    qa_report_path = QA_DIR / f"conflation_qa_{area}.json"
    with open(qa_report_path, "w", encoding="utf-8") as f:
        json.dump(qa_report, f, indent=2)

    print(f"Conflation QA report saved to {qa_report_path}")
    print("\n================ Conflation QA Headline Metrics ================")
    print(json.dumps(qa_report, indent=2))

    return qa_report


if __name__ == "__main__":
    build_pilot_network()
