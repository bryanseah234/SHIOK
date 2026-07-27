import json
import sqlite3
import time
import httpx
import geopandas as gpd
from pathlib import Path
from pyrosm import OSM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
DB_PATH = RAW_DIR / "geocode_cache.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS postcodes (
            postal_code TEXT PRIMARY KEY,
            status TEXT,
            lat REAL,
            lon REAL,
            response TEXT
        )
    """
    )
    conn.commit()
    return conn


def extract_postcodes():
    print("Extracting pilot universe (HDB + OSM)...")
    pa_path = list(RAW_DIR.rglob("planning_area_boundary.geojson"))[0]
    pa_gdf = gpd.read_file(pa_path).to_crs(epsg=3414)
    pilot_pas = pa_gdf[
        pa_gdf["PLN_AREA_N"].str.upper().isin(["TOA PAYOH", "BUKIT TIMAH", "DOWNTOWN CORE"])
    ]

    # HDB Postcodes
    hdb_path = list(RAW_DIR.rglob("building_points.geojson"))[0]
    with open(hdb_path, "r", encoding="utf-8") as f:
        hdb_data = json.load(f)

    hdb_island_wide = set()
    hdb_records = []
    for feat in hdb_data.get("features", []):
        props = feat.get("properties", {})
        pc = props.get("POSTAL_COD")
        if pc:
            hdb_island_wide.add(str(pc))
            geom = feat.get("geometry")
            if geom and geom["type"] == "Point":
                hdb_records.append({"postal": str(pc), "geometry": geom})

    print(f"HDB island-wide count: {len(hdb_island_wide)}")

    # Filter HDB to pilot area
    hdb_pilot_set = set()
    if hdb_records:
        hdb_gdf = gpd.GeoDataFrame.from_features(
            [
                {
                    "type": "Feature",
                    "properties": {"postal": r["postal"]},
                    "geometry": r["geometry"],
                }
                for r in hdb_records
            ],
            crs="EPSG:4326",
        )
        hdb_gdf_3414 = hdb_gdf.to_crs(epsg=3414)
        hdb_pilot_gdf = gpd.sjoin(hdb_gdf_3414, pilot_pas, how="inner", predicate="intersects")
        hdb_pilot_set = set(hdb_pilot_gdf["postal"].unique())
    print(f"HDB pilot-area count: {len(hdb_pilot_set)}")

    # OSM Postcodes
    pbf_path = list(RAW_DIR.rglob("*.osm.pbf"))[0]
    osm = OSM(str(pbf_path))
    osm_data = osm.get_data_by_custom_criteria(
        custom_filter={"addr:postcode": True}, keep_nodes=True, keep_ways=True, keep_relations=True
    )

    osm_island_wide = set()
    osm_pilot_set = set()
    if osm_data is not None and not osm_data.empty:
        osm_island_wide = set(osm_data["addr:postcode"].dropna().unique())
        osm_data_3414 = osm_data.to_crs(epsg=3414)
        osm_pilot_gdf = gpd.sjoin(osm_data_3414, pilot_pas, how="inner", predicate="intersects")
        osm_pilot_set = set(osm_pilot_gdf["addr:postcode"].dropna().unique())

    print(f"OSM island-wide count: {len(osm_island_wide)}")
    print(f"OSM pilot-area count: {len(osm_pilot_set)}")

    universe_island_wide = hdb_island_wide.union(osm_island_wide)
    universe_pilot = hdb_pilot_set.union(osm_pilot_set)
    print(f"Deduplicated Island-wide Universe: {len(universe_island_wide)}")
    print(f"Deduplicated Pilot Universe: {len(universe_pilot)}")

    return universe_pilot


def geocode_loop():
    pilot_postcodes = extract_postcodes()
    conn = init_db()
    c = conn.cursor()

    # Populate pending
    for pc in pilot_postcodes:
        c.execute(
            "INSERT OR IGNORE INTO postcodes (postal_code, status) VALUES (?, ?)", (pc, "PENDING")
        )
    conn.commit()

    c.execute("SELECT COUNT(*) FROM postcodes WHERE status='PENDING'")
    pending_count = c.fetchone()[0]
    print(
        f"\nStarting background geocode job. Queue size: {pending_count}. Throttle: 2.0s per request."
    )

    while True:
        c.execute("SELECT postal_code FROM postcodes WHERE status='PENDING' LIMIT 1")
        row = c.fetchone()
        if not row:
            print("Queue empty. Geocoding complete.")
            break

        pc = row[0]
        url = f"https://www.onemap.gov.sg/api/common/elastic/search?searchVal={pc}&returnGeom=Y&getAddrDetails=Y&pageNum=1"
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(url)
            if resp.status_code == 429:
                print("429 Rate limited. Backing off 10s...")
                time.sleep(10)
                continue

            resp.raise_for_status()
            data = resp.json()

            if data.get("found", 0) > 0:
                result = data["results"][0]
                lat = float(result.get("LATITUDE", 0))
                lon = float(result.get("LONGITUDE", 0))
                c.execute(
                    """
                    UPDATE postcodes SET status='SUCCESS', lat=?, lon=?, response=? WHERE postal_code=?
                """,
                    (lat, lon, json.dumps(result), pc),
                )
            else:
                c.execute("UPDATE postcodes SET status='NOT_FOUND' WHERE postal_code=?", (pc,))

        except Exception as e:
            print(f"Error geocoding {pc}: {e}")
            c.execute("UPDATE postcodes SET status='ERROR' WHERE postal_code=?", (pc,))

        conn.commit()
        time.sleep(2.0)  # 0.5 req/s ratelimit


if __name__ == "__main__":
    geocode_loop()
