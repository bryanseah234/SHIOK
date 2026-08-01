from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform, unary_union

from pipeline.route_feedback import load_network_edges

HDB_MARKERS = ("hdb", "void_deck", "inferred_hdb")
OFFICIAL_SHELTER_MARKERS = ("covered_linkway", "overhead_bridge_underpass")
BRIDGE_MARKERS = ("overhead_bridge_underpass", "bridge", "underpass", "tunnel")
OSM_SHELTER_MARKERS = (
    "osm_explicit_shelter",
    "osm_building_roof",
    "osm_native_covered",
    "building_passage",
    "canopy",
    "covered",
    "platform",
    "roof",
    "shelter",
    "weather_protection",
)
REVIEW_READY_CLASSES = {
    "official_shelter_overlap_review",
    "bridge_underpass_overlap_review",
    "hdb_source_overlap_review",
    "covered_source_overlap_review",
    "short_partial_hdb_overlap_review",
}


def load_connector_candidates(path: Path) -> gpd.GeoDataFrame:
    candidates = gpd.read_file(path)
    if candidates.empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:3414")
    if candidates.crs is None:
        candidates = candidates.set_crs(epsg=4326)
    return candidates.to_crs(epsg=3414).reset_index(drop=True)


def _text(row: pd.Series, columns: tuple[str, ...]) -> str:
    values = []
    for column in columns:
        value = row.get(column)
        if value is not None and not pd.isna(value):
            values.append(str(value).strip().lower())
    return " ".join(values)


def _has_marker(markers: tuple[str, ...]) -> Callable[[pd.Series], bool]:
    def check(row: pd.Series) -> bool:
        text = _text(
            row,
            (
                "synth_class",
                "source_layer",
                "highway",
                "covered",
                "bridge",
                "tunnel",
                "shelter",
                "shelter_type",
                "weather_protection",
                "man_made",
                "building:part",
                "public_transport",
            ),
        )
        return any(marker in text for marker in markers)

    return check


def _is_covered(row: pd.Series) -> bool:
    value = row.get("is_covered")
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _edge_count_summary(edges: gpd.GeoDataFrame) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for _, row in edges.iterrows():
        for column in ("synth_class", "source_layer", "highway"):
            value = row.get(column)
            if value is not None and not pd.isna(value) and str(value).strip():
                counter[str(value).strip()] += 1
                break
    return dict(sorted(counter.items()))


def _nearest_distance(line, edges: gpd.GeoDataFrame) -> float | None:
    if edges.empty:
        return None
    return round(float(edges.geometry.distance(line).min()), 1)


def _overlap_ratio(line, edges: gpd.GeoDataFrame, buffer_m: float) -> float:
    if edges.empty or line.length <= 0:
        return 0.0
    buffered = [
        geom.buffer(buffer_m) for geom in edges.geometry if geom is not None and not geom.is_empty
    ]
    if not buffered:
        return 0.0
    evidence_union = unary_union(buffered)
    return round(float(line.intersection(evidence_union).length / line.length), 3)


def _candidate_classification(row: dict[str, Any]) -> str:
    if row["official_shelter_overlap_ratio"] >= 0.8:
        return "official_shelter_overlap_review"
    if row["bridge_overlap_ratio"] >= 0.8:
        return "bridge_underpass_overlap_review"
    if row["hdb_overlap_ratio"] >= 0.8:
        return "hdb_source_overlap_review"
    if row["covered_overlap_ratio"] >= 0.8:
        return "covered_source_overlap_review"
    if row["hdb_overlap_ratio"] >= 0.45 and row["length_m"] <= 45.0:
        return "short_partial_hdb_overlap_review"
    return "insufficient_source_overlap"


def _promotion_status(classification: str) -> str:
    if classification in REVIEW_READY_CLASSES:
        return "review_ready_not_scoring"
    if classification == "missing_network_evidence":
        return "blocked_missing_network_evidence_not_scoring"
    return "blocked_insufficient_source_overlap_not_scoring"


def audit_connector_candidates(
    candidates: gpd.GeoDataFrame,
    network_edges: gpd.GeoDataFrame,
    *,
    search_m: float = 80.0,
    evidence_buffer_m: float = 8.0,
) -> gpd.GeoDataFrame:
    if candidates.empty:
        return candidates.copy()
    if network_edges.empty:
        audited = candidates.copy()
        audited["length_m"] = audited.geometry.length.round(1)
        audited["candidate_classification"] = "missing_network_evidence"
        audited["promotion_status"] = _promotion_status("missing_network_evidence")
        return audited

    network = network_edges.to_crs(candidates.crs).reset_index(drop=True)
    sindex = network.sindex
    rows: list[dict[str, Any]] = []
    for _, candidate in candidates.iterrows():
        line = candidate.geometry
        possible = sindex.query(line.buffer(search_m), predicate="intersects")
        nearby = network.iloc[list(possible)].copy()
        if not nearby.empty:
            nearby["distance_m"] = nearby.geometry.distance(line)
            nearby = nearby[nearby["distance_m"] <= search_m].copy()

        hdb_edges = nearby[nearby.apply(_has_marker(HDB_MARKERS), axis=1)].copy()
        official_edges = nearby[nearby.apply(_has_marker(OFFICIAL_SHELTER_MARKERS), axis=1)].copy()
        bridge_edges = nearby[nearby.apply(_has_marker(BRIDGE_MARKERS), axis=1)].copy()
        osm_edges = nearby[nearby.apply(_has_marker(OSM_SHELTER_MARKERS), axis=1)].copy()
        covered_edges = nearby[nearby.apply(_is_covered, axis=1)].copy()

        row = dict(candidate)
        row["length_m"] = round(float(line.length), 1)
        row["nearby_edge_count"] = len(nearby)
        row["nearby_hdb_edge_count"] = len(hdb_edges)
        row["nearby_official_shelter_edge_count"] = len(official_edges)
        row["nearby_bridge_edge_count"] = len(bridge_edges)
        row["nearby_osm_shelter_edge_count"] = len(osm_edges)
        row["nearby_covered_edge_count"] = len(covered_edges)
        row["nearest_hdb_edge_m"] = _nearest_distance(line, hdb_edges)
        row["nearest_official_shelter_edge_m"] = _nearest_distance(line, official_edges)
        row["nearest_bridge_edge_m"] = _nearest_distance(line, bridge_edges)
        row["nearest_osm_shelter_edge_m"] = _nearest_distance(line, osm_edges)
        row["nearest_covered_edge_m"] = _nearest_distance(line, covered_edges)
        row["hdb_overlap_ratio"] = _overlap_ratio(line, hdb_edges, evidence_buffer_m)
        row["official_shelter_overlap_ratio"] = _overlap_ratio(
            line, official_edges, evidence_buffer_m
        )
        row["bridge_overlap_ratio"] = _overlap_ratio(line, bridge_edges, evidence_buffer_m)
        row["osm_shelter_overlap_ratio"] = _overlap_ratio(line, osm_edges, evidence_buffer_m)
        row["covered_overlap_ratio"] = _overlap_ratio(line, covered_edges, evidence_buffer_m)
        row["nearby_sources"] = _edge_count_summary(
            covered_edges if not covered_edges.empty else nearby
        )
        row["candidate_classification"] = _candidate_classification(row)
        row["promotion_status"] = _promotion_status(str(row["candidate_classification"]))
        rows.append(row)

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=candidates.crs)


def audit_summary(audited: gpd.GeoDataFrame) -> dict[str, Any]:
    if audited.empty:
        return {
            "ok": True,
            "candidate_count": 0,
            "classification_counts": {},
            "promotion_status_counts": {},
            "candidates": [],
        }
    counts = Counter(audited["candidate_classification"].astype(str))
    promotion_counts = Counter(audited["promotion_status"].astype(str))
    candidates = []
    for _, row in audited.iterrows():
        candidates.append(
            {
                "postal": str(row.get("postal", "")),
                "destination": str(row.get("destination", "")),
                "segment_index": int(row.get("segment_index", -1)),
                "label": str(row.get("label", "")),
                "length_m": float(row["length_m"]),
                "candidate_classification": str(row["candidate_classification"]),
                "promotion_status": str(row["promotion_status"]),
                "hdb_overlap_ratio": _float_metric(row, "hdb_overlap_ratio"),
                "official_shelter_overlap_ratio": _float_metric(
                    row, "official_shelter_overlap_ratio"
                ),
                "bridge_overlap_ratio": _float_metric(row, "bridge_overlap_ratio"),
                "osm_shelter_overlap_ratio": _float_metric(row, "osm_shelter_overlap_ratio"),
                "covered_overlap_ratio": _float_metric(row, "covered_overlap_ratio"),
                "nearest_hdb_edge_m": _nullable(row.get("nearest_hdb_edge_m")),
                "nearest_official_shelter_edge_m": _nullable(
                    row.get("nearest_official_shelter_edge_m")
                ),
                "nearest_covered_edge_m": _nullable(row.get("nearest_covered_edge_m")),
                "nearby_sources": row.get("nearby_sources", {}),
            }
        )
    return {
        "ok": True,
        "candidate_count": len(audited),
        "classification_counts": dict(sorted(counts.items())),
        "promotion_status_counts": dict(sorted(promotion_counts.items())),
        "candidates": candidates,
    }


def _float_metric(row: pd.Series, key: str) -> float:
    value = row.get(key, 0.0)
    if _nullable(value) is None:
        return 0.0
    return float(value)


def _nullable(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def audit_geojson(audited: gpd.GeoDataFrame) -> dict[str, Any]:
    if audited.empty:
        return {"type": "FeatureCollection", "features": []}
    to_wgs84 = Transformer.from_crs(audited.crs, "EPSG:4326", always_xy=True)
    features = []
    for _, row in audited.iterrows():
        properties = {
            key: _nullable(value)
            for key, value in row.drop(labels=["geometry"]).items()
            if key != "nearby_sources"
        }
        properties["nearby_sources"] = row.get("nearby_sources", {})
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(transform(to_wgs84.transform, row.geometry)),
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _safe_id_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    safe = []
    for char in text:
        if char.isalnum():
            safe.append(char)
        elif safe and safe[-1] != "-":
            safe.append("-")
    return "".join(safe).strip("-") or "unknown"


def draft_correction_geojson(audited: gpd.GeoDataFrame) -> dict[str, Any]:
    """Export review-ready candidates as non-ingested correction drafts."""
    if audited.empty:
        return {"type": "FeatureCollection", "features": []}

    review_ready = audited[audited["promotion_status"].astype(str) == "review_ready_not_scoring"]
    if review_ready.empty:
        return {"type": "FeatureCollection", "features": []}

    to_wgs84 = Transformer.from_crs(review_ready.crs, "EPSG:4326", always_xy=True)
    features = []
    for _, row in review_ready.iterrows():
        postal = str(row.get("postal", "")).zfill(6)
        segment_index = int(row.get("segment_index", -1))
        classification = str(row.get("candidate_classification", ""))
        audit_id = (
            f"feedback-{_safe_id_text(postal)}-segment-{segment_index}-"
            f"{_safe_id_text(classification)}"
        )
        properties = {
            "audit_id": audit_id,
            "status": "needs_owner_review",
            "is_covered": True,
            "covered": "yes",
            "source": "route_feedback_component_gap_source_audit",
            "postal": postal,
            "destination": str(row.get("destination", "")),
            "segment_index": segment_index,
            "label": str(row.get("label", "")),
            "candidate_classification": classification,
            "promotion_status": str(row.get("promotion_status", "")),
            "length_m": float(row.get("length_m", 0.0)),
            "hdb_overlap_ratio": _float_metric(row, "hdb_overlap_ratio"),
            "official_shelter_overlap_ratio": _float_metric(row, "official_shelter_overlap_ratio"),
            "bridge_overlap_ratio": _float_metric(row, "bridge_overlap_ratio"),
            "osm_shelter_overlap_ratio": _float_metric(row, "osm_shelter_overlap_ratio"),
            "covered_overlap_ratio": _float_metric(row, "covered_overlap_ratio"),
            "review_note": (
                "Draft only. Network build ignores this until a human verifies source "
                "evidence and changes status to approved."
            ),
        }
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(transform(to_wgs84.transform, row.geometry)),
                "properties": properties,
            }
        )

    return {
        "type": "FeatureCollection",
        "name": "draft_audited_shelter_corrections",
        "schema": "shiok-audited-shelter-corrections-v1",
        "usage": (
            "Draft review artifact only. Copy reviewed features into "
            "data/audited_shelter_corrections.geojson and set status=approved "
            "only after human source review."
        ),
        "features": features,
    }


def audit_candidate_file(
    candidates_path: Path,
    network_path: Path,
    *,
    search_m: float = 80.0,
    evidence_buffer_m: float = 8.0,
) -> gpd.GeoDataFrame:
    return audit_connector_candidates(
        load_connector_candidates(candidates_path),
        load_network_edges(network_path),
        search_m=search_m,
        evidence_buffer_m=evidence_buffer_m,
    )
