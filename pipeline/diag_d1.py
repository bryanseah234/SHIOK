import geopandas as gpd
from pyrosm import OSM
from pathlib import Path

raw_dir = Path("raw")
pbf_path = list(raw_dir.rglob("*.osm.pbf"))[0]

pa_path = list(raw_dir.rglob("planning_area_boundary.geojson"))[0]
pa_gdf = gpd.read_file(pa_path).to_crs(epsg=3414)
pilot_pas = pa_gdf[
    pa_gdf["PLN_AREA_N"].str.upper().isin(["TOA PAYOH", "BUKIT TIMAH", "DOWNTOWN CORE"])
]

print("Loading raw PBF to find covered tags...")
osm = OSM(str(pbf_path))
# We just need covered=*
custom_filter = {"covered": True}
covered_ways = osm.get_data_by_custom_criteria(
    custom_filter=custom_filter, keep_nodes=False, keep_ways=True, keep_relations=False
)

if covered_ways is None or covered_ways.empty:
    print("No covered tags found in OSM!")
else:
    covered_ways = covered_ways.to_crs(epsg=3414)
    print(f"Total covered ways in OSM (island-wide): {len(covered_ways)}")

    for _, row in pilot_pas.iterrows():
        name = row["PLN_AREA_N"]
        poly = row.geometry
        clipped = gpd.clip(covered_ways, poly)
        total_len = clipped.geometry.length.sum() if not clipped.empty else 0.0
        print(f"  {name}: {len(clipped)} ways, {total_len:.2f} m")
