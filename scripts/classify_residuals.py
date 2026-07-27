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


def run_classification():
    pa_gdf = gpd.read_file(find_raw_file("planning_area_boundary.geojson")).to_crs(epsg=3414)
    pa_boundary = pa_gdf[
        pa_gdf["PLN_AREA_N"].str.upper().isin(["TOA PAYOH", "BUKIT TIMAH", "DOWNTOWN CORE"])
    ]
    union_poly = pa_boundary.geometry.union_all().buffer(500)
    boundary_line = union_poly.boundary

    osm_path = find_raw_file("*.osm.pbf")
    bbox_poly = gpd.GeoSeries([union_poly], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
    osm = OSM(str(osm_path), bounding_box=bbox_poly)

    nodes, edges = osm.get_network(
        nodes=True, network_type="walking", extra_attributes=["access", "foot"]
    )

    edges_full = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)

    # Filtered graph (current logic)
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

    # Landuse
    landuse = osm.get_landuse()
    landuse_gdf = None
    if landuse is not None:
        landuse_gdf = gpd.GeoDataFrame(landuse, geometry="geometry", crs="EPSG:4326").to_crs(
            epsg=3414
        )
        landuse_gdf = landuse_gdf[landuse_gdf["landuse"] == "residential"]

    def get_components(edges_df):
        G = nx.Graph()
        for idx, row in edges_df.iterrows():
            if row.geometry and not row.geometry.is_empty:
                c = row.geometry.coords
                u = (round(c[0][0], 2), round(c[0][1], 2))
                v = (round(c[-1][0], 2), round(c[-1][1], 2))
                G.add_edge(u, v, idx=idx)
        return sorted(list(nx.connected_components(G)), key=len, reverse=True), G

    comps_filtered, G_filtered = get_components(edges_filtered)

    print("\n--- RESIDUAL COMPONENTS > 50 NODES ---")
    residuals = [c for c in comps_filtered[3:] if len(c) > 50]

    dropped_edges = edges_full[~edges_full.index.isin(edges_filtered.index)]

    for i, comp in enumerate(residuals, 1):
        # Centroid
        nodes_list = [Point(n) for n in comp]
        comp_geom = gpd.GeoSeries(nodes_list).union_all()
        centroid_3414 = comp_geom.centroid
        centroid_4326 = gpd.GeoSeries([centroid_3414], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]

        comp_edge_indices = set()
        for u, v, data in G_filtered.edges(nbunch=list(comp), data=True):
            comp_edge_indices.add(data["idx"])
        comp_lines = edges_filtered.loc[list(comp_edge_indices)].geometry.union_all()

        # Classification
        c_class = "REAL_DISCONNECTION"

        # 1. Check CLIP_EDGE
        dist_to_boundary = comp_geom.distance(boundary_line)
        if dist_to_boundary <= 20.0:
            c_class = "CLIP_EDGE"
        else:
            # 2. Check PRIVATE_ESTATE
            # See if surrounded by dropped edges
            touching_dropped = False
            for _, row in dropped_edges.iterrows():
                if row.geometry and row.geometry.distance(comp_lines) < 1.0:
                    touching_dropped = True
                    break

            if touching_dropped:
                c_class = "PRIVATE_ESTATE"
            else:
                # Check residential landuse
                if landuse_gdf is not None:
                    # Does the centroid fall in residential?
                    if landuse_gdf.geometry.contains(centroid_3414).any():
                        c_class = "PRIVATE_ESTATE"

        print(
            f"[{i}] Size: {len(comp)} | Centroid: ({centroid_4326.y:.5f}, {centroid_4326.x:.5f}) | Class: {c_class}"
        )


if __name__ == "__main__":
    run_classification()
