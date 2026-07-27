import geopandas as gpd
import pandas as pd
import igraph as ig
from pyrosm import OSM
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"


def print_header(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")


def find_raw_file(pattern: str) -> Path | None:
    for path in RAW_DIR.rglob(pattern):
        if path.is_file():
            return path
    return None


def run_diagnostics():
    # Load LTA linkways
    zip_path = find_raw_file("covered_linkway.zip")
    if not zip_path:
        print("File not found: covered_linkway.zip")
        return

    import tempfile
    import zipfile

    tmp_dir = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp_dir)
    shp_files = list(tmp_dir.rglob("*.shp"))
    lta_gdf = gpd.read_file(shp_files[0]).to_crs(epsg=3414)

    # Load boundary polygons
    boundary_path = find_raw_file("planning_area_boundary.geojson")
    pa_gdf = gpd.read_file(boundary_path).to_crs(epsg=3414)
    pilot_areas = ["Toa Payoh", "Bukit Timah", "Downtown Core"]
    pa_boundary = pa_gdf[
        pa_gdf["PLN_AREA_N"].str.upper().isin([pa.upper() for pa in pilot_areas])
    ].copy()
    union_poly = pa_boundary.geometry.union_all().buffer(500)

    # Filter to pilots via spatial join
    lta_gdf = gpd.sjoin(
        lta_gdf, pa_boundary[["PLN_AREA_N", "geometry"]], how="inner", predicate="intersects"
    )

    # Load OSM
    osm_path = find_raw_file("*.osm.pbf")
    bbox_poly = gpd.GeoSeries([union_poly], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
    osm = OSM(str(osm_path), bounding_box=bbox_poly)

    # Filter highway
    highway_filter = [
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
        "primary_link",
        "track",
        "cycleway",
    ]

    nodes, edges = osm.get_network(
        nodes=True, network_type="walking", extra_attributes=["covered", "tunnel", "indoor"]
    )
    edges_gdf = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)

    # Pre-calculate covered edges (via tags)
    covered_mask = pd.Series(False, index=edges_gdf.index)
    if "covered" in edges_gdf.columns:
        covered_mask |= edges_gdf["covered"].isin(["yes"])
    if "highway" in edges_gdf.columns:
        covered_mask |= edges_gdf["highway"].str.contains("covered", na=False)
    if "tunnel" in edges_gdf.columns:
        covered_mask |= edges_gdf["tunnel"].isin(["yes", "building_passage"])
    if "indoor" in edges_gdf.columns:
        covered_mask |= edges_gdf["indoor"].isin(["yes"])

    covered_osm = edges_gdf[covered_mask].copy()

    # D-A: Nearest-distance histogram
    print_header("D-A: Nearest-distance histogram")
    # For each LTA polygon, distance to nearest covered OSM way
    dists = []
    for _, row in lta_gdf.iterrows():
        dist = covered_osm.distance(row.geometry).min()
        dists.append({"area": row["PLN_AREA_N"], "dist": dist})

    dist_df = pd.DataFrame(dists)

    for area in pilot_areas:
        area_df = dist_df[dist_df["area"].str.upper() == area.upper()]["dist"]
        if area_df.empty:
            continue
        p50 = area_df.median()
        p90 = area_df.quantile(0.90)
        b0_1 = (area_df <= 1).sum()
        b1_3 = ((area_df > 1) & (area_df <= 3)).sum()
        b3_6 = ((area_df > 3) & (area_df <= 6)).sum()
        b6_10 = ((area_df > 6) & (area_df <= 10)).sum()
        b_gt_10 = (area_df > 10).sum()
        print(f"[{area}] p50={p50:.2f}m, p90={p90:.2f}m")
        print(f"  Buckets: 0-1m:{b0_1}, 1-3m:{b1_3}, 3-6m:{b3_6}, 6-10m:{b6_10}, >10m:{b_gt_10}")

    # D-B: Polygon-direct match at 3 m
    print_header("D-B: Polygon-direct match at 3 m")
    cov_buffer = covered_osm.geometry.buffer(3).union_all()

    for area in pilot_areas:
        area_lta = lta_gdf[lta_gdf["PLN_AREA_N"].str.upper() == area.upper()].copy()
        if area_lta.empty:
            continue

        area_lta["match_area"] = area_lta.geometry.intersection(cov_buffer).area
        area_lta["is_matched"] = (area_lta["match_area"] / area_lta.geometry.area) >= 0.5
        match_pct = area_lta["is_matched"].mean() * 100
        print(
            f"[{area}] Matched polygons: {area_lta['is_matched'].sum()} / {len(area_lta)} ({match_pct:.1f}%)"
        )

    # D-C: Length-estimator cross-check
    print_header("D-C: Length-estimator cross-check")
    for area in pilot_areas:
        area_lta = lta_gdf[lta_gdf["PLN_AREA_N"].str.upper() == area.upper()].copy()
        if area_lta.empty:
            continue

        # perimeter/2
        len_perim2 = (area_lta.geometry.length / 2).sum()

        # MBR-axis
        def mbr_axis(geom):
            try:
                mbr = geom.minimum_rotated_rectangle
                coords = list(mbr.exterior.coords)
                d1 = gpd.GeoSeries([geom]).distance(
                    gpd.GeoSeries([gpd.points_from_xy([coords[0][0]], [coords[0][1]])[0]])
                )[
                    0
                ]  # Hacky but we just want length of MBR axis
                # Actually, distance between corners
                from shapely.geometry import LineString

                return max(
                    LineString([coords[0], coords[1]]).length,
                    LineString([coords[1], coords[2]]).length,
                )
            except:
                return 0

        len_mbr = area_lta.geometry.apply(mbr_axis).sum()
        print(f"[{area}] perimeter/2: {len_perim2:.2f}m | MBR-axis: {len_mbr:.2f}m")

    # D-D: Graph-fragmentation probe
    print_header("D-D: Graph-fragmentation probe")
    unique_nodes = pd.concat([edges_gdf["u"], edges_gdf["v"]]).unique()
    node_mapping = {n: i for i, n in enumerate(unique_nodes)}

    edges_mapped = [
        (node_mapping[u], node_mapping[v]) for u, v in zip(edges_gdf["u"], edges_gdf["v"])
    ]
    g = ig.Graph(edges=edges_mapped, directed=False)

    components = g.components()
    comp_sizes = components.sizes()
    giant_size = max(comp_sizes)

    # Calculate edge share of giant component
    giant_nodes = set(components[comp_sizes.index(giant_size)])
    giant_edges = sum(1 for u, v in edges_mapped if u in giant_nodes and v in giant_nodes)

    print(f"Nodes: {g.vcount()}, Edges: {g.ecount()}")
    print(f"Connected components: {len(comp_sizes)}")
    print(f"Giant component edge share: {giant_edges / g.ecount() * 100:.2f}%")

    sorted_sizes = sorted(comp_sizes, reverse=True)
    print(f"Top 5 component sizes (nodes): {sorted_sizes[:5]}")

    # D-E: Node-sharing check
    print_header("D-E: Node-sharing check")
    # Find 5 pairs of edges that share a coordinate
    print("Checking topological nodes from pyrosm output...")
    # Group edges by 'u' and 'v'
    sample_nodes = edges_gdf["u"].value_counts()
    shared_nodes = sample_nodes[sample_nodes > 1].index[:5]

    for i, node_id in enumerate(shared_nodes):
        edges_with_node = edges_gdf[(edges_gdf["u"] == node_id) | (edges_gdf["v"] == node_id)]
        print(
            f"Pair {i+1}: Node ID {node_id} is shared by {len(edges_with_node)} edges. (OSM edge IDs: {edges_with_node['id'].tolist()})"
        )


if __name__ == "__main__":
    run_diagnostics()
