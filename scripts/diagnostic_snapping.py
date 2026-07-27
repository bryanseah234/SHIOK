import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, MultiLineString
from shapely.ops import nearest_points
from pyrosm import OSM
from pathlib import Path
import tempfile
import zipfile
import warnings
from shapely.errors import ShapelyDeprecationWarning

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)
from centerline.geometry import Centerline

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
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
        cl = Centerline(poly)
        return extract_longest_linestring(cl.geometry)
    except Exception:
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return LineString([poly.centroid, poly.centroid])
        p1 = Point(coords[0])
        furthest = max([Point(c) for c in coords[1:]], key=lambda p: p1.distance(p))
        return LineString([p1, furthest])


def run_diagnostics():
    # Setup data like build
    pa_gdf = gpd.read_file(find_raw_file("planning_area_boundary.geojson")).to_crs(epsg=3414)
    pa_boundary = pa_gdf[
        pa_gdf["PLN_AREA_N"].str.upper().isin([p.upper() for p in PILOT_AREAS])
    ].copy()
    union_poly = pa_boundary.geometry.union_all().buffer(500)

    zip_path = find_raw_file("covered_linkway.zip")
    tmp_dir = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp_dir)
    shp_files = list(tmp_dir.rglob("*.shp"))
    lta_gdf = gpd.read_file(shp_files[0]).to_crs(epsg=3414)
    lta_gdf = gpd.sjoin(
        lta_gdf, pa_boundary[["PLN_AREA_N", "geometry"]], how="inner", predicate="intersects"
    )

    osm_path = find_raw_file("*.osm.pbf")
    bbox_poly = gpd.GeoSeries([union_poly], crs="EPSG:3414").to_crs(epsg=4326).iloc[0]
    osm = OSM(str(osm_path), bounding_box=bbox_poly)
    nodes, edges = osm.get_network(
        nodes=True, network_type="walking", extra_attributes=["covered", "tunnel", "indoor"]
    )

    edges_gdf = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)
    nodes_gdf = gpd.GeoDataFrame(nodes, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3414)

    covered_mask = pd.Series(False, index=edges_gdf.index)
    if "covered" in edges_gdf.columns:
        covered_mask |= edges_gdf["covered"].isin(["yes", "covered", "arcade", "colonnade"])
    if "highway" in edges_gdf.columns:
        covered_mask |= edges_gdf["highway"].str.contains("covered", na=False)

    edges_gdf.loc[covered_mask, "is_covered"] = 1
    covered_osm = edges_gdf[edges_gdf["is_covered"] == 1].copy()

    # Find some OFFSET polygons
    lta_gdf["dist_to_covered"] = 999.0
    for idx, row in lta_gdf.iterrows():
        lta_gdf.at[idx, "dist_to_covered"] = covered_osm.distance(row.geometry).min()

    offset_polys = lta_gdf[
        (lta_gdf["dist_to_covered"] > 3.0) & (lta_gdf["dist_to_covered"] <= 10.0)
    ].head(5)

    print("\n--- SNAPPING DIAGNOSTICS FOR 5 OFFSET POLYGONS ---")
    nodes_geom = nodes_gdf.geometry.union_all()

    for i, (idx, row) in enumerate(offset_polys.iterrows(), 1):
        dist_da = row["dist_to_covered"]
        poly = row.geometry
        line = get_skeleton(poly)
        if not line or line.is_empty:
            print(f"[{i}] OFFSET: dist={dist_da:.2f}m | Skeleton failed.")
            continue

        coords = list(line.coords)
        s_pt, e_pt = Point(coords[0]), Point(coords[-1])

        p_near_s, _ = nearest_points(nodes_geom, s_pt)
        p_near_e, _ = nearest_points(nodes_geom, e_pt)

        d_s = s_pt.distance(p_near_s)
        d_e = e_pt.distance(p_near_e)

        print(f"[{i}] OFFSET: D-A dist = {dist_da:.2f}m")
        print(f"    Skeleton start: ({s_pt.x:.1f}, {s_pt.y:.1f}), Nearest node dist: {d_s:.2f}m")
        print(f"    Skeleton end:   ({e_pt.x:.1f}, {e_pt.y:.1f}), Nearest node dist: {d_e:.2f}m")
        cap = min(11.0, dist_da + 1.0)
        print(f"    Computed cap: {cap:.2f}m")


if __name__ == "__main__":
    run_diagnostics()
