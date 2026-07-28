from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import transform


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POSTAL = "560234"
GEOM_SHARD = PROJECT_ROOT / "web/public/data/generated_20260728_1405/geom/h3/88652636c1fffff.json"
SCORE_SHARD = (
    PROJECT_ROOT / "web/public/data/generated_20260728_1405/scores/ANG_MO_KIO_PART_001.json"
)
NETWORK_PATH = PROJECT_ROOT / "processed/network_island.parquet"
OUT_GEOJSON = PROJECT_ROOT / "qa/560234_shelter_audit.geojson"
OUT_NOTES = PROJECT_ROOT / "qa/560234_shelter_audit_notes.md"


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    index = 0
    lat = 0
    lng = 0
    coords: list[tuple[float, float]] = []
    while index < len(encoded):
        result = 0
        shift = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lat += ~(result >> 1) if result & 1 else result >> 1

        result = 0
        shift = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lng += ~(result >> 1) if result & 1 else result >> 1
        coords.append((lng / 1e5, lat / 1e5))
    return coords


def find_raw_file(name: str) -> Path:
    matches = sorted((PROJECT_ROOT / "raw").rglob(name))
    if not matches:
        raise FileNotFoundError(f"raw file not found: {name}")
    return matches[0]


def read_linkways() -> gpd.GeoDataFrame:
    zip_path = find_raw_file("covered_linkway.zip")
    with zipfile.ZipFile(zip_path) as archive:
        shapefiles = [name for name in archive.namelist() if name.lower().endswith(".shp")]
    if not shapefiles:
        raise FileNotFoundError(f"covered_linkway.zip contains no shapefile: {zip_path}")
    return gpd.read_file(f"zip://{zip_path}!{shapefiles[0]}").to_crs("EPSG:3414")


def load_route_geom() -> dict[str, Any]:
    rows = json.loads(GEOM_SHARD.read_text(encoding="utf-8"))
    return next(row for row in rows if row["postal"] == POSTAL)


def load_score() -> dict[str, Any]:
    rows = json.loads(SCORE_SHARD.read_text(encoding="utf-8"))
    return next(row for row in rows if row["postal"] == POSTAL)


def feature_frame(
    layer: str, gdf: gpd.GeoDataFrame, extra: dict[str, Any] | None = None
) -> gpd.GeoDataFrame:
    frame = gdf.copy()
    frame["audit_layer"] = layer
    if extra:
        for key, value in extra.items():
            frame[key] = value
    return frame


def route_line(encoded: str) -> LineString:
    return LineString(decode_polyline(encoded))


def route_points(route: LineString) -> gpd.GeoDataFrame:
    rows = [
        {
            "audit_layer": "route_endpoint",
            "name": "route_start",
            "geometry": Point(route.coords[0]),
        },
        {"audit_layer": "route_endpoint", "name": "route_end", "geometry": Point(route.coords[-1])},
    ]
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


def network_corridor(route_3414: LineString) -> tuple[gpd.GeoDataFrame, list[dict[str, Any]]]:
    minx, miny, maxx, maxy = route_3414.buffer(120).bounds
    cols = [
        "geometry",
        "is_covered",
        "is_synthesized",
        "synth_class",
        "covered",
        "indoor",
        "tunnel",
        "bridge",
        "layer",
        "level",
        "highway",
        "footway",
        "name",
        "length",
        "length_m",
    ]
    edges = pd.read_parquet(NETWORK_PATH, columns=cols)
    edges["geometry"] = edges["geometry"].map(wkt.loads)
    edges = gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:3414")
    edges["effective_len_m"] = edges["length_m"].fillna(edges["length"]).fillna(0)
    candidate = edges.cx[minx:maxx, miny:maxy].copy()
    candidate["dist_to_route_m"] = candidate.geometry.distance(route_3414)

    stats: list[dict[str, Any]] = []
    for threshold in [5, 10, 20, 50, 100]:
        subset = candidate[candidate["dist_to_route_m"] <= threshold]
        covered = subset[subset["is_covered"] == 1]
        stats.append(
            {
                "threshold_m": threshold,
                "edge_count": int(len(subset)),
                "covered_edge_count": int(len(covered)),
                "edge_len_m": round(float(subset["effective_len_m"].sum()), 1),
                "covered_len_m": round(float(covered["effective_len_m"].sum()), 1),
            }
        )

    within_80 = candidate[candidate["dist_to_route_m"] <= 80].copy()
    within_80["audit_layer"] = "network_uncovered_edge"
    within_80.loc[within_80["is_covered"] == 1, "audit_layer"] = "network_covered_edge"
    synth_mask = within_80["is_synthesized"].fillna(0).astype(float) > 0
    within_80.loc[synth_mask, "audit_layer"] = "network_synthetic_edge"
    return within_80.to_crs("EPSG:4326"), stats


def source_layers(route_3414: LineString) -> list[gpd.GeoDataFrame]:
    frames: list[gpd.GeoDataFrame] = []
    corridor = route_3414.buffer(220)

    linkways = read_linkways()
    linkways = linkways[linkways.intersects(corridor)].copy()
    if not linkways.empty:
        linkways["source_area_m2"] = linkways.geometry.area.round(2)
        frames.append(feature_frame("lta_covered_linkway", linkways).to_crs("EPSG:4326"))

    mrt = gpd.read_file(find_raw_file("mrt_lrt_exits.geojson")).to_crs("EPSG:3414")
    mrt = mrt[mrt.geometry.distance(route_3414) <= 450].copy()
    if not mrt.empty:
        frames.append(feature_frame("mrt_lrt_exit", mrt).to_crs("EPSG:4326"))

    hdb = gpd.read_file(find_raw_file("building_points.geojson")).to_crs("EPSG:3414")
    hdb = hdb[hdb.geometry.distance(route_3414) <= 260].copy()
    if not hdb.empty:
        frames.append(feature_frame("hdb_building_point", hdb).to_crs("EPSG:4326"))

    return frames


def main() -> int:
    OUT_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    geom = load_route_geom()
    score = load_score()
    to_3414 = Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)

    shortest_wgs = route_line(geom["shortest"])
    sheltered_wgs = route_line(geom["sheltered"])
    sheltered_3414 = transform(to_3414.transform, sheltered_wgs)

    route_frames = [
        feature_frame(
            "route_shiokest",
            gpd.GeoDataFrame(
                [{"name": "Shiokest route", "geometry": sheltered_wgs}], crs="EPSG:4326"
            ),
        ),
        feature_frame(
            "route_shortest",
            gpd.GeoDataFrame(
                [{"name": "Shortest route", "geometry": shortest_wgs}], crs="EPSG:4326"
            ),
        ),
        route_points(sheltered_wgs),
    ]
    gap_rows = [
        {
            "audit_layer": "route_exposed_gap",
            "name": gap["label"],
            "len_m": gap["len_m"],
            "geometry": route_line(gap["geom"]),
        }
        for gap in geom.get("exposure_gaps", [])
    ]
    if gap_rows:
        route_frames.append(gpd.GeoDataFrame(gap_rows, crs="EPSG:4326"))

    network, corridor_stats = network_corridor(sheltered_3414)
    frames = route_frames + [network] + source_layers(sheltered_3414)
    combined = pd.concat(frames, ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    combined.to_file(OUT_GEOJSON, driver="GeoJSON")

    covered_near_20 = next(item for item in corridor_stats if item["threshold_m"] == 20)
    notes = f"""# 560234 Shelter Audit

Generated: 2026-07-28

## Shipped Score Record

- Postal: {score["postal"]}
- State: {score["state"]}
- Total: {score["total"]}/100
- Best node: {score["best_node"]["name"]}
- Shiokest distance: {score["paths"]["sheltered_m"]} m
- Shortest distance: {score["paths"]["shortest_m"]} m
- Covered length: {score["paths"]["covered_m"]} m
- Covered ratio: {round(score["paths"]["covered_ratio"] * 100, 1)}%
- Shortest covered ratio: {round(score["paths"]["shortest_covered_ratio"] * 100, 1)}%

## Corridor Evidence

Current graph coverage near the shipped Shiokest route:

```json
{json.dumps(corridor_stats, indent=2)}
```

## Initial Classification

- The shipped score is not a frontend display bug: the score artifact itself reports only {score["paths"]["covered_m"]} m covered.
- Covered graph edges do exist near the route corridor: within 20 m there are {covered_near_20["covered_edge_count"]} covered edges totalling about {covered_near_20["covered_len_m"]} m.
- This points to a data/topology issue rather than a scoring-formula issue: the known sheltered walk is likely missing, disconnected, snapped to an uncovered parallel path, or represented as HDB/indoor/void-deck geometry that public OSM/LTA layers do not expose as a routable connected corridor.

## Files

- `qa/560234_shelter_audit.geojson`

Open the GeoJSON in geojson.io or QGIS and inspect the route corridor around Mayflower MRT / Postal 560234. The next manual step is to draw the actual sheltered overpass / HDB cut-through path as an audited correction if it is missing or disconnected.
"""
    OUT_NOTES.write_text(notes, encoding="utf-8")
    print(
        json.dumps(
            {"geojson": str(OUT_GEOJSON), "notes": str(OUT_NOTES), "features": len(combined)},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
