# mypy: ignore-errors
# ruff: noqa: E402, RUF100

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.scoring import NO_TRANSIT_IN_RANGE
from pipeline.scoring_integration import score_postals

DEFAULT_OUTPUT = PROJECT_ROOT / "qa" / "partial_resnap_rescore_sample.json"
DEFAULT_NETWORK = PROJECT_ROOT / "processed" / "network_island.parquet"
DEFAULT_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)
DEFAULT_AREAS = ["ANG_MO_KIO", "HOUGANG", "CLEMENTI", "BUKIT_TIMAH"]


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
    return PROJECT_ROOT / "web" / "public" / "data" / str(bundle_config["bundle"])


def area_from_shard(shard: str) -> str:
    return shard.split("_PART_")[0]


def load_bundle_records(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    index = read_json(bundle_dir / "scores" / "index.json")
    records: dict[str, dict[str, Any]] = {}
    for shard in sorted(index):
        shard_records = read_json(bundle_dir / "scores" / f"{shard}.json")
        for record in shard_records:
            postal = str(record["postal"])
            records[postal] = {
                **record,
                "_area": area_from_shard(shard),
                "_score_shard": shard,
            }
    return records


def bus_direct_count(record: dict[str, Any]) -> int:
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    node_set = (
        provenance.get("transit_node_set")
        if isinstance(provenance.get("transit_node_set"), dict)
        else {}
    )
    value = node_set.get("bus_stop_candidates_direct", 0)
    return int(value) if isinstance(value, int | float) else 0


def select_no_transit_postals(
    records: dict[str, dict[str, Any]],
    areas: list[str],
    per_area: int,
    extra_postals: list[str],
    limit: int,
) -> list[str]:
    selected: list[str] = []

    def add(postal: str) -> None:
        if postal in records and postal not in selected:
            selected.append(postal)

    for postal in extra_postals:
        add(str(postal).zfill(6))

    no_transit = [
        record
        for record in sorted(records.values(), key=lambda item: str(item["postal"]))
        if record.get("state") == NO_TRANSIT_IN_RANGE
    ]
    for area in areas:
        area_records = [record for record in no_transit if record.get("_area") == area]
        area_records.sort(
            key=lambda item: (
                bus_direct_count(item) == 0,
                str(item["postal"]),
            )
        )
        for record in area_records[:per_area]:
            add(str(record["postal"]))

    for record in no_transit:
        if len(selected) >= limit:
            break
        add(str(record["postal"]))
    return selected[:limit]


def compact_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    return {
        "state": record.get("state"),
        "total": record.get("total"),
        "best_node": record.get("best_node"),
        "paths": record.get("paths"),
        "subscores": record.get("subscores"),
        "origin_snap_distance_m": provenance.get("origin_snap_distance_m"),
        "origin_resnap": provenance.get("origin_resnap"),
        "routing_diagnostics": provenance.get("routing_diagnostics"),
        "transit_node_set": provenance.get("transit_node_set"),
    }


def build_report(
    bundle_dir: Path,
    network_path: Path,
    postal_universe_path: Path,
    areas: list[str],
    per_area: int,
    extra_postals: list[str],
    limit: int,
) -> dict[str, Any]:
    records = load_bundle_records(bundle_dir)
    selected_postals = select_no_transit_postals(records, areas, per_area, extra_postals, limit)
    rescored = score_postals(
        postal_codes=selected_postals,
        include_geometry=False,
        network_path=network_path,
        postal_universe_path=postal_universe_path,
    )
    rescored_by_postal = {str(record["postal"]): record for record in rescored}

    comparisons = []
    for postal in selected_postals:
        before = records.get(postal)
        after = rescored_by_postal.get(postal)
        comparisons.append(
            {
                "postal": postal,
                "area": before.get("_area") if before else None,
                "before": compact_record(before),
                "after": compact_record(after),
                "state_changed": (before or {}).get("state") != (after or {}).get("state"),
            }
        )

    before_counts = Counter(str(item["before"]["state"]) for item in comparisons if item["before"])
    after_counts = Counter(str(item["after"]["state"]) for item in comparisons if item["after"])
    converted = [
        item["postal"]
        for item in comparisons
        if item["before"]
        and item["after"]
        and item["before"]["state"] == NO_TRANSIT_IN_RANGE
        and item["after"]["state"] != NO_TRANSIT_IN_RANGE
    ]

    return {
        "bundle": bundle_dir.name,
        "network": str(network_path),
        "postal_universe": str(postal_universe_path),
        "areas": areas,
        "per_area": per_area,
        "selected_count": len(selected_postals),
        "before_state_counts": dict(sorted(before_counts.items())),
        "after_state_counts": dict(sorted(after_counts.items())),
        "converted_from_no_transit": converted,
        "converted_count": len(converted),
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded before/after rescore for resnap QA.")
    parser.add_argument("--bundle-dir", type=Path)
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK)
    parser.add_argument("--postal-universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--area", action="append", dest="areas")
    parser.add_argument("--per-area", type=int, default=6)
    parser.add_argument("--postal", action="append", dest="postals", default=[])
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args()

    report = build_report(
        bundle_dir=args.bundle_dir if args.bundle_dir else active_bundle_dir(),
        network_path=args.network,
        postal_universe_path=args.postal_universe,
        areas=args.areas if args.areas else DEFAULT_AREAS,
        per_area=max(0, int(args.per_area)),
        extra_postals=args.postals,
        limit=max(1, int(args.limit)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "selected_count": report["selected_count"],
                "converted_count": report["converted_count"],
                "after_state_counts": report["after_state_counts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
