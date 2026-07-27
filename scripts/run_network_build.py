import geopandas as gpd
import pandas as pd
import networkx as nx
from shapely.geometry import LineString, Point, MultiLineString
from shapely.ops import nearest_points
from pyrosm import OSM
from pathlib import Path
import tempfile
import zipfile
import json
import warnings
from shapely.errors import ShapelyDeprecationWarning

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)
from centerline.geometry import Centerline

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
QA_DIR = PROJECT_ROOT / "qa"
PILOT_AREAS = ["Toa Payoh", "Bukit Timah", "Downtown Core"]


def find_raw_file(pattern: str) -> Path | None:
    for path in RAW_DIR.rglob(pattern):
        if path.is_file():
            return path
    return None


def extract_longest_linestring(geom):
    if isinstance(geom, LineString):
        return geom
    elif isinstance(geom, MultiLineString):
        lines = list(geom.geoms)
        if not lines:
            return None
        return max(lines, key=lambda l: l.length)
    return None


def get_skeleton(poly):
    try:
        # Some very thin polygons cause Voronoi errors in centerline
        cl = Centerline(poly)
        return extract_longest_linestring(cl.geometry)
    except Exception:
        # Fallback to straight segment between furthest points in polygon
        # This is a safe fallback for near-rectangular thin polygons that fail skeletonization
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return LineString([poly.centroid, poly.centroid])
        p1 = Point(coords[0])
        furthest = max([Point(c) for c in coords[1:]], key=lambda p: p1.distance(p))
        return LineString([p1, furthest])


def run_build():
    QA_DIR.mkdir(exist_ok=True)

    # Load boundaries
    boundary_path = find_raw_file("planning_area_boundary.geojson")
    pa_gdf = gpd.read_file(boundary_path).to_crs(epsg=3414)
    pa_boundary = pa_gdf[
        pa_gdf["PLN_AREA_N"].str.upper().isin([pa.upper() for pa in PILOT_AREAS])
    ].copy()
    union_poly = pa_boundary.geometry.union_all().buffer(500)

    # Load LTA linkways
    zip_path = find_raw_file("covered_linkway.zip")
    tmp_dir = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp_dir)
    shp_files = list(tmp_dir.rglob("*.shp"))
    lta_gdf = gpd.read_file(shp_files[0]).to_crs(epsg=3414)
    lta_gdf = gpd.sjoin(
        lta_gdf, pa_boundary[["PLN_AREA_N", "geometry"]], how="inner", predicate="intersects"
    )

    # Load OSM
    osm_path = find_raw_file("*.osm.pbf")
    bbox_poly = gpd.GeoSeries([union_poly], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
    osm = OSM(str(osm_path), bounding_box=bbox_poly)

    nodes, edges = osm.get_network(
        nodes=True,
        network_type="walking",
        extra_attributes=["covered", "tunnel", "indoor", "layer", "level"],
    )

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
    nodes_gdf = gpd.GeoDataFrame(nodes, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)

    edges_gdf["is_covered"] = 0
    native_covered_mask = pd.Series(False, index=edges_gdf.index)
    if "covered" in edges_gdf.columns:
        native_covered_mask |= edges_gdf["covered"].isin(["yes", "covered", "arcade", "colonnade"])
    if "highway" in edges_gdf.columns:
        native_covered_mask |= edges_gdf["highway"].str.contains("covered", na=False)
    if "tunnel" in edges_gdf.columns:
        native_covered_mask |= edges_gdf["tunnel"].isin(["yes", "building_passage"])
    if "indoor" in edges_gdf.columns:
        native_covered_mask |= edges_gdf["indoor"].isin(["yes"])

    edges_gdf.loc[native_covered_mask, "is_covered"] = 1

    # 3 GATE
    def get_components(edges_df):
        G = nx.Graph()
        for idx, row in edges_df.iterrows():
            geom = row.geometry
            if geom and not geom.is_empty:
                c = geom.coords
                u = (round(c[0][0], 2), round(c[0][1], 2))
                v = (round(c[-1][0], 2), round(c[-1][1], 2))
                G.add_edge(u, v)
        components = list(nx.connected_components(G))
        sizes = [len(c) for c in sorted(components, key=len, reverse=True)]

        # Save residual endpoints
        residual_info = []
        if len(components) > 3:
            residuals = sorted(components, key=len, reverse=True)[3:8]
            for comp in residuals:
                # Get one node from this component
                node = list(comp)[0]
                pt = Point(node)
                # reproject to 4326
                pt_4326 = gpd.GeoSeries([pt], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
                residual_info.append(f"Size {len(comp)} at ({pt_4326.y:.5f}, {pt_4326.x:.5f})")
        return sizes, G.number_of_nodes(), residual_info

    sizes_initial, total_nodes_initial, residuals_initial = get_components(edges_gdf)
    top_3_share_initial = (
        sum(sizes_initial[:3]) / total_nodes_initial if total_nodes_initial > 0 else 0
    )

    print("\n============================================================")
    print("3 GATE: OSM-only Graph Structure")
    print("============================================================")
    print(f"Nodes: {total_nodes_initial}, Edges: {len(edges_gdf)}")
    print(f"Top 3 component node share: {top_3_share_initial*100:.2f}%")
    print(f"Top 5 component sizes: {sizes_initial[:5]}")
    if top_3_share_initial < 0.97:
        print("Residual components (top 5 of the remainder):")
        for r in residuals_initial:
            print("  " + r)
    print(f"Mean edge length: {edges_gdf.geometry.length.mean():.2f}m")

    # Also 60% intersection logic for attribution (used for downstream scoring, NOT classification)
    lta_buffer = lta_gdf.geometry.union_all().buffer(3)
    lta_matches = edges_gdf.geometry.intersection(lta_buffer)
    match_ratio = lta_matches.length / edges_gdf.geometry.length
    edges_gdf.loc[match_ratio >= 0.60, "is_covered"] = 1

    # Classification uses NATIVE covered osm
    native_covered_osm = edges_gdf[native_covered_mask].copy()

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

    # Classification
    lta_gdf["class"] = "UNKNOWN"
    lta_gdf["dist_to_covered"] = 999.0
    lta_gdf["perimeter_div_2"] = lta_gdf.geometry.length / 2.0

    for idx, row in lta_gdf.iterrows():
        if native_covered_osm.empty:
            dist = 999.0
        else:
            dist = native_covered_osm.distance(row.geometry).min()
        lta_gdf.at[idx, "dist_to_covered"] = dist

        if dist <= 3.0:
            lta_gdf.at[idx, "class"] = "ALIGNED"
        elif dist <= 10.0:
            lta_gdf.at[idx, "class"] = "OFFSET"
        else:
            # Unrepresented -> Check underground
            if not ug_osm.empty:
                ug_dist = ug_osm.distance(row.geometry).min()
                if ug_dist <= 10.0:
                    lta_gdf.at[idx, "class"] = "UNDERGROUND_OR_INDOOR"
                    nearest_ug_idx = ug_osm.distance(row.geometry).idxmin()
                    edges_gdf.at[nearest_ug_idx, "is_covered"] = 1
                else:
                    lta_gdf.at[idx, "class"] = "UNREPRESENTED-surface"
            else:
                lta_gdf.at[idx, "class"] = "UNREPRESENTED-surface"

    # Synthesize edges
    synth_edges = []
    unsnapped_count = 0
    needs_manual_count = 0
    nodes_geom = nodes_gdf.geometry.union_all()
    edges_geom = edges_gdf.geometry.union_all()

    for idx, row in lta_gdf.iterrows():
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

        p_nearest_s, _ = nearest_points(nodes_geom, start_pt)
        p_nearest_e, _ = nearest_points(nodes_geom, end_pt)

        d_s_node = start_pt.distance(p_nearest_s)
        d_e_node = end_pt.distance(p_nearest_e)

        if row["class"] == "OFFSET":
            cap = min(11.0, row["dist_to_covered"] + 1.0)

            # If nodes are too far, try to snap to nearest edge and project
            p_edge_s, _ = nearest_points(edges_geom, start_pt)
            p_edge_e, _ = nearest_points(edges_geom, end_pt)
            d_s_edge = start_pt.distance(p_edge_s)
            d_e_edge = end_pt.distance(p_edge_e)

            snapped_s = False
            snapped_e = False

            if d_s_node <= cap:
                coords[0] = (p_nearest_s.x, p_nearest_s.y)
                snapped_s = True
            elif d_s_edge <= cap:
                coords[0] = (p_edge_s.x, p_edge_s.y)
                snapped_s = True

            if d_e_node <= cap:
                coords[-1] = (p_nearest_e.x, p_nearest_e.y)
                snapped_e = True
            elif d_e_edge <= cap:
                coords[-1] = (p_edge_e.x, p_edge_e.y)
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
                    }
                )

        elif row["class"] == "UNREPRESENTED-surface":
            # 2m node snap or 5m edge split
            p_edge_s, _ = nearest_points(edges_geom, start_pt)
            p_edge_e, _ = nearest_points(edges_geom, end_pt)
            d_s_edge = start_pt.distance(p_edge_s)
            d_e_edge = end_pt.distance(p_edge_e)

            snapped_s = False
            snapped_e = False

            if d_s_node <= 2.0:
                coords[0] = (p_nearest_s.x, p_nearest_s.y)
                snapped_s = True
            elif d_s_edge <= 5.0:
                coords[0] = (p_edge_s.x, p_edge_s.y)
                snapped_s = True

            if d_e_node <= 2.0:
                coords[-1] = (p_nearest_e.x, p_nearest_e.y)
                snapped_e = True
            elif d_e_edge <= 5.0:
                coords[-1] = (p_edge_e.x, p_edge_e.y)
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
                    }
                )

    if synth_edges:
        synth_gdf = gpd.GeoDataFrame(synth_edges, crs="EPSG:3414")
        edges_gdf = pd.concat([edges_gdf, synth_gdf], ignore_index=True)

    sizes, total_nodes, residuals = get_components(edges_gdf)
    top_3_share = sum(sizes[:3]) / total_nodes if total_nodes > 0 else 0

    print("\n============================================================")
    print("4 GATE: Classification Counts")
    print("============================================================")
    for area in PILOT_AREAS:
        a_gdf = lta_gdf[lta_gdf["PLN_AREA_N"].str.upper() == area.upper()]
        counts = a_gdf["class"].value_counts()
        print(f"[{area}]")
        print(f"  ALIGNED: {counts.get('ALIGNED', 0)}")
        print(f"  OFFSET: {counts.get('OFFSET', 0)}")
        print(f"  UNREPRESENTED-surface: {counts.get('UNREPRESENTED-surface', 0)}")
        print(f"  UNDERGROUND_OR_INDOOR: {counts.get('UNDERGROUND_OR_INDOOR', 0)}")
        print(f"  NEEDS_MANUAL: {counts.get('NEEDS_MANUAL', 0)}")

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
    qa_report = {
        "nodes": total_nodes,
        "edges": len(edges_gdf),
        "mean_edge_length_m": edges_gdf.geometry.length.mean(),
        "connected_components_count": len(sizes),
        "top_5_component_sizes": sizes[:5],
        "top_3_component_node_share_pct": top_3_share * 100,
        "linkway_total_length_m": total_len,
        "synthesized_length_m": synth_len,
        "synthesized_pct_of_total": synth_len / total_len * 100 if total_len else 0,
        "unsnapped_endpoints_count": unsnapped_count,
        "needs_manual_count": needs_manual_count,
    }
    with open(QA_DIR / "conflation_qa_pilot.json", "w") as f:
        json.dump(qa_report, f, indent=2)

    debug_export = lta_gdf[["geometry", "class"]].copy().to_crs(epsg=4326)
    if synth_edges:
        se = gpd.GeoDataFrame(synth_edges, crs="EPSG:3414").to_crs(epsg=4326)
        se["class"] = "SYNTHESIZED: " + se["synth_class"]
        debug_export = pd.concat([debug_export, se[["geometry", "class"]]], ignore_index=True)
    debug_export.to_file(QA_DIR / "pilot_debug.geojson", driver="GeoJSON")


if __name__ == "__main__":
    run_build()
