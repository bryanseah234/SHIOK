import geopandas as gpd
from pyrosm import OSM
from pathlib import Path
import json

raw_dir = Path("raw")
pbf_path = list(raw_dir.rglob("*.osm.pbf"))[0]

# 1. Extract every building/address carrying addr:postcode island-wide
osm = OSM(str(pbf_path))
custom_filter = {"addr:postcode": True}
osm_postcodes_gdf = osm.get_data_by_custom_criteria(
    custom_filter=custom_filter, keep_nodes=True, keep_ways=True, keep_relations=False
)
osm_postcodes = set()
if osm_postcodes_gdf is not None and "addr:postcode" in osm_postcodes_gdf.columns:
    osm_postcodes = set(osm_postcodes_gdf["addr:postcode"].dropna().unique())

# HDB Set
hdb_path = list(raw_dir.rglob("building_points.geojson"))[0]
with open(hdb_path, "r", encoding="utf-8") as f:
    hdb_data = json.load(f)
hdb_postcodes = set()
for feat in hdb_data.get("features", []):
    props = feat.get("properties", {})
    pc = props.get("POSTAL") or props.get("POSTAL_CD") or props.get("postal_code")
    if pc:
        hdb_postcodes.add(str(pc))

# 2. Re-quantify: HDB set + OSM-postcode set, deduplicated
combined = hdb_postcodes.union(osm_postcodes)
print(f"HDB set postcodes: {len(hdb_postcodes)}")
print(f"OSM set postcodes: {len(osm_postcodes)}")
print(f"Combined deduplicated island-wide: {len(combined)}")

# 3. Pilot area only counts (for Toa Payoh, Bukit Timah, Downtown Core)
# In a real scenario we'd do a spatial join to the planning areas.
# Let's do that.
pa_path = list(raw_dir.rglob("planning_area_boundary.geojson"))[0]
pa_gdf = gpd.read_file(pa_path)
pilot_pas = pa_gdf[
    pa_gdf["PLN_AREA_N"].str.upper().isin(["TOA PAYOH", "BUKIT TIMAH", "DOWNTOWN CORE"])
]

if osm_postcodes_gdf is not None:
    osm_pilot = gpd.sjoin(osm_postcodes_gdf, pilot_pas.to_crs(osm_postcodes_gdf.crs), how="inner")
    osm_pilot_pcs = set(osm_pilot["addr:postcode"].dropna().unique())
else:
    osm_pilot_pcs = set()

print(f"Pilot-area OSM postcodes: {len(osm_pilot_pcs)}")
