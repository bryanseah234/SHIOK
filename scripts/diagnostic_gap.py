import geopandas as gpd
import pandas as pd
import networkx as nx
from shapely.geometry import Point
from pyrosm import OSM
from pathlib import Path
from shapely.ops import nearest_points
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
        nodes=True, network_type="walking", extra_attributes=["access", "foot"]
    )

    edges_full = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)

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
    nodes_filtered = gpd.GeoDataFrame(nodes, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)

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

    target_pt_4326 = Point(103.86390, 1.28208)
    target_pt_3414 = gpd.GeoSeries([target_pt_4326], crs="EPSG:4326").to_crs(epsg=3414).iloc[0]

    target_comp = None
    for comp in comps_filtered:
        if len(comp) < 10 or len(comp) > 1000:
            continue
        for node in comp:
            pt = Point(node)
            d = pt.distance(target_pt_3414)
            if d < 100:
                target_comp = comp
                break
        if target_comp:
            break

    if not target_comp:
        print("Could not find the component!")
        return

    main_comps = comps_filtered[:3]  # Top 3 components

    comp_geom = gpd.GeoSeries([Point(n) for n in target_comp]).union_all()
    main_geom = gpd.GeoSeries([Point(n) for comp in main_comps for n in comp]).union_all()

    p_comp, p_main = nearest_points(comp_geom, main_geom)
    gap = p_comp.distance(p_main)
    print(f"Gap distance to top-3 components: {gap:.2f}m")
    print(
        f"Nearest points: Comp({p_comp.x:.1f}, {p_comp.y:.1f}), Main({p_main.x:.1f}, {p_main.y:.1f})"
    )


if __name__ == "__main__":
    run_diagnostic()
