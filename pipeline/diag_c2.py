import tempfile
import zipfile
import geopandas as gpd
from pathlib import Path

raw_dir = Path("X:/01 REPOSITORIES/SHIOK/raw")


def get_gdf_from_zip(pattern):
    path = list(raw_dir.rglob(pattern))
    if not path:
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(path[0], "r") as z:
            z.extractall(tmpdir)
        shp_files = list(Path(tmpdir).rglob("*.shp"))
        if not shp_files:
            return None
        gdf = gpd.read_file(shp_files[0])
        return gdf


pob = get_gdf_from_zip("overhead_bridge_underpass.zip")
print(f"POB/underpass feature count: {len(pob) if pob is not None else 0}")

geojson = list(raw_dir.rglob("planning_area_boundary.geojson"))
if geojson:
    bnd = gpd.read_file(geojson[0])
    bnd = bnd.to_crs(epsg=3414)
    for pa in ["Toa Payoh", "Bukit Timah", "Downtown Core"]:
        pa_geom = bnd[bnd["PLN_AREA_N"].str.upper() == pa.upper()]
        if not pa_geom.empty:
            area_km2 = pa_geom.geometry.area.sum() / 1e6
            print(f"Clip polygon {pa} (CRS: {bnd.crs}) area: {area_km2:.2f} km^2")
