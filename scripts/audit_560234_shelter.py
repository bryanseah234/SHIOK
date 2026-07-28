from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import transform


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.routing import RoutingGraph, prepare_edges_for_routing  # noqa: E402
from pipeline.scoring_integration import (  # noqa: E402
    load_mrt_exits,
    nearest_graph_node,
    select_mrt_exit_candidates,
)

POSTAL = "560234"
GEOM_SHARD = PROJECT_ROOT / "web/public/data/generated_20260728_1405/geom/h3/88652636c1fffff.json"
SCORE_SHARD = (
    PROJECT_ROOT / "web/public/data/generated_20260728_1405/scores/ANG_MO_KIO_PART_001.json"
)
NETWORK_PATH = PROJECT_ROOT / "processed/network_island.parquet"
UNIVERSE_PATH = (
    PROJECT_ROOT / "processed/postal_universe_candidate_full_registered_geocoded.parquet"
)
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


def load_postal_point() -> Point:
    rows = pd.read_parquet(UNIVERSE_PATH, columns=["postal_code", "status", "x", "y"])
    rows["postal_code"] = rows["postal_code"].astype(str).str.zfill(6)
    row = rows[rows["postal_code"] == POSTAL].iloc[0]
    return Point(float(row["x"]), float(row["y"]))


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


def route_diagnostics(route_3414: LineString, score: dict[str, Any]) -> dict[str, Any]:
    edges_df = prepare_edges_for_routing(pd.read_parquet(NETWORK_PATH))
    routing_graph = RoutingGraph.from_prepared_edges(edges_df)
    nodes = (
        pd.concat([edges_df["u"], edges_df["v"]], ignore_index=True)
        .dropna()
        .drop_duplicates()
        .tolist()
    )
    node_xy = np.asarray(nodes, dtype=float)
    origin_point = load_postal_point()
    origin_node, origin_snap_m = nearest_graph_node(origin_point, nodes, node_xy)

    mrt_exits = load_mrt_exits()
    candidates = select_mrt_exit_candidates(origin_point, mrt_exits, nodes, node_xy)
    mayflower_candidates = [
        candidate for candidate in candidates if "MAYFLOWER" in candidate.station_name.upper()
    ]
    best_node = score["best_node"]
    best_candidate = next(
        (
            candidate
            for candidate in mayflower_candidates
            if candidate.station_name == best_node["station"]
            and candidate.exit_code == best_node["exit"]
        ),
        None,
    )

    lambda_sweep: list[dict[str, Any]] = []
    sweep_candidates = [best_candidate] if best_candidate is not None else mayflower_candidates
    od_pairs = {origin_node: [candidate.graph_node for candidate in sweep_candidates]}
    for lambda_value in [0, 0.6, 1.5, 3, 6, 12, 30]:
        results = routing_graph.route(
            od_pairs, shelter_lambda=lambda_value, detour_budget=10.0, include_geometry=False
        )
        if not results:
            continue
        best = sorted(results, key=lambda item: item["length_m"])[0]
        candidate = next(
            (item for item in sweep_candidates if item.graph_node == best.get("destination")),
            None,
        )
        lambda_sweep.append(
            {
                "lambda": lambda_value,
                "candidate": candidate.name if candidate else str(best.get("destination")),
                "length_m": round(float(best["length_m"]), 1),
                "shortest_m": round(float(best["shortest_length_m"]), 1),
                "extra_walk_m": round(
                    float(best["length_m"]) - float(best["shortest_length_m"]), 1
                ),
                "covered_m": round(float(best["covered_m"]), 1),
                "covered_ratio_pct": round(float(best["covered_ratio"]) * 100, 1),
                "within_25pct_detour": bool(
                    float(best["length_m"]) <= 1.25 * float(best["shortest_length_m"])
                ),
            }
        )

    covered_edges = edges_df[edges_df["is_covered"] == 1].copy()
    covered_edges["geometry"] = covered_edges["geometry"].map(
        lambda geom: wkt.loads(geom) if isinstance(geom, str) else geom
    )
    covered_gdf = gpd.GeoDataFrame(covered_edges, geometry="geometry", crs="EPSG:3414")
    best_exit = mrt_exits[
        (mrt_exits["STATION_NA"] == best_node["station"])
        & (mrt_exits["EXIT_CODE"] == best_node["exit"])
    ].iloc[0]
    near_route = covered_gdf[covered_gdf.geometry.distance(route_3414) <= 30].copy()

    return {
        "origin_xy": [round(origin_point.x, 3), round(origin_point.y, 3)],
        "origin_snap_m": round(origin_snap_m, 1),
        "candidate_count": len(candidates),
        "mayflower_candidates": [
            {
                "name": candidate.name,
                "straight_line_m": round(candidate.straight_line_m, 1),
                "snap_distance_m": round(candidate.snap_distance_m, 1),
            }
            for candidate in mayflower_candidates
        ],
        "lambda_sweep_destination": best_candidate.name if best_candidate else None,
        "lambda_sweep": lambda_sweep,
        "nearest_covered_to_origin_m": round(
            float(covered_gdf.geometry.distance(origin_point).min()), 1
        ),
        "nearest_covered_to_best_exit_m": round(
            float(covered_gdf.geometry.distance(best_exit.geometry).min()), 1
        ),
        "covered_edges_within_30m_of_route": int(len(near_route)),
        "covered_len_within_30m_of_route": round(
            float(near_route["length_m"].fillna(near_route.get("length", 0)).sum()), 1
        ),
    }


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
    diagnostics = route_diagnostics(sheltered_3414, score)

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

## Candidate / Lambda Diagnostics

```json
{json.dumps(diagnostics, indent=2)}
```

## Initial Classification

- The shipped score is not a frontend display bug: the score artifact itself reports only {score["paths"]["covered_m"]} m covered.
- Covered graph edges do exist near the route corridor: within 20 m there are {covered_near_20["covered_edge_count"]} covered edges totalling about {covered_near_20["covered_len_m"]} m.
- The current shelter lambda is also too weak for this case: lambda 0.6 leaves the sheltered route identical to shortest, while lambda 1.5+ finds a valid +55 m route within the 25% detour cap and lifts covered ratio from 3.1% to 13.9%.
- Lambda tuning alone does not solve the owner-verified ground truth: even lambda 30 only reaches 13.9% covered, and the nearest covered graph edge is {diagnostics["nearest_covered_to_origin_m"]} m from the postal origin and {diagnostics["nearest_covered_to_best_exit_m"]} m from Exit 5.
- Root-cause classification: mixed algorithm/data issue. Raise lambda only after a broader safety sweep, and separately investigate missing/disconnected/untagged HDB void-deck, overpass, and final-MRT-approach shelter geometry. Do not hardcode a postal-specific score override.

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
