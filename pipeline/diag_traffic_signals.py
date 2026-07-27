import sys
import tempfile
import zipfile
import geopandas as gpd
from pathlib import Path

zip_path = Path(
    "X:/01 REPOSITORIES/SHIOK/raw/942ff2506603f431f0782a3acdc70fec75d4b15c73b54f1a983c804c60d818af/traffic_signals.zip"
)
if not zip_path.exists():
    print("Traffic signals zip not found")
    sys.exit(1)

with tempfile.TemporaryDirectory() as tmpdir:
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmpdir)

    shp_files = list(Path(tmpdir).rglob("*.shp"))
    if not shp_files:
        print("No shapefile found in the traffic signals zip")
        sys.exit(1)

    gdf = gpd.read_file(shp_files[0])
    print(f"Loaded {shp_files[0].name}")
    print("CRS:", gdf.crs)
    print("Fields:", list(gdf.columns))
    print("\nFirst 5 rows:")
    print(gdf.head(5).to_string())
