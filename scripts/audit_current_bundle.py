# mypy: ignore-errors
# ruff: noqa: E402, RUF100

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.scoring import NO_TRANSIT_IN_RANGE, NOT_YET_SCORED, score_transit_access
from pipeline.scoring_integration import (
    CandidateNode,
    ScoringContext,
    load_postal_universe_points,
    load_scoring_context,
    nearest_graph_node,
    select_bus_stop_candidates,
    select_mrt_exit_candidates,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "qa" / "current_bundle_state_report.json"
DEFAULT_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)
DEFAULT_NETWORK = PROJECT_ROOT / "processed" / "network_island.parquet"


def read_json(path: Path) -> Any:
    if path.is_file():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    gz_path = path.with_name(f"{path.name}.gz")
    if gz_path.is_file():
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError(path)


def active_bundle_dir() -> Path:
    bundle_config = read_json(PROJECT_ROOT / "web" / "data-bundle.json")
    bundle = str(bundle_config["bundle"])
    return PROJECT_ROOT / "web" / "public" / "data" / bundle


def area_from_shard(shard: str) -> str:
    return shard.split("_PART_")[0]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, round((len(values) - 1) * pct))
    return sorted(values)[index]


def value_distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min": round(min(values), 1) if values else None,
        "p50": round(statistics.median(values), 1) if values else None,
        "p95": round(percentile(values, 0.95), 1) if values else None,
        "max": round(max(values), 1) if values else None,
    }


def load_bundle_records(bundle_dir: Path) -> list[dict[str, Any]]:
    index = read_json(bundle_dir / "scores" / "index.json")
    records: list[dict[str, Any]] = []
    for shard in sorted(index):
        shard_records = read_json(bundle_dir / "scores" / f"{shard}.json")
        for record in shard_records:
            record["_score_shard"] = shard
            record["_area"] = area_from_shard(shard)
            records.append(record)
    return records


def no_transit_shape(record: dict[str, Any]) -> str:
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    node_set = (
        provenance.get("transit_node_set")
        if isinstance(provenance.get("transit_node_set"), dict)
        else {}
    )
    bus = (
        provenance.get("bus_connectivity")
        if isinstance(provenance.get("bus_connectivity"), dict)
        else {}
    )
    return str(
        (
            node_set.get("mrt_lrt_exit_candidates"),
            node_set.get("bus_stop_candidates_direct"),
            bus.get("routed_stop_count"),
            bus.get("nearest_routed_m"),
        )
    )


def source_tuple(record: dict[str, Any]) -> tuple[str, ...]:
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    sources = provenance.get("sources") if isinstance(provenance.get("sources"), list) else []
    return tuple(sorted(str(source) for source in sources))


def sample_postals(records: list[dict[str, Any]], replay_limit: int) -> list[str]:
    if replay_limit <= 0:
        return []

    no_transit = [record for record in records if record.get("state") == NO_TRANSIT_IN_RANGE]
    with_bus = [
        record
        for record in no_transit
        if (
            isinstance(record.get("provenance"), dict)
            and record["provenance"]
            .get("transit_node_set", {})
            .get("bus_stop_candidates_direct", 0)
            > 0
        )
    ]
    without_bus = [
        record
        for record in no_transit
        if (
            isinstance(record.get("provenance"), dict)
            and record["provenance"]
            .get("transit_node_set", {})
            .get("bus_stop_candidates_direct", 0)
            == 0
        )
    ]
    top_areas = [
        area for area, _ in Counter(record["_area"] for record in no_transit).most_common(8)
    ]

    selected: list[str] = []

    def add(record: dict[str, Any]) -> None:
        postal = str(record["postal"])
        if postal not in selected:
            selected.append(postal)

    for area in top_areas:
        for record in no_transit:
            if record["_area"] == area:
                add(record)
                if len(selected) >= replay_limit:
                    return selected
                break
    for bucket in [with_bus, without_bus]:
        step = max(1, len(bucket) // max(1, replay_limit // 2))
        for record in bucket[::step]:
            add(record)
            if len(selected) >= replay_limit:
                return selected
    for record in no_transit:
        add(record)
        if len(selected) >= replay_limit:
            return selected
    return selected[:replay_limit]


def candidate_label(candidate: CandidateNode) -> dict[str, Any]:
    return {
        "type": candidate.node_type,
        "name": candidate.name,
        "straight_line_m": round(candidate.straight_line_m, 1),
        "snap_distance_m": round(candidate.snap_distance_m, 1),
        "expected_wait_min": (
            round(candidate.expected_wait_min, 3)
            if candidate.expected_wait_min is not None
            else None
        ),
    }


def diagnose_postal(
    postal: str,
    postal_rows: pd.DataFrame,
    context: ScoringContext,
) -> dict[str, Any]:
    rows = postal_rows[postal_rows["postal_code"].astype(str).str.zfill(6) == postal]
    if rows.empty:
        return {"postal": postal, "classification": "missing_from_universe"}

    row = rows.iloc[0]
    postal_point = row.geometry
    origin_node, origin_snap_m = nearest_graph_node(postal_point, context.nodes, context.node_xy)
    bus_radius_m = float(
        context.params.get("bus_connectivity", {}).get("straight_line_candidate_m", 300.0)
    )
    mrt_candidates = select_mrt_exit_candidates(
        postal_point, context.mrt_exits_gdf, context.nodes, context.node_xy
    )
    bus_candidates = select_bus_stop_candidates(postal_point, context.bus_index, bus_radius_m)
    candidates = mrt_candidates + bus_candidates
    if not candidates:
        return {
            "postal": postal,
            "classification": "no_candidate_nodes",
            "origin_snap_m": round(origin_snap_m, 1),
            "mrt_candidates": 0,
            "bus_candidates": 0,
        }

    destinations: list[tuple[float, float]] = []
    candidate_by_destination: dict[tuple[float, float], list[CandidateNode]] = {}
    for candidate in candidates:
        if candidate.graph_node not in candidate_by_destination:
            destinations.append(candidate.graph_node)
            candidate_by_destination[candidate.graph_node] = []
        candidate_by_destination[candidate.graph_node].append(candidate)

    route_results = context.routing_graph.route(
        {origin_node: destinations},
        float(context.params["shelter_lambda"]),
        float(context.params["detour_budget"]),
        include_geometry=False,
    )
    zero_credit_m = float(context.params["transit_access"]["zero_credit_m"])
    reachable_candidates: list[dict[str, Any]] = []
    in_access_range: list[dict[str, Any]] = []
    for route_result in route_results:
        routed_m = float(route_result["shortest_length_m"])
        access_score = score_transit_access(routed_m, context.params["transit_access"])
        for candidate in candidate_by_destination.get(route_result["destination"], []):
            item = {
                **candidate_label(candidate),
                "routed_m": round(routed_m, 1),
                "access_score": access_score,
            }
            reachable_candidates.append(item)
            if routed_m <= zero_credit_m:
                in_access_range.append(item)

    candidate_components = {
        context.routing_graph.component_membership[context.routing_graph.node_map[destination]]
        for destination in destinations
        if destination in context.routing_graph.node_map
    }
    origin_component = context.routing_graph.component_membership[
        context.routing_graph.node_map[origin_node]
    ]
    nearest_candidate_component_snap_m: float | None = None
    if candidate_components and origin_component not in candidate_components:
        component_mask = np.asarray(
            [
                context.routing_graph.component_membership[context.routing_graph.node_map[node]]
                in candidate_components
                for node in context.nodes
            ],
            dtype=bool,
        )
        candidate_component_xy = context.node_xy[component_mask]
        if len(candidate_component_xy):
            point_xy = np.asarray([postal_point.x, postal_point.y], dtype=float)
            deltas = candidate_component_xy - point_xy
            nearest_candidate_component_snap_m = float(
                np.sqrt(np.einsum("ij,ij->i", deltas, deltas)).min()
            )

    if not route_results:
        classification = "candidate_graph_disconnected"
    elif not in_access_range:
        classification = "all_candidates_beyond_access_range"
    else:
        classification = "should_have_scored_or_bundle_stale"

    nearest = sorted(reachable_candidates, key=lambda item: float(item["routed_m"]))[:6]
    return {
        "postal": postal,
        "classification": classification,
        "origin_snap_m": round(origin_snap_m, 1),
        "origin_component_reaches_candidate": origin_component in candidate_components,
        "nearest_candidate_component_snap_m": (
            round(nearest_candidate_component_snap_m, 1)
            if nearest_candidate_component_snap_m is not None
            else None
        ),
        "mrt_candidates": len(mrt_candidates),
        "bus_candidates": len(bus_candidates),
        "route_results": len(route_results),
        "reachable_candidates": len(reachable_candidates),
        "within_access_range": len(in_access_range),
        "nearest_reachable": nearest,
    }


def build_report(
    bundle_dir: Path,
    replay_limit: int,
    network_path: Path,
    postal_universe_path: Path,
) -> dict[str, Any]:
    manifest = read_json(bundle_dir / "manifest.json")
    records = load_bundle_records(bundle_dir)
    state_counts = Counter(str(record.get("state")) for record in records)
    no_transit = [record for record in records if record.get("state") == NO_TRANSIT_IN_RANGE]
    not_yet = [record for record in records if record.get("state") == NOT_YET_SCORED]
    scored = [record for record in records if record.get("state") == "SCORED"]

    origin_snaps: list[float] = []
    bus_direct_counts: list[int] = []
    mrt_candidate_counts: list[int] = []
    for record in no_transit:
        provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        node_set = (
            provenance.get("transit_node_set")
            if isinstance(provenance.get("transit_node_set"), dict)
            else {}
        )
        if isinstance(provenance.get("origin_snap_distance_m"), int | float):
            origin_snaps.append(float(provenance["origin_snap_distance_m"]))
        if isinstance(node_set.get("bus_stop_candidates_direct"), int | float):
            bus_direct_counts.append(int(node_set["bus_stop_candidates_direct"]))
        if isinstance(node_set.get("mrt_lrt_exit_candidates"), int | float):
            mrt_candidate_counts.append(int(node_set["mrt_lrt_exit_candidates"]))

    replay_postals = sample_postals(records, replay_limit) if replay_limit > 0 else []
    replay: list[dict[str, Any]] = []
    if replay_postals:
        context = load_scoring_context(
            network_path=network_path,
            postal_universe_path=postal_universe_path,
        )
        postal_rows = load_postal_universe_points(postal_universe_path, postal_codes=replay_postals)
        for postal in replay_postals:
            replay.append(diagnose_postal(postal, postal_rows, context))
    disconnected_replay = [
        item for item in replay if item.get("classification") == "candidate_graph_disconnected"
    ]
    resnap_distances = [
        float(item["nearest_candidate_component_snap_m"])
        for item in disconnected_replay
        if isinstance(item.get("nearest_candidate_component_snap_m"), int | float)
    ]

    return {
        "bundle": bundle_dir.name,
        "manifest_record_count": manifest.get("provenance", {}).get("record_count"),
        "state_counts": dict(sorted(state_counts.items())),
        "not_yet_scored": {
            "count": len(not_yet),
            "reason_counts": Counter(
                str(
                    (record.get("provenance") or {}).get("reason")
                    if isinstance(record.get("provenance"), dict)
                    else None
                )
                for record in not_yet
            ).most_common(),
            "source_counts": Counter(source_tuple(record) for record in not_yet).most_common(),
            "samples": [
                {
                    "postal": record["postal"],
                    "area": record["_area"],
                    "provenance": record.get("provenance"),
                }
                for record in not_yet[:20]
            ],
        },
        "no_transit_in_range": {
            "count": len(no_transit),
            "area_counts_top20": Counter(record["_area"] for record in no_transit).most_common(20),
            "reason_shape_top20": Counter(
                no_transit_shape(record) for record in no_transit
            ).most_common(20),
            "origin_snap_m": value_distribution(origin_snaps),
            "bus_direct_candidates": {
                "count": len(bus_direct_counts),
                "zero": sum(1 for value in bus_direct_counts if value == 0),
                "gt0": sum(1 for value in bus_direct_counts if value > 0),
                "max": max(bus_direct_counts) if bus_direct_counts else None,
            },
            "mrt_candidates": {
                "count": len(mrt_candidate_counts),
                "zero": sum(1 for value in mrt_candidate_counts if value == 0),
                "gt0": sum(1 for value in mrt_candidate_counts if value > 0),
                "max": max(mrt_candidate_counts) if mrt_candidate_counts else None,
            },
            "samples": [
                {
                    "postal": record["postal"],
                    "area": record["_area"],
                    "provenance": record.get("provenance"),
                }
                for record in no_transit[:20]
            ],
            "replay_sample_count": len(replay),
            "replay_classification_counts": Counter(
                item["classification"] for item in replay
            ).most_common(),
            "replay_disconnected_candidate_component_snap_m": {
                **value_distribution(resnap_distances),
                "within_25m": sum(1 for value in resnap_distances if value <= 25.0),
                "within_50m": sum(1 for value in resnap_distances if value <= 50.0),
                "within_75m": sum(1 for value in resnap_distances if value <= 75.0),
            },
            "replay_samples": replay,
        },
        "scored": {
            "count": len(scored),
            "total_score_distribution": value_distribution(
                [
                    float(record["total"])
                    for record in scored
                    if isinstance(record.get("total"), int | float)
                ]
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast audit of the current deployed score bundle.")
    parser.add_argument("--bundle-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replay-limit", type=int, default=30)
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK)
    parser.add_argument("--postal-universe", type=Path, default=DEFAULT_UNIVERSE)
    args = parser.parse_args()

    bundle_dir = args.bundle_dir if args.bundle_dir is not None else active_bundle_dir()
    report = build_report(
        bundle_dir=bundle_dir,
        replay_limit=max(0, int(args.replay_limit)),
        network_path=args.network,
        postal_universe_path=args.postal_universe,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps({"ok": True, "output": str(args.output), "bundle": report["bundle"]}, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
