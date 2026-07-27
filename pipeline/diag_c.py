import warnings

import geopandas as gpd
import pandas as pd
from pipeline.network import (
    approx_centerline,
    find_raw_file,
    load_covered_linkways,
    load_planning_area_boundaries,
)
from pyrosm import OSM
from shapely.errors import ShapelyDeprecationWarning

warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)


def run_c_battery():
    boundaries_gdf = load_planning_area_boundaries()
    linkways_gdf = load_covered_linkways()

    pilot_names = ["TOA PAYOH", "BUKIT TIMAH", "DOWNTOWN CORE"]
    pa_geom = boundaries_gdf[boundaries_gdf["PLN_AREA_N"].str.upper().isin(pilot_names)]
    pa_poly = pa_geom.geometry.union_all()

    pa_linkways = gpd.clip(linkways_gdf, pa_poly)
    pa_linkways = gpd.sjoin(
        pa_linkways, pa_geom[["PLN_AREA_N", "geometry"]], how="inner", predicate="intersects"
    )

    pbf_path = find_raw_file("*.osm.pbf")
    osm = OSM(str(pbf_path), bounding_box=pa_geom.to_crs(epsg=4326).geometry.iloc[0])
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
    _, edges = osm.get_network(network_type="walking", nodes=True)
    if "access" in edges.columns:
        edges = edges[~(edges["access"].isin(["private", "no"]))]
    edges = edges.to_crs(epsg=3414)
    edges = gpd.clip(edges, pa_poly)

    # Extract covered edges
    covered_mask = pd.Series(False, index=edges.index)
    if "covered" in edges.columns:
        covered_mask |= edges["covered"] == "yes"
    if "tunnel" in edges.columns:
        covered_mask |= edges["tunnel"].isin(["yes", "building_passage"])
    if "indoor" in edges.columns:
        covered_mask |= edges["indoor"] == "yes"

    covered_edges = edges[covered_mask]
    covered_union = covered_edges.geometry.union_all()

    print("=== C.1 Nearest-distance histogram ===")
    for area in pilot_names:
        area_linkways = pa_linkways[pa_linkways["PLN_AREA_N"].str.upper() == area].copy()
        if area_linkways.empty:
            continue

        distances = area_linkways.geometry.distance(covered_union)
        print(f"\nArea: {area}")
        print(f"  p50 distance: {distances.median():.2f} m")
        print(f"  p90 distance: {distances.quantile(0.9):.2f} m")

        bins = [0, 1, 3, 6, 10, float("inf")]
        labels = ["0-1m", "1-3m", "3-6m", "6-10m", ">10m"]
        dist_cats = pd.cut(distances, bins=bins, labels=labels, right=False)
        print("  Buckets:")
        counts = dist_cats.value_counts().sort_index()
        for label, count in counts.items():
            print(f"    {label}: {count}")

    print("\n=== C.2 Polygon-direct match test (3m tolerance) ===")
    covered_buffer_3m = covered_union.buffer(3.0)
    pa_linkways["c2_intersection"] = pa_linkways.geometry.intersection(covered_buffer_3m).area
    pa_linkways["c2_matched"] = (pa_linkways["c2_intersection"] / pa_linkways.geometry.area) >= 0.5
    pa_linkways["perimeter_div_2"] = pa_linkways.geometry.length / 2.0

    for area in pilot_names:
        area_linkways = pa_linkways[pa_linkways["PLN_AREA_N"].str.upper() == area]
        if area_linkways.empty:
            continue
        matched_len = area_linkways[area_linkways["c2_matched"]]["perimeter_div_2"].sum()
        total_len = area_linkways["perimeter_div_2"].sum()
        pct = (matched_len / total_len * 100) if total_len > 0 else 0
        print(f"{area}: Match% = {pct:.2f}% ({matched_len:.2f} m / {total_len:.2f} m)")

    print("\n=== C.3 Length-estimator cross-check ===")
    pa_linkways["mbr_axis"] = pa_linkways.geometry.apply(lambda p: approx_centerline(p).length)
    for area in pilot_names:
        area_linkways = pa_linkways[pa_linkways["PLN_AREA_N"].str.upper() == area]
        if area_linkways.empty:
            continue
        total_perim2 = area_linkways["perimeter_div_2"].sum()
        total_mbr = area_linkways["mbr_axis"].sum()
        print(f"{area}: Perimeter/2 = {total_perim2:.2f} m | MBR-axis = {total_mbr:.2f} m")


if __name__ == "__main__":
    run_c_battery()
