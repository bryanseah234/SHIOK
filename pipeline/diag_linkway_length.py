import tempfile
import zipfile
import geopandas as gpd
from pathlib import Path


def get_linkways():
    raw_dir = Path("X:/01 REPOSITORIES/SHIOK/raw")
    zip_path = list(raw_dir.rglob("covered_linkway.zip"))
    if not zip_path:
        print("covered_linkway.zip not found")
        return None
    zip_path = zip_path[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmpdir)
        shp_files = list(Path(tmpdir).rglob("*.shp"))
        if not shp_files:
            return None
        gdf = gpd.read_file(shp_files[0])
        if gdf.crs is None or gdf.crs.to_epsg() != 3414:
            gdf = gdf.to_crs(epsg=3414)
        return gdf


gdf = get_linkways()
if gdf is not None:
    print(f"Total rows in LTA linkways: {len(gdf)}")
    print(f"Geometry type: {gdf.geom_type.unique()}")
    print(f"Total length (m) of all linkways: {gdf.geometry.length.sum():.2f}")
