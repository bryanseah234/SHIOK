from __future__ import annotations

import json
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, mapping
from shapely.ops import transform

COVERED_LABELS = {"sheltered", "void_deck", "covered_bridge", "underpass"}
BRIDGE_LABELS = {"covered_bridge", "underpass"}
NETWORK_COLUMNS = [
    "geometry",
    "is_covered",
    "is_synthesized",
    "synth_class",
    "source_layer",
    "highway",
    "covered",
    "bridge",
    "tunnel",
    "indoor",
    "length_m",
]


def load_feedback_routes(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise ValueError(f"feedback file must contain an object or list of objects: {path}")


def _segment_label(labels: list[Any], index: int) -> str:
    if index < len(labels):
        return str(labels[index]).strip().lower() or "unlabeled"
    return "unlabeled"


def feedback_segments(routes: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    to_3414 = Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)
    rows: list[dict[str, Any]] = []
    for route_index, route in enumerate(routes):
        waypoints = route.get("waypoints", [])
        if not isinstance(waypoints, list) or len(waypoints) < 2:
            continue
        labels = route.get("segment_labels", [])
        if not isinstance(labels, list):
            labels = []
        for segment_index, (start, end) in enumerate(pairwise(waypoints)):
            if not (
                isinstance(start, list)
                and isinstance(end, list)
                and len(start) == 2
                and len(end) == 2
            ):
                continue
            line_wgs84 = LineString(
                [
                    (float(start[1]), float(start[0])),
                    (float(end[1]), float(end[0])),
                ]
            )
            line_3414 = transform(to_3414.transform, line_wgs84)
            rows.append(
                {
                    "route_index": route_index,
                    "postal": str(route.get("postal", "")).zfill(6),
                    "destination": str(route.get("destination", "")),
                    "issue": str(route.get("issue", "")),
                    "source": str(route.get("source", "")),
                    "created_at": str(route.get("created_at", "")),
                    "segment_index": segment_index,
                    "label": _segment_label(labels, segment_index),
                    "length_m": round(float(line_3414.length), 1),
                    "geometry": line_3414,
                }
            )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3414")


def load_network_edges(path: Path) -> gpd.GeoDataFrame:
    frame = pd.read_parquet(path)
    keep = [column for column in NETWORK_COLUMNS if column in frame.columns]
    frame = frame[keep].copy()
    frame["geometry"] = frame["geometry"].map(
        lambda geom: wkt.loads(geom) if isinstance(geom, str) else geom
    )
    return gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:3414").reset_index(drop=True)


def _is_covered(frame: gpd.GeoDataFrame) -> pd.Series:
    if "is_covered" not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame["is_covered"].fillna(0).astype(float) > 0


def _truthy_string(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "covered"}


def _edge_source_summary(edges: gpd.GeoDataFrame) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for _, row in edges.iterrows():
        for column in ["synth_class", "source_layer", "highway"]:
            value = row.get(column)
            if value is not None and not pd.isna(value) and str(value).strip():
                counter[str(value).strip()] += 1
                break
    return dict(sorted(counter.items()))


def _has_bridge_evidence(edges: gpd.GeoDataFrame) -> bool:
    if edges.empty:
        return False
    for _, row in edges.iterrows():
        source = str(row.get("source_layer", "")).lower()
        highway = str(row.get("highway", "")).lower()
        bridge = _truthy_string(row.get("bridge"))
        tunnel = _truthy_string(row.get("tunnel"))
        if "overhead_bridge_underpass" in source or bridge or tunnel or "underpass" in highway:
            return True
    return False


def _has_hdb_evidence(edges: gpd.GeoDataFrame) -> bool:
    if edges.empty:
        return False
    for _, row in edges.iterrows():
        synth_class = str(row.get("synth_class", "")).lower()
        source = str(row.get("source_layer", "")).lower()
        if "hdb" in synth_class or "hdb" in source or "void_deck" in synth_class:
            return True
    return False


def _classify(label: str, nearby: gpd.GeoDataFrame, covered: gpd.GeoDataFrame) -> str:
    if label == "exposed":
        return "user_marked_exposed_no_shelter_expected"
    if nearby.empty:
        return "missing_routable_geometry"
    if covered.empty:
        if label == "void_deck":
            return "missing_hdb_void_deck_connector"
        if label in BRIDGE_LABELS:
            return "missing_bridge_underpass_connector_or_coverage"
        if label in COVERED_LABELS:
            return "routable_but_uncovered_or_missing_shelter_source"
        return "routable_geometry_nearby"
    if label == "void_deck":
        if _has_hdb_evidence(covered):
            return "hdb_void_deck_evidence_nearby_check_connectivity"
        return "covered_evidence_nearby_but_not_void_deck"
    if label in BRIDGE_LABELS:
        if _has_bridge_evidence(covered):
            return "bridge_underpass_evidence_nearby_check_endpoint_snap"
        return "covered_evidence_nearby_but_not_bridge_underpass"
    if label in COVERED_LABELS:
        return "covered_evidence_nearby_check_connectivity_or_snap"
    return "routable_geometry_nearby"


def classify_feedback_segments(
    segments: gpd.GeoDataFrame,
    network_edges: gpd.GeoDataFrame,
    *,
    search_m: float = 20.0,
) -> gpd.GeoDataFrame:
    if segments.empty:
        return segments.copy()
    if network_edges.empty:
        audited = segments.copy()
        audited["classification"] = "missing_routable_geometry"
        audited["nearby_edge_count"] = 0
        audited["nearby_covered_edge_count"] = 0
        audited["nearest_edge_m"] = None
        audited["nearest_covered_edge_m"] = None
        audited["nearby_sources"] = [{} for _ in range(len(audited))]
        return audited

    network = network_edges.to_crs(segments.crs).reset_index(drop=True)
    sindex = network.sindex
    rows: list[dict[str, Any]] = []
    for _, segment in segments.iterrows():
        line = segment.geometry
        possible = sindex.query(line.buffer(search_m), predicate="intersects")
        candidates = network.iloc[list(possible)].copy()
        if not candidates.empty:
            candidates["distance_m"] = candidates.geometry.distance(line)
            nearby = candidates[candidates["distance_m"] <= search_m].copy()
        else:
            nearby = candidates
        covered = nearby[_is_covered(nearby)].copy() if not nearby.empty else nearby
        classification = _classify(str(segment["label"]), nearby, covered)

        row = dict(segment)
        row["classification"] = classification
        row["needs_model_qa"] = bool(
            str(segment["label"]) in COVERED_LABELS
            and classification
            not in {
                "covered_evidence_nearby_check_connectivity_or_snap",
                "bridge_underpass_evidence_nearby_check_endpoint_snap",
                "hdb_void_deck_evidence_nearby_check_connectivity",
            }
        )
        row["nearby_edge_count"] = len(nearby)
        row["nearby_covered_edge_count"] = len(covered)
        row["nearest_edge_m"] = (
            round(float(nearby["distance_m"].min()), 1) if not nearby.empty else None
        )
        row["nearest_covered_edge_m"] = (
            round(float(covered["distance_m"].min()), 1) if not covered.empty else None
        )
        row["nearby_sources"] = _edge_source_summary(covered if not covered.empty else nearby)
        rows.append(row)

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=segments.crs)


def audit_report(audited_segments: gpd.GeoDataFrame) -> dict[str, Any]:
    if audited_segments.empty:
        return {"ok": True, "route_count": 0, "segment_count": 0, "segments": []}

    classification_counts = Counter(audited_segments["classification"].astype(str))
    label_counts = Counter(audited_segments["label"].astype(str))
    route_count = int(audited_segments[["postal", "destination"]].drop_duplicates().shape[0])
    segments = []
    for _, row in audited_segments.iterrows():
        segments.append(
            {
                "postal": row["postal"],
                "destination": row["destination"],
                "segment_index": int(row["segment_index"]),
                "label": row["label"],
                "length_m": float(row["length_m"]),
                "classification": row["classification"],
                "needs_model_qa": bool(row["needs_model_qa"]),
                "nearby_edge_count": int(row["nearby_edge_count"]),
                "nearby_covered_edge_count": int(row["nearby_covered_edge_count"]),
                "nearest_edge_m": row["nearest_edge_m"],
                "nearest_covered_edge_m": row["nearest_covered_edge_m"],
                "nearby_sources": row["nearby_sources"],
            }
        )
    return {
        "ok": True,
        "route_count": route_count,
        "segment_count": len(audited_segments),
        "label_counts": dict(sorted(label_counts.items())),
        "classification_counts": dict(sorted(classification_counts.items())),
        "segments": segments,
    }


def audit_geojson(audited_segments: gpd.GeoDataFrame) -> dict[str, Any]:
    if audited_segments.empty:
        return {"type": "FeatureCollection", "features": []}
    output = audited_segments.to_crs("EPSG:4326")
    features = []
    for _, row in output.iterrows():
        properties = {
            key: value
            for key, value in row.drop(labels=["geometry"]).items()
            if key != "nearby_sources"
        }
        properties["nearby_sources"] = json.dumps(row["nearby_sources"], sort_keys=True)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(row.geometry),
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}
