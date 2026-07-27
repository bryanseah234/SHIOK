import json
import tempfile
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
from centerline.geometry import Centerline
from pyrosm import OSM
from shapely.errors import ShapelyDeprecationWarning
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import nearest_points

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
QA_DIR = PROJECT_ROOT / "qa"
PROCESSED_DIR = PROJECT_ROOT / "processed"
QA_DIR.mkdir(exist_ok=True, parents=True)
PROCESSED_DIR.mkdir(exist_ok=True, parents=True)
PILOT_AREAS = ["Toa Payoh", "Bukit Timah", "Downtown Core"]
VALID_SCOPES = {"pilot", "island"}


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


def extract_longest_linestring(geom):
    if isinstance(geom, LineString):
        return geom
    elif isinstance(geom, MultiLineString):
        lines = list(geom.geoms)
        if not lines:
            return None
        return max(lines, key=lambda line: line.length)
    return None


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

    # Load LTA linkways
    zip_path = require_raw_file("covered_linkway.zip")
    tmp_dir = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp_dir)
    shp_files = list(tmp_dir.rglob("*.shp"))
    lta_gdf = gpd.read_file(shp_files[0]).to_crs(epsg=3414)
    lta_gdf = gpd.sjoin(
        lta_gdf, pa_boundary[["PLN_AREA_N", "geometry"]], how="inner", predicate="intersects"
    )

    # Load OSM
    osm_path = require_raw_file("*.osm.pbf")
    bbox_poly = gpd.GeoSeries([union_poly], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
    osm = OSM(str(osm_path), bounding_box=bbox_poly)

    nodes, edges = osm.get_network(
        nodes=True,
        network_type="walking",
        extra_attributes=["covered", "tunnel", "indoor", "layer", "level"],
    )

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
    if len(components_pre) > 3:
        main_comps = components_pre[:3]
        main_geom = gpd.GeoSeries([Point(n) for comp in main_comps for n in comp]).union_all()
        synthetic_edges = []
        print("\n--- SYNTHESIZING OSM GAPS ---")
        for comp in components_pre[3:]:
            if len(comp) > 50:
                comp_geom = gpd.GeoSeries([Point(n) for n in comp]).union_all()
                p_comp, p_main = nearest_points(comp_geom, main_geom)
                dist = p_comp.distance(p_main)
                p_comp_4326 = gpd.GeoSeries([p_comp], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]

                if dist < 5.0:
                    print(
                        f"Auto-bridging size {len(comp)} component at ({p_comp_4326.y:.5f}, {p_comp_4326.x:.5f}) with gap {dist:.2f}m"
                    )
                    synthetic_edges.append(
                        {
                            "geometry": LineString([p_comp, p_main]),
                            "highway": "synthetic_osm_gap",
                            "is_covered": 0,
                            "covered": "no",
                        }
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

        main_comps = components[:3]
        main_geom = None
        if main_comps:
            main_geom = gpd.GeoSeries([Point(n) for comp in main_comps for n in comp]).union_all()

        residual_info = []
        if len(components) > 3:
            for comp in components[3:]:
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

                    # Boundary distance
                    dist_to_boundary = comp_geom.distance(boundary_line)

                    # Dropped edges enclosing it
                    comp_edge_indices = set()
                    for u, v, data in G.edges(nbunch=list(comp), data=True):
                        comp_edge_indices.add(data["idx"])

                    # Filtered out edges that touch this component
                    touching_dropped = []
                    if "access" in edges_full_gdf.columns:
                        dropped_edges = edges_full_gdf[~edges_full_gdf.index.isin(edges_df.index)]
                        # Fast bbox check
                        xmin, ymin, xmax, ymax = comp_geom.bounds
                        possible_matches = dropped_edges.cx[xmin:xmax, ymin:ymax]
                        for _, row_d in possible_matches.iterrows():
                            if (
                                row_d.geometry
                                and not row_d.geometry.is_empty
                                and comp_geom.distance(row_d.geometry) < 1.0
                            ):
                                touching_dropped.append(row_d)

                    c_class = "REAL_DISCONNECTION"
                    evidence = ""

                    if dist_to_boundary < 20.0:
                        c_class = "CLIP_EDGE"
                        evidence = f"dist_to_boundary={dist_to_boundary:.2f}m (<20m)"
                    else:
                        private_ways = [
                            r for r in touching_dropped if r.get("access") in ["private", "no"]
                        ]
                        if len(private_ways) >= 2:
                            c_class = "PRIVATE_ESTATE"
                            ways_info = []
                            for r in private_ways[:2]:
                                h = r.get("highway", "NA")
                                a = r.get("access", "NA")
                                ways_info.append(f"highway={h}/access={a}")
                            evidence = f"enclosed by: {', '.join(ways_info)}"

                        if c_class == "REAL_DISCONNECTION":
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
                                if (
                                    abs(centroid_4326.y - oy) < 0.001
                                    and abs(centroid_4326.x - ox) < 0.001
                                ):
                                    c_class = oclass
                                    evidence = oevid
                                    break

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
    lta_buffer = lta_gdf.geometry.union_all().buffer(3)
    lta_matches = edges_gdf.geometry.intersection(lta_buffer)
    match_ratio = lta_matches.length / edges_gdf.geometry.length
    lta_match_mask = match_ratio >= 0.60
    lta_match_edge_length = edges_gdf.loc[lta_match_mask, "geometry"].length.sum()
    edges_gdf.loc[match_ratio >= 0.60, "is_covered"] = 1
    native_covered_edge_length = edges_gdf.loc[native_covered_mask, "geometry"].length.sum()

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
        "covered_edge_length_m_union": float(covered_union_length),
        "flags": flags,
    }
    with open(qa_path, "w") as f:
        json.dump(qa_report, f, indent=2)

    debug_export = lta_gdf[["geometry", "class"]].copy().to_crs(epsg=4326)
    if synth_edges:
        se = gpd.GeoDataFrame(synth_edges, crs="EPSG:3414").to_crs(epsg=4326)
        se["class"] = "SYNTHESIZED: " + se["synth_class"]
        debug_export = pd.concat([debug_export, se[["geometry", "class"]]], ignore_index=True)
    debug_export.to_file(debug_path, driver="GeoJSON")

    # Save network for routing!
    edges_export = pd.DataFrame(edges_gdf.copy())
    if "geometry" in edges_export.columns:
        edges_export["geometry"] = edges_export["geometry"].apply(lambda x: x.wkt if x else None)
    edges_export.to_parquet(network_path)


if __name__ == "__main__":
    run_build()
