import geopandas as gpd
import pandas as pd
from pipeline.network import load_planning_area_boundaries, find_raw_file
from pyrosm import OSM


def run_d6():
    print("Running D.6 Dangling-edge note...")
    boundaries_gdf = load_planning_area_boundaries()
    pa_boundary_all = boundaries_gdf[
        boundaries_gdf["PLN_AREA_N"].str.upper().isin(["TOA PAYOH", "BUKIT TIMAH", "DOWNTOWN CORE"])
    ]
    union_poly = pa_boundary_all.unary_union
    union_buffered = union_poly.buffer(500)

    clip_gdf_3414 = gpd.GeoDataFrame(geometry=[union_buffered], crs="EPSG:3414")
    clip_gdf_4326 = clip_gdf_3414.to_crs(epsg=4326)
    union_buffered_4326 = clip_gdf_4326.geometry.iloc[0]

    pbf_path = find_raw_file("*.osm.pbf")
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
        mask = ~(edges["access"].isin(["private", "no"]))
        edges = edges[mask]
    if "highway" in edges.columns:
        edges = edges[edges["highway"] != "construction"]

    edges = edges.to_crs(epsg=3414)
    nodes = nodes.to_crs(epsg=3414)

    # Dangling edges calculation
    if "u" in edges.columns:
        counts = pd.concat([edges["u"], edges["v"]]).value_counts()
        dangling_node_ids = counts[counts == 1].index

        dangling_nodes = nodes[nodes["id"].isin(dangling_node_ids)]

        # Buffer the clip polygon slightly inwards and outwards to catch boundary touches
        boundary_line = union_buffered.boundary

        # Nodes touching boundary (within 1 meter tolerance)
        touching = dangling_nodes.geometry.distance(boundary_line) <= 1.0
        boundary_count = touching.sum()
        interior_count = len(dangling_nodes) - boundary_count
        print(f"Total Dangling Nodes: {len(dangling_nodes)}")
        print(f"Touching Boundary (artifact): {boundary_count}")
        print(f"Interior (real break): {interior_count}")


if __name__ == "__main__":
    run_d6()
