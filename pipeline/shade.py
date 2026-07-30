from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

NPARKS_SHADE_SOURCE_KEYS = {
    "nparks_nature_ways",
    "nparks_park_connector_loop",
    "nparks_tracks",
    "nparks_heritage_trees",
}

SHADE_ONLY_NOTE = "tree_and_greenery_proxy_heat_only_not_rain_shelter"


def prepare_shade_proxy_geometries(
    features: gpd.GeoDataFrame,
    *,
    source_key: str,
    line_buffer_m: float = 8.0,
    point_buffer_m: float = 6.0,
) -> gpd.GeoDataFrame:
    """Convert NParks greenery features into conservative shade proxy polygons.

    These polygons are heat-comfort evidence only. They must not be merged into
    rain-shelter coverage.
    """
    if features.empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")

    frame = features.copy()
    if frame.crs is None:
        frame = frame.set_crs("EPSG:4326")
    frame = frame.to_crs("EPSG:3414")

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        geom_type = geom.geom_type
        if geom_type in {"LineString", "MultiLineString"}:
            shade_geom = geom.buffer(line_buffer_m)
        elif geom_type in {"Point", "MultiPoint"}:
            shade_geom = geom.buffer(point_buffer_m)
        elif geom_type in {"Polygon", "MultiPolygon"}:
            shade_geom = geom
        else:
            continue
        rows.append(
            {
                "source_key": source_key,
                "source_layer": source_key,
                "shade_proxy": 1,
                "shade_weight": 0.5,
                "score_use": SHADE_ONLY_NOTE,
                "confidence": "proxy",
                "geometry": shade_geom,
            }
        )

    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3414")


def compute_edge_shade_ratio(
    edges: gpd.GeoDataFrame,
    shade_polygons: gpd.GeoDataFrame,
) -> pd.Series:
    """Return the fraction of each edge length covered by shade proxy polygons."""
    if edges.empty:
        return pd.Series(dtype=float)
    if shade_polygons.empty:
        return pd.Series(0.0, index=edges.index)

    edge_frame = edges.copy()
    if edge_frame.crs is None:
        edge_frame = edge_frame.set_crs("EPSG:3414")
    edge_frame = edge_frame.to_crs("EPSG:3414")

    shade_frame = shade_polygons.copy()
    if shade_frame.crs is None:
        shade_frame = shade_frame.set_crs("EPSG:3414")
    shade_union = unary_union(shade_frame.to_crs("EPSG:3414").geometry)
    ratios: list[float] = []
    for geom in edge_frame.geometry:
        length = float(geom.length) if geom is not None else 0.0
        if length <= 0:
            ratios.append(0.0)
            continue
        shaded_length = float(geom.intersection(shade_union).length)
        ratios.append(max(0.0, min(1.0, shaded_length / length)))
    return pd.Series(ratios, index=edges.index)
