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
from shapely.geometry import LineString, Point, mapping
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


def _line_endpoints(geometry: Any) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type == "LineString":
        coords = list(geometry.coords)
    elif geometry.geom_type == "MultiLineString":
        parts = [part for part in geometry.geoms if not part.is_empty]
        if not parts:
            return None
        coords = list(parts[0].coords) + list(parts[-1].coords)
    else:
        return None
    if len(coords) < 2:
        return None
    return (float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))


class _UnionFind:
    def __init__(self) -> None:
        self.parent: list[int] = []
        self.size: list[int] = []

    def add(self) -> int:
        node_id = len(self.parent)
        self.parent.append(node_id)
        self.size.append(1)
        return node_id

    def find(self, node_id: int) -> int:
        parent = self.parent[node_id]
        if parent != node_id:
            self.parent[node_id] = self.find(parent)
        return self.parent[node_id]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.size[left_root] < self.size[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.size[left_root] += self.size[right_root]


def _component_diagnostics(
    network: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, int | None]:
    """Derive graph components from edge endpoints for QA reporting."""
    union_find = _UnionFind()
    node_ids_by_xy: dict[tuple[float, float], int] = {}
    node_xy: list[tuple[float, float]] = []
    edge_node_ids: list[tuple[int | None, int | None]] = []

    def node_id_for(xy: tuple[float, float]) -> int:
        key = (round(xy[0], 3), round(xy[1], 3))
        if key in node_ids_by_xy:
            return node_ids_by_xy[key]
        node_id = union_find.add()
        node_ids_by_xy[key] = node_id
        node_xy.append(key)
        return node_id

    for geometry in network.geometry:
        endpoints = _line_endpoints(geometry)
        if endpoints is None:
            edge_node_ids.append((None, None))
            continue
        start_id = node_id_for(endpoints[0])
        end_id = node_id_for(endpoints[1])
        union_find.union(start_id, end_id)
        edge_node_ids.append((start_id, end_id))

    if not node_xy:
        empty_nodes = gpd.GeoDataFrame(
            columns=["node_id", "component_id", "component_node_count", "geometry"],
            geometry="geometry",
            crs=network.crs,
        )
        enriched = network.copy()
        enriched["component_id"] = None
        return empty_nodes, enriched, None

    roots = [union_find.find(node_id) for node_id in range(len(node_xy))]
    root_counts = Counter(roots)
    root_order = {
        root: component_id
        for component_id, (root, _) in enumerate(
            sorted(root_counts.items(), key=lambda item: (-item[1], item[0]))
        )
    }
    component_ids = [root_order[root] for root in roots]
    component_sizes = {root_order[root]: count for root, count in root_counts.items()}

    nodes = gpd.GeoDataFrame(
        [
            {
                "node_id": node_id,
                "component_id": component_ids[node_id],
                "component_node_count": component_sizes[component_ids[node_id]],
                "geometry": Point(xy),
            }
            for node_id, xy in enumerate(node_xy)
        ],
        geometry="geometry",
        crs=network.crs,
    )

    enriched = network.copy()
    edge_component_ids: list[int | None] = []
    for edge_start_id, edge_end_id in edge_node_ids:
        if edge_start_id is None or edge_end_id is None:
            edge_component_ids.append(None)
        else:
            edge_component_ids.append(component_ids[edge_start_id])
    enriched["component_id"] = edge_component_ids
    return nodes, enriched, 0


def _nearest_row(
    frame: gpd.GeoDataFrame,
    spatial_index: Any,
    point: Point,
    *,
    max_m: float,
) -> pd.Series | None:
    if frame.empty:
        return None
    radii = sorted({radius for radius in [5.0, 20.0, 50.0, 100.0, max_m] if radius <= max_m})
    for query_radius in radii:
        possible = spatial_index.query(point.buffer(query_radius), predicate="intersects")
        if len(possible) == 0:
            continue
        candidates = frame.iloc[list(possible)].copy()
        candidates["distance_m"] = candidates.geometry.distance(point)
        candidates = candidates[candidates["distance_m"] <= query_radius]
        if not candidates.empty:
            return candidates.sort_values("distance_m").iloc[0]
    return None


def _nearest_edge_distance(
    frame: gpd.GeoDataFrame,
    point: Point,
    *,
    max_m: float = 250.0,
) -> tuple[float | None, dict[str, int]]:
    if frame.empty:
        return None, {}
    spatial_index = frame.sindex
    nearest = _nearest_row(frame, spatial_index, point, max_m=max_m)
    if nearest is None:
        return None, {}
    return round(float(nearest["distance_m"]), 1), _edge_source_summary(
        gpd.GeoDataFrame([nearest.to_dict()], geometry="geometry", crs=frame.crs)
    )


def json_nullable(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


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


def _component_gap_classification(label: str, covered: gpd.GeoDataFrame) -> str:
    if label == "void_deck" or _has_hdb_evidence(covered):
        return "hdb_void_deck_component_gap"
    if label in BRIDGE_LABELS or _has_bridge_evidence(covered):
        return "bridge_underpass_component_gap"
    return "covered_component_gap"


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
        audited["needs_model_qa"] = audited["label"].isin(COVERED_LABELS)
        audited["start_component_id"] = None
        audited["end_component_id"] = None
        audited["same_component"] = None
        audited["start_snap_node_m"] = None
        audited["end_snap_node_m"] = None
        audited["start_component_node_count"] = None
        audited["end_component_node_count"] = None
        audited["endpoint_component_gap_m"] = None
        audited["nearest_main_component_edge_m"] = None
        audited["nearest_main_component_covered_edge_m"] = None
        audited["nearest_main_component_sources"] = [{} for _ in range(len(audited))]
        audited["nearest_main_component_covered_sources"] = [{} for _ in range(len(audited))]
        audited["nearby_edge_count"] = 0
        audited["nearby_covered_edge_count"] = 0
        audited["nearest_edge_m"] = None
        audited["nearest_covered_edge_m"] = None
        audited["nearby_sources"] = [{} for _ in range(len(audited))]
        return audited

    network = network_edges.to_crs(segments.crs).reset_index(drop=True)
    component_nodes, network_with_components, main_component_id = _component_diagnostics(network)
    sindex = network.sindex
    node_sindex = component_nodes.sindex if not component_nodes.empty else None
    main_edges = (
        network_with_components[network_with_components["component_id"] == main_component_id].copy()
        if main_component_id is not None
        else network_with_components.iloc[0:0].copy()
    )
    main_covered_edges = (
        main_edges[_is_covered(main_edges)].copy() if not main_edges.empty else main_edges
    )
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
        first_coord = line.coords[0]
        last_coord = line.coords[-1]
        start_snap = (
            _nearest_row(
                component_nodes,
                node_sindex,
                Point(first_coord),
                max_m=max(search_m, 100.0),
            )
            if node_sindex is not None
            else None
        )
        end_snap = (
            _nearest_row(
                component_nodes,
                node_sindex,
                Point(last_coord),
                max_m=max(search_m, 100.0),
            )
            if node_sindex is not None
            else None
        )
        start_component = int(start_snap["component_id"]) if start_snap is not None else None
        end_component = int(end_snap["component_id"]) if end_snap is not None else None
        same_component = (
            start_component == end_component
            if start_component is not None and end_component is not None
            else None
        )
        if (
            str(segment["label"]) in COVERED_LABELS
            and same_component is False
            and (not nearby.empty or not covered.empty)
        ):
            classification = _component_gap_classification(str(segment["label"]), covered)

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
        row["start_component_id"] = start_component
        row["end_component_id"] = end_component
        row["same_component"] = same_component
        row["start_snap_node_m"] = (
            round(float(start_snap["distance_m"]), 1) if start_snap is not None else None
        )
        row["end_snap_node_m"] = (
            round(float(end_snap["distance_m"]), 1) if end_snap is not None else None
        )
        row["start_snap_x"] = (
            round(float(start_snap.geometry.x), 3) if start_snap is not None else None
        )
        row["start_snap_y"] = (
            round(float(start_snap.geometry.y), 3) if start_snap is not None else None
        )
        row["end_snap_x"] = round(float(end_snap.geometry.x), 3) if end_snap is not None else None
        row["end_snap_y"] = round(float(end_snap.geometry.y), 3) if end_snap is not None else None
        row["start_component_node_count"] = (
            int(start_snap["component_node_count"]) if start_snap is not None else None
        )
        row["end_component_node_count"] = (
            int(end_snap["component_node_count"]) if end_snap is not None else None
        )
        row["endpoint_component_gap_m"] = (
            round(float(start_snap.geometry.distance(end_snap.geometry)), 1)
            if same_component is False and start_snap is not None and end_snap is not None
            else None
        )
        nearest_main_point = line.interpolate(0.5, normalized=True)
        nearest_main_edge_m, nearest_main_sources = _nearest_edge_distance(
            main_edges, nearest_main_point
        )
        nearest_main_covered_edge_m, nearest_main_covered_sources = _nearest_edge_distance(
            main_covered_edges, nearest_main_point
        )
        row["nearest_main_component_edge_m"] = nearest_main_edge_m
        row["nearest_main_component_covered_edge_m"] = nearest_main_covered_edge_m
        row["nearest_main_component_sources"] = nearest_main_sources
        row["nearest_main_component_covered_sources"] = nearest_main_covered_sources
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
                "start_component_id": json_nullable(row.get("start_component_id")),
                "end_component_id": json_nullable(row.get("end_component_id")),
                "same_component": json_nullable(row.get("same_component")),
                "start_snap_node_m": json_nullable(row.get("start_snap_node_m")),
                "end_snap_node_m": json_nullable(row.get("end_snap_node_m")),
                "start_snap_x": json_nullable(row.get("start_snap_x")),
                "start_snap_y": json_nullable(row.get("start_snap_y")),
                "end_snap_x": json_nullable(row.get("end_snap_x")),
                "end_snap_y": json_nullable(row.get("end_snap_y")),
                "start_component_node_count": json_nullable(row.get("start_component_node_count")),
                "end_component_node_count": json_nullable(row.get("end_component_node_count")),
                "endpoint_component_gap_m": json_nullable(row.get("endpoint_component_gap_m")),
                "nearest_main_component_edge_m": json_nullable(
                    row.get("nearest_main_component_edge_m")
                ),
                "nearest_main_component_covered_edge_m": json_nullable(
                    row.get("nearest_main_component_covered_edge_m")
                ),
                "nearest_main_component_sources": row.get("nearest_main_component_sources", {}),
                "nearest_main_component_covered_sources": row.get(
                    "nearest_main_component_covered_sources", {}
                ),
                "nearby_edge_count": int(row["nearby_edge_count"]),
                "nearby_covered_edge_count": int(row["nearby_covered_edge_count"]),
                "nearest_edge_m": json_nullable(row["nearest_edge_m"]),
                "nearest_covered_edge_m": json_nullable(row["nearest_covered_edge_m"]),
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
    dict_properties = {
        "nearby_sources",
        "nearest_main_component_sources",
        "nearest_main_component_covered_sources",
    }
    for _, row in output.iterrows():
        properties = {
            key: json_nullable(value)
            for key, value in row.drop(labels=["geometry"]).items()
            if key not in dict_properties
        }
        for key in dict_properties:
            if key in row:
                properties[key] = json.dumps(row.get(key, {}), sort_keys=True)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(row.geometry),
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def component_gap_candidate_geojson(audited_segments: gpd.GeoDataFrame) -> dict[str, Any]:
    if audited_segments.empty:
        return {"type": "FeatureCollection", "features": []}

    to_wgs84 = Transformer.from_crs(audited_segments.crs, "EPSG:4326", always_xy=True)
    features = []
    component_gap_classes = {
        "covered_component_gap",
        "hdb_void_deck_component_gap",
        "bridge_underpass_component_gap",
    }
    for _, row in audited_segments.iterrows():
        if str(row.get("classification", "")) not in component_gap_classes:
            continue
        coords = [
            row.get("start_snap_x"),
            row.get("start_snap_y"),
            row.get("end_snap_x"),
            row.get("end_snap_y"),
        ]
        if any(json_nullable(value) is None for value in coords):
            continue

        candidate_line = LineString(
            [
                (float(row["start_snap_x"]), float(row["start_snap_y"])),
                (float(row["end_snap_x"]), float(row["end_snap_y"])),
            ]
        )
        if candidate_line.is_empty or candidate_line.length <= 0:
            continue
        line_wgs84 = transform(to_wgs84.transform, candidate_line)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(line_wgs84),
                "properties": {
                    "postal": row["postal"],
                    "destination": row["destination"],
                    "segment_index": int(row["segment_index"]),
                    "label": row["label"],
                    "classification": row["classification"],
                    "length_m": round(float(candidate_line.length), 1),
                    "endpoint_component_gap_m": json_nullable(row.get("endpoint_component_gap_m")),
                    "start_component_id": json_nullable(row.get("start_component_id")),
                    "end_component_id": json_nullable(row.get("end_component_id")),
                    "evidence_status": "qa_candidate_not_scoring",
                    "promotion_rule": (
                        "Promote only after source-backed review or a general tested "
                        "network-build connector rule."
                    ),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}
