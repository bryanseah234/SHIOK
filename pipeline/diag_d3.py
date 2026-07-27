import geopandas as gpd
from pipeline.network import (
    load_planning_area_boundaries,
    load_covered_linkways,
    approx_centerline,
    find_raw_file,
)
from pyrosm import OSM


def run_d3():
    print("Loading datasets for D.3 Buffer Sensitivity (Toa Payoh)...")
    boundaries_gdf = load_planning_area_boundaries()
    linkways_gdf = load_covered_linkways()

    pa_geom = boundaries_gdf[boundaries_gdf["PLN_AREA_N"].str.upper() == "TOA PAYOH"]
    pa_poly = pa_geom.unary_union

    # Clip linkways to Toa Payoh
    pa_linkways = gpd.clip(linkways_gdf, pa_poly)
    pa_linkways_centerlines = pa_linkways.copy()
    pa_linkways_centerlines.geometry = pa_linkways_centerlines.geometry.apply(approx_centerline)
    total_len = pa_linkways_centerlines.geometry.length.sum()
    print(f"Toa Payoh true centerline length (approx): {total_len:.2f} m")

    # Load OSM edges (cached or via pyrosm)
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
        mask = ~(edges["access"].isin(["private", "no"]))
        edges = edges[mask]
    edges = edges.to_crs(epsg=3414)
    edges = gpd.clip(edges, pa_poly)

    print("\nBuffer Sensitivity (Match threshold >= 60% of OSM edge length inside buffered LTA):")
    for buf in [3.0, 6.0, 10.0]:
        buffered_lta = pa_linkways.copy()
        buffered_lta.geometry = buffered_lta.geometry.buffer(buf)
        lta_union = buffered_lta.unary_union

        matches = edges.geometry.intersection(lta_union)
        match_ratio = matches.length / edges.geometry.length

        matched_edges = edges[match_ratio >= 0.60]
        # Match LTA back to matched edges to get match%
        matched_lta = gpd.sjoin(buffered_lta, matched_edges, how="inner", predicate="intersects")
        matched_indices = matched_lta.index.unique()

        matched_lta_centerlines = pa_linkways_centerlines[
            pa_linkways_centerlines.index.isin(matched_indices)
        ]
        matched_len = matched_lta_centerlines.geometry.length.sum()

        pct = (matched_len / total_len * 100) if total_len > 0 else 0
        print(f"  {buf}m buffer: Match% = {pct:.2f}% (Matched {matched_len:.2f} m)")


if __name__ == "__main__":
    run_d3()
