from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.targeted_bundle_refresh import active_bundle_dir, load_score_index, load_score_records

SCOREABLE_STATES = {"SCORED", "SCORED_PARTIAL"}
IMPROVEMENT_FLAGS = {"total_improvement", "coverage_improvement"}


def normalize_postal(value: Any) -> str:
    return str(value).strip().zfill(6)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nested(record: dict[str, Any], *keys: str) -> Any:
    value: Any = record
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def load_candidate_records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        value = payload.get("records") or payload.get("candidate_records") or payload.get("results")
        if isinstance(value, list):
            records = value
        elif isinstance(payload.get("comparisons"), list):
            records = []
            for item in payload["comparisons"]:
                if not isinstance(item, dict):
                    continue
                after = item.get("after")
                if not isinstance(after, dict):
                    continue
                record = dict(after)
                record.setdefault("postal", item.get("postal"))
                records.append(record)
        else:
            records = []
    else:
        records = []
    return [record for record in records if isinstance(record, dict) and record.get("postal")]


def load_bundle_records(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    score_index = load_score_index(bundle_dir)
    records: dict[str, dict[str, Any]] = {}
    for shard in sorted(score_index):
        for record in load_score_records(bundle_dir, shard):
            if isinstance(record, dict) and record.get("postal"):
                records[normalize_postal(record["postal"])] = record
    return records


def best_node_summary(record: dict[str, Any]) -> dict[str, Any]:
    node = record.get("best_node")
    if not isinstance(node, dict):
        return {}
    return {
        "type": node.get("type"),
        "name": node.get("name"),
        "station": node.get("station"),
        "exit": node.get("exit"),
        "routed_m": numeric(node.get("routed_m")),
    }


def compare_record(
    active: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    total_tolerance: float,
    coverage_tolerance: float,
) -> dict[str, Any]:
    postal = normalize_postal(candidate["postal"])
    active_state = active.get("state") if active else None
    candidate_state = candidate.get("state")
    active_total = numeric(active.get("total")) if active else None
    candidate_total = numeric(candidate.get("total"))
    active_covered = numeric(nested(active or {}, "paths", "covered_ratio"))
    candidate_covered = numeric(nested(candidate, "paths", "covered_ratio"))
    active_distance = numeric(nested(active or {}, "paths", "sheltered_m"))
    candidate_distance = numeric(nested(candidate, "paths", "sheltered_m"))

    total_delta = (
        round(candidate_total - active_total, 3)
        if active_total is not None and candidate_total is not None
        else None
    )
    covered_delta = (
        round(candidate_covered - active_covered, 4)
        if active_covered is not None and candidate_covered is not None
        else None
    )
    distance_delta = (
        round(candidate_distance - active_distance, 3)
        if active_distance is not None and candidate_distance is not None
        else None
    )

    flags: list[str] = []
    if active is None:
        flags.append("missing_active_record")
    if active_state in SCOREABLE_STATES and candidate_state not in SCOREABLE_STATES:
        flags.append("state_regression")
    if total_delta is not None and total_delta < -total_tolerance:
        flags.append("total_regression")
    if covered_delta is not None and covered_delta < -coverage_tolerance:
        flags.append("coverage_regression")
    if total_delta is not None and total_delta > total_tolerance:
        flags.append("total_improvement")
    if covered_delta is not None and covered_delta > coverage_tolerance:
        flags.append("coverage_improvement")

    active_best = best_node_summary(active or {})
    candidate_best = best_node_summary(candidate)
    if active_best and candidate_best and active_best != candidate_best:
        flags.append("best_node_changed")

    blocking = any(
        flag
        in {
            "missing_active_record",
            "state_regression",
            "total_regression",
            "coverage_regression",
        }
        for flag in flags
    )

    return {
        "postal": postal,
        "active": {
            "state": active_state,
            "total": active_total,
            "covered_ratio": active_covered,
            "sheltered_m": active_distance,
            "best_node": active_best,
        },
        "candidate": {
            "state": candidate_state,
            "total": candidate_total,
            "covered_ratio": candidate_covered,
            "sheltered_m": candidate_distance,
            "best_node": candidate_best,
        },
        "delta": {
            "total": total_delta,
            "covered_ratio": covered_delta,
            "sheltered_m": distance_delta,
        },
        "flags": flags or ["unchanged_or_within_tolerance"],
        "blocking": blocking,
    }


def compare_records(
    active_records: dict[str, dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    total_tolerance: float,
    coverage_tolerance: float,
) -> dict[str, Any]:
    comparisons = [
        compare_record(
            active_records.get(normalize_postal(candidate["postal"])),
            candidate,
            total_tolerance=total_tolerance,
            coverage_tolerance=coverage_tolerance,
        )
        for candidate in candidate_records
    ]
    flag_counts = Counter(flag for item in comparisons for flag in item["flags"])
    blocking_count = sum(1 for item in comparisons if item["blocking"])
    safe_improvements = [
        item
        for item in comparisons
        if not item["blocking"] and any(flag in IMPROVEMENT_FLAGS for flag in item["flags"])
    ]
    safe_unchanged = [
        item
        for item in comparisons
        if not item["blocking"] and item["flags"] == ["unchanged_or_within_tolerance"]
    ]
    blocked = [item for item in comparisons if item["blocking"]]
    return {
        "ok": blocking_count == 0,
        "candidate_count": len(candidate_records),
        "compared_count": len(comparisons),
        "blocking_count": blocking_count,
        "safe_improvement_count": len(safe_improvements),
        "safe_improvement_postals": [item["postal"] for item in safe_improvements],
        "safe_unchanged_postals": [item["postal"] for item in safe_unchanged],
        "blocked_postals": [item["postal"] for item in blocked],
        "flag_counts": dict(sorted(flag_counts.items())),
        "promotion_recommendation": (
            "safe_to_promote_targeted_records"
            if blocking_count == 0
            else (
                "promote_safe_improvements_only"
                if safe_improvements
                else "hold_for_review_do_not_promote_wholesale"
            )
        ),
        "comparisons": comparisons,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare a targeted score report against the active static bundle."
    )
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--bundle-dir", type=Path, default=None)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--safe-postals-output",
        type=Path,
        default=None,
        help="Optional text output containing only non-blocking materially improved postals.",
    )
    parser.add_argument("--total-tolerance", type=float, default=0.5)
    parser.add_argument("--coverage-tolerance", type=float, default=0.02)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle_dir = args.bundle_dir or active_bundle_dir()
    active_records = load_bundle_records(bundle_dir)
    candidate_records = load_candidate_records(args.candidate)
    report = compare_records(
        active_records,
        candidate_records,
        total_tolerance=args.total_tolerance,
        coverage_tolerance=args.coverage_tolerance,
    )
    report["inputs"] = {
        "candidate": str(args.candidate),
        "bundle_dir": str(bundle_dir),
        "total_tolerance": args.total_tolerance,
        "coverage_tolerance": args.coverage_tolerance,
    }
    write_json(args.output, report)
    if args.safe_postals_output is not None:
        args.safe_postals_output.parent.mkdir(parents=True, exist_ok=True)
        args.safe_postals_output.write_text(
            "\n".join(report["safe_improvement_postals"]) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "ok",
                    "compared_count",
                    "blocking_count",
                    "safe_improvement_count",
                    "safe_improvement_postals",
                    "blocked_postals",
                    "flag_counts",
                    "promotion_recommendation",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
