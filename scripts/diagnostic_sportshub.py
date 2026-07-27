import geopandas as gpd
import pandas as pd
import networkx as nx
from shapely.geometry import Point
from pyrosm import OSM
from pathlib import Path
import warnings
from shapely.errors import ShapelyDeprecationWarning

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"


def find_raw_file(pattern: str) -> Path | None:
    for path in RAW_DIR.rglob(pattern):
        if path.is_file():
            return path
    return None


def run_diagnostic():
    pa_gdf = gpd.read_file(find_raw_file("planning_area_boundary.geojson")).to_crs(epsg=3414)
    pa_boundary = pa_gdf[
        pa_gdf["PLN_AREA_N"].str.upper().isin(["TOA PAYOH", "BUKIT TIMAH", "DOWNTOWN CORE"])
    ]
    union_poly = pa_boundary.geometry.union_all().buffer(500)

    osm_path = find_raw_file("*.osm.pbf")
    bbox_poly = gpd.GeoSeries([union_poly], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
    osm = OSM(str(osm_path), bounding_box=bbox_poly)

    nodes, edges = osm.get_network(
        nodes=True,
        network_type="walking",
        extra_attributes=["covered", "tunnel", "indoor", "layer", "level", "access", "foot"],
    )

    # 1. Full unfiltered graph
    edges_full = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)

    # 2. Filtered graph (current logic)
    mask = pd.Series(True, index=edges.index)
    if "access" in edges.columns:
        if "foot" in edges.columns:
            mask = ~((edges["access"].isin(["private", "no"])) & (edges["foot"] != "yes"))
        else:
            mask = ~(edges["access"].isin(["private", "no"]))

    if "highway" in edges.columns:
        exclude = ["motorway", "motorway_link", "trunk", "trunk_link", "construction"]
        if "foot" in edges.columns:
            mask &= ~((edges["highway"].isin(exclude)) & (edges["foot"] != "yes"))
        else:
            mask &= ~edges["highway"].isin(exclude)

    edges_filtered = edges_full[mask].copy()

    import sys

    if len(sys.argv) == 3:
        target_pt_4326 = Point(float(sys.argv[2]), float(sys.argv[1]))
    else:
        target_pt_4326 = Point(103.87233, 1.30167)

    target_pt_3414 = gpd.GeoSeries([target_pt_4326], crs="EPSG:4326").to_crs(epsg=3414).iloc[0]

    def get_components(edges_df):
        G = nx.Graph()
        for idx, row in edges_df.iterrows():
            if row.geometry and not row.geometry.is_empty:
                c = row.geometry.coords
                u = (round(c[0][0], 2), round(c[0][1], 2))
                v = (round(c[-1][0], 2), round(c[-1][1], 2))
                G.add_edge(u, v, idx=idx)
        return list(nx.connected_components(G)), G

    comps_filtered, G_filtered = get_components(edges_filtered)

    # Find component containing the target
    target_comp = None
    min_dist = 99999

    for comp in comps_filtered:
        if len(comp) < 100 or len(comp) > 1000:
            continue
        for node in comp:
            pt = Point(node)
            d = pt.distance(target_pt_3414)
            if d < min_dist:
                min_dist = d
                target_comp = comp

    if not target_comp:
        print("Could not find the 137-node component!")
        return

    print(
        f"Found target component with size {len(target_comp)}. Min dist to target coords: {min_dist:.2f}m"
    )

    # Find all edges in this component
    comp_edge_indices = set()
    for u, v, data in G_filtered.edges(nbunch=list(target_comp), data=True):
        comp_edge_indices.add(data["idx"])

    comp_geom = edges_filtered.loc[list(comp_edge_indices)].geometry.union_all()

    # Find edges in the FULL graph that touch this component but were DROPPED
    dropped_edges = edges_full[~edges_full.index.isin(edges_filtered.index)]

    touching_dropped = []
    for idx, row in dropped_edges.iterrows():
        if row.geometry and row.geometry.distance(comp_geom) < 1.0:
            touching_dropped.append(row)

    print(f"\nDropped ways touching the Sports Hub component: {len(touching_dropped)}")
    for i, row in enumerate(touching_dropped[:5]):
        print(
            f"  [{i}] Highway: {row.get('highway', 'NA')}, Access: {row.get('access', 'NA')}, Foot: {row.get('foot', 'NA')}"
        )

    if not touching_dropped:
        print(
            "\nNo dropped edges touch this component. This means it is natively disconnected in OSM!"
        )
        # Let's find distance to the nearest main component
        main_comp = max(comps_filtered, key=len)
        main_edge_indices = set()
        for u, v, data in G_filtered.edges(nbunch=list(main_comp), data=True):
            main_edge_indices.add(data["idx"])
        main_geom = edges_filtered.loc[list(main_edge_indices)].geometry.union_all()
        gap = comp_geom.distance(main_geom)
        print(f"Gap distance to main giant component: {gap:.2f}m")


if __name__ == "__main__":
    run_diagnostic()
