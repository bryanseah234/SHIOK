# mypy: ignore-errors
# ruff: noqa: E402

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_COMPONENT_AUDIT = (
    PROJECT_ROOT
    / "qa"
    / "route_feedback_component_gap_source_audit_amk_20260801_osm_covered_values_network.json"
)
DEFAULT_FEEDBACK_AUDIT = (
    PROJECT_ROOT / "qa" / "route_feedback_algorithm_qa_amk_20260801_osm_covered_values_network.json"
)
DEFAULT_POSTALS = ["560231", "560234", "560225"]
DEFAULT_APPROVED_CORRECTIONS = PROJECT_ROOT / "data" / "audited_shelter_corrections.geojson"


def read_json(path: Path) -> Any:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    gz_path = path.with_name(f"{path.name}.gz")
    if gz_path.is_file():
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError(path)


def active_bundle_dir() -> Path:
    config = read_json(PROJECT_ROOT / "web" / "data-bundle.json")
    return PROJECT_ROOT / "web" / "public" / "data" / str(config["bundle"])


def normalize_postal(value: str) -> str:
    return str(value).strip().zfill(6)


def safe_id_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")


def candidate_audit_id(candidate: dict[str, Any]) -> str:
    explicit = str(candidate.get("audit_id") or "").strip()
    if explicit:
        return explicit
    postal = normalize_postal(str(candidate.get("postal", "")))
    segment_index = candidate.get("segment_index", -1)
    classification = str(candidate.get("candidate_classification", ""))
    return f"feedback-{safe_id_text(postal)}-segment-{segment_index}-{safe_id_text(classification)}"


def find_score_record(bundle_dir: Path, postal: str) -> dict[str, Any] | None:
    score_index = read_json(bundle_dir / "scores" / "index.json")
    postal = normalize_postal(postal)
    for shard, postals in score_index.items():
        if postal not in {normalize_postal(str(item)) for item in postals}:
            continue
        records = read_json(bundle_dir / "scores" / f"{shard}.json")
        for record in records:
            if normalize_postal(str(record.get("postal"))) == postal:
                return record
    return None


def find_geom_record(bundle_dir: Path, postal: str) -> dict[str, Any] | None:
    postal = normalize_postal(postal)
    postal_index_path = bundle_dir / "geom" / "postal-index.json"
    if (
        not postal_index_path.is_file()
        and not postal_index_path.with_name("postal-index.json.gz").is_file()
    ):
        return None
    postal_index = read_json(postal_index_path)
    shard = postal_index.get(postal)
    if not shard:
        return None
    records = read_json(bundle_dir / "geom" / "h3" / f"{shard}.json")
    for record in records:
        if normalize_postal(str(record.get("postal"))) == postal:
            return record
    return None


def compact_route_option(option: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(option, dict):
        return None
    paths = option.get("paths") if isinstance(option.get("paths"), dict) else {}
    best_node = option.get("best_node") if isinstance(option.get("best_node"), dict) else {}
    return {
        "state": option.get("state"),
        "total": option.get("total"),
        "best_node": (
            {
                "type": best_node.get("type"),
                "name": best_node.get("name"),
                "routed_m": best_node.get("routed_m"),
                "straight_line_m": best_node.get("straight_line_m"),
            }
            if best_node
            else None
        ),
        "paths": (
            {
                "sheltered_m": paths.get("sheltered_m"),
                "shortest_m": paths.get("shortest_m"),
                "covered_ratio": paths.get("covered_ratio"),
                "shortest_covered_ratio": paths.get("shortest_covered_ratio"),
                "shade_ratio": paths.get("shade_ratio"),
                "routing_type": paths.get("routing_type"),
            }
            if paths
            else None
        ),
    }


def score_summary(bundle_dir: Path, postals: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for postal in postals:
        record = find_score_record(bundle_dir, postal)
        if record is None:
            summary[postal] = {"missing": True}
            continue
        route_options = (
            record.get("route_options") if isinstance(record.get("route_options"), dict) else {}
        )
        summary[postal] = {
            "state": record.get("state"),
            "total": record.get("total"),
            "best_transit": compact_route_option(record),
            "mrt_lrt": compact_route_option(route_options.get("mrt_lrt")),
            "bus": compact_route_option(route_options.get("bus")),
        }
    return summary


def route_segment_source_summary(option: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(option, dict):
        return None
    route_segments = option.get("route_segments")
    if not isinstance(route_segments, dict):
        return None
    output: dict[str, Any] = {}
    for route_key in ["shortest", "sheltered"]:
        segments = route_segments.get(route_key)
        if not isinstance(segments, list):
            continue
        source_lengths: dict[str, float] = defaultdict(float)
        covered_m = 0.0
        exposed_m = 0.0
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            length_m = float(segment.get("len_m") or 0.0)
            source = str(
                segment.get("source_class")
                or segment.get("source_layer")
                or ("covered_unknown" if segment.get("is_covered") else "exposed")
            )
            source_lengths[source] += length_m
            if segment.get("is_covered"):
                covered_m += length_m
            else:
                exposed_m += length_m
        output[route_key] = {
            "segment_count": len(segments),
            "covered_m": round(covered_m, 1),
            "exposed_m": round(exposed_m, 1),
            "source_lengths_m": {
                source: round(length, 1) for source, length in sorted(source_lengths.items())
            },
        }
    return output or None


def route_gap_summary(option: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(option, dict):
        return None
    gaps = option.get("exposure_gaps")
    if not isinstance(gaps, list):
        gaps = []
    compact_gaps = [
        {
            "len_m": float(gap.get("len_m") or 0.0),
            "label": gap.get("label"),
        }
        for gap in gaps
        if isinstance(gap, dict)
    ]
    compact_gaps.sort(key=lambda gap: gap["len_m"], reverse=True)
    return {
        "gap_count": len(compact_gaps),
        "largest_gap_m": round(compact_gaps[0]["len_m"], 1) if compact_gaps else 0.0,
        "largest_gap_label": compact_gaps[0]["label"] if compact_gaps else None,
        "gaps": [
            {"len_m": round(gap["len_m"], 1), "label": gap["label"]} for gap in compact_gaps[:5]
        ],
    }


def geom_route_summary(bundle_dir: Path, postals: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for postal in postals:
        geom = find_geom_record(bundle_dir, postal)
        if geom is None:
            output[postal] = {"missing": True}
            continue
        route_options = (
            geom.get("route_options") if isinstance(geom.get("route_options"), dict) else {}
        )
        output[postal] = {}
        for mode in ["best_transit", "mrt_lrt", "bus"]:
            option = route_options.get(mode) if mode != "best_transit" else geom
            output[postal][mode] = {
                "route_segments": route_segment_source_summary(option),
                "exposure_gaps": route_gap_summary(option),
            }
    return output


def mrt_gap_signals(scores: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    signals: dict[str, Any] = {}
    for postal, score in scores.items():
        mrt_score = score.get("mrt_lrt") if isinstance(score, dict) else None
        best_score = score.get("best_transit") if isinstance(score, dict) else None
        mrt_paths = mrt_score.get("paths") if isinstance(mrt_score, dict) else {}
        best_paths = best_score.get("paths") if isinstance(best_score, dict) else {}
        geom_modes = geometry.get(postal) if isinstance(geometry.get(postal), dict) else {}
        mrt_geom = geom_modes.get("mrt_lrt") if isinstance(geom_modes, dict) else {}
        mrt_gaps = mrt_geom.get("exposure_gaps") if isinstance(mrt_geom, dict) else {}
        largest_gap_m = float((mrt_gaps or {}).get("largest_gap_m") or 0.0)
        mrt_covered = mrt_paths.get("covered_ratio") if isinstance(mrt_paths, dict) else None
        best_covered = best_paths.get("covered_ratio") if isinstance(best_paths, dict) else None
        signals[postal] = {
            "mrt_lrt_covered_ratio": mrt_covered,
            "best_transit_covered_ratio": best_covered,
            "covered_ratio_gap_vs_best": (
                round(float(best_covered) - float(mrt_covered), 3)
                if isinstance(best_covered, int | float) and isinstance(mrt_covered, int | float)
                else None
            ),
            "largest_mrt_exposed_gap_m": largest_gap_m,
            "largest_mrt_exposed_gap_label": (mrt_gaps or {}).get("largest_gap_label"),
            "mrt_specific_false_negative_signal": (
                isinstance(mrt_covered, int | float)
                and float(mrt_covered) < 0.6
                and largest_gap_m >= 100.0
            ),
        }
    return signals


def connector_summary(component_audit: dict[str, Any], postals: list[str]) -> dict[str, Any]:
    wanted = {normalize_postal(postal) for postal in postals}
    candidates = [
        candidate
        for candidate in component_audit.get("candidates", [])
        if normalize_postal(str(candidate.get("postal"))) in wanted
    ]
    by_postal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_postal[normalize_postal(str(candidate.get("postal")))].append(
            {
                "audit_id": candidate_audit_id(candidate),
                "segment_index": candidate.get("segment_index"),
                "label": candidate.get("label"),
                "length_m": candidate.get("length_m"),
                "promotion_status": candidate.get("promotion_status"),
                "candidate_classification": candidate.get("candidate_classification"),
                "covered_overlap_ratio": candidate.get("covered_overlap_ratio"),
                "hdb_overlap_ratio": candidate.get("hdb_overlap_ratio"),
                "osm_shelter_overlap_ratio": candidate.get("osm_shelter_overlap_ratio"),
                "official_shelter_overlap_ratio": candidate.get("official_shelter_overlap_ratio"),
            }
        )
    return {
        "candidate_count": len(candidates),
        "promotion_status_counts": dict(
            Counter(str(item.get("promotion_status")) for item in candidates)
        ),
        "classification_counts": dict(
            Counter(str(item.get("candidate_classification")) for item in candidates)
        ),
        "by_postal": dict(sorted(by_postal.items())),
    }


def feedback_summary(feedback_audit: dict[str, Any], postals: list[str]) -> dict[str, Any]:
    wanted = {normalize_postal(postal) for postal in postals}
    segments = [
        segment
        for segment in feedback_audit.get("segments", [])
        if normalize_postal(str(segment.get("postal"))) in wanted
    ]
    by_postal: dict[str, Counter[str]] = defaultdict(Counter)
    for segment in segments:
        by_postal[normalize_postal(str(segment.get("postal")))].update(
            [str(segment.get("classification"))]
        )
    return {
        "segment_count": len(segments),
        "classification_counts": dict(
            Counter(str(item.get("classification")) for item in segments)
        ),
        "by_postal": {postal: dict(counter) for postal, counter in sorted(by_postal.items())},
    }


def approved_correction_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    payload = read_json(path)
    ids: set[str] = set()
    for feature in payload.get("features", []):
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        if props.get("status") != "approved":
            continue
        audit_id = str(props.get("audit_id") or props.get("id") or "").strip()
        if audit_id:
            ids.add(audit_id)
    return ids


def build_summary(
    bundle_dir: Path,
    component_audit_path: Path,
    feedback_audit_path: Path,
    postals: list[str],
    approved_corrections_path: Path | None = None,
) -> dict[str, Any]:
    normalized = [normalize_postal(postal) for postal in postals]
    component_audit = read_json(component_audit_path)
    feedback_audit = read_json(feedback_audit_path)
    connectors = connector_summary(component_audit, normalized)
    approved_ids = (
        approved_correction_ids(approved_corrections_path) if approved_corrections_path else set()
    )
    review_ready = connectors["promotion_status_counts"].get("review_ready_not_scoring", 0)
    approved_review_ready = sum(
        1
        for items in connectors["by_postal"].values()
        for item in items
        if item.get("promotion_status") == "review_ready_not_scoring"
        and str(item.get("audit_id") or "") in approved_ids
    )
    scores = score_summary(bundle_dir, normalized)
    geometry = geom_route_summary(bundle_dir, normalized)
    return {
        "ok": True,
        "bundle": bundle_dir.name,
        "postals": normalized,
        "scores": scores,
        "route_geometry": geometry,
        "mrt_gap_signals": mrt_gap_signals(scores, geometry),
        "feedback_segments": feedback_summary(feedback_audit, normalized),
        "connector_candidates": connectors,
        "conclusion": {
            "score_override_used": False,
            "approved_source_backed_corrections": approved_review_ready,
            "ready_for_owner_review": max(0, review_ready - approved_review_ready),
            "blocked_without_more_source_evidence": connectors["promotion_status_counts"].get(
                "blocked_insufficient_source_overlap_not_scoring", 0
            ),
        },
    }


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    def fmt(value: Any) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.3g}"
        return str(value)

    lines = [
        "# Mayflower Route QA Summary",
        "",
        f"Bundle: `{summary['bundle']}`",
        "",
        "## Route Scores",
    ]
    for postal, score in summary["scores"].items():
        lines.append(f"- `{postal}`: state `{score.get('state')}`, total `{score.get('total')}`")
        for mode in ["best_transit", "mrt_lrt", "bus"]:
            option = score.get(mode)
            if not option:
                continue
            node = option.get("best_node") or {}
            paths = option.get("paths") or {}
            lines.append(
                "  - "
                f"{mode}: `{option.get('state')}`, total `{option.get('total')}`, "
                f"node `{node.get('name')}`, distance `{paths.get('sheltered_m')}`, "
                f"covered `{paths.get('covered_ratio')}`"
            )
    lines.extend(["", "## Active MRT Gap Signals"])
    for postal, signal in sorted(summary.get("mrt_gap_signals", {}).items()):
        lines.append(
            f"- `{postal}`: MRT covered `{fmt(signal.get('mrt_lrt_covered_ratio'))}`, "
            f"best covered `{fmt(signal.get('best_transit_covered_ratio'))}`, "
            f"largest MRT exposed gap `{fmt(signal.get('largest_mrt_exposed_gap_m'))}` m, "
            f"signal `{signal.get('mrt_specific_false_negative_signal')}`"
        )

    lines.extend(["", "## Active Route Segment Sources"])
    for postal, modes in sorted(summary.get("route_geometry", {}).items()):
        if not isinstance(modes, dict) or modes.get("missing"):
            lines.append(f"- `{postal}`: missing geometry")
            continue
        lines.append(f"- `{postal}`")
        for mode in ["best_transit", "mrt_lrt", "bus"]:
            mode_summary = modes.get(mode) if isinstance(modes.get(mode), dict) else {}
            sheltered = (
                (mode_summary.get("route_segments") or {}).get("sheltered")
                if isinstance(mode_summary, dict)
                else None
            )
            gaps = mode_summary.get("exposure_gaps") if isinstance(mode_summary, dict) else None
            if not sheltered:
                continue
            lines.append(
                "  - "
                f"{mode}: exposed `{fmt(sheltered.get('exposed_m'))}` m, "
                f"covered `{fmt(sheltered.get('covered_m'))}` m, "
                f"sources `{sheltered.get('source_lengths_m')}`, "
                f"largest gap `{fmt((gaps or {}).get('largest_gap_m'))}` m"
            )

    lines.extend(["", "## Feedback Segment Classes"])
    by_feedback = summary["feedback_segments"].get("by_postal", {})
    if by_feedback:
        for postal, classes in sorted(by_feedback.items()):
            lines.append(f"- `{postal}`: `{classes}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Connector Candidates",
            f"- candidate count: `{summary['connector_candidates']['candidate_count']}`",
            f"- promotion statuses: `{summary['connector_candidates']['promotion_status_counts']}`",
            f"- classifications: `{summary['connector_candidates']['classification_counts']}`",
            "",
            "## Candidate Details",
        ]
    )
    by_candidate = summary["connector_candidates"].get("by_postal", {})
    if by_candidate:
        for postal, candidates in sorted(by_candidate.items()):
            lines.append(f"- `{postal}`")
            ordered = sorted(
                candidates,
                key=lambda item: (
                    item.get("segment_index") is None,
                    item.get("segment_index") or 0,
                    str(item.get("audit_id") or ""),
                ),
            )
            for candidate in ordered:
                lines.append(
                    "  - "
                    f"`{candidate.get('audit_id')}`: segment `{candidate.get('segment_index')}`, "
                    f"label `{candidate.get('label')}`, length `{fmt(candidate.get('length_m'))}` m, "
                    f"status `{candidate.get('promotion_status')}`, "
                    f"class `{candidate.get('candidate_classification')}`, "
                    f"covered overlap `{fmt(candidate.get('covered_overlap_ratio'))}`, "
                    f"HDB overlap `{fmt(candidate.get('hdb_overlap_ratio'))}`, "
                    f"OSM shelter overlap `{fmt(candidate.get('osm_shelter_overlap_ratio'))}`, "
                    f"official shelter overlap `{fmt(candidate.get('official_shelter_overlap_ratio'))}`"
                )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Conclusion",
            f"- score override used: `{summary['conclusion']['score_override_used']}`",
            f"- approved source-backed corrections: `{summary['conclusion']['approved_source_backed_corrections']}`",
            f"- ready for owner review: `{summary['conclusion']['ready_for_owner_review']}`",
            f"- blocked without more source evidence: `{summary['conclusion']['blocked_without_more_source_evidence']}`",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Mayflower route QA evidence.")
    parser.add_argument("--bundle-dir", type=Path, default=None)
    parser.add_argument("--component-audit", type=Path, default=DEFAULT_COMPONENT_AUDIT)
    parser.add_argument("--feedback-audit", type=Path, default=DEFAULT_FEEDBACK_AUDIT)
    parser.add_argument("--approved-corrections", type=Path, default=DEFAULT_APPROVED_CORRECTIONS)
    parser.add_argument("--postal", action="append", dest="postals", default=[])
    parser.add_argument(
        "--output-json",
        type=Path,
        default=PROJECT_ROOT / "qa" / "mayflower_route_qa_summary_20260801.json",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=PROJECT_ROOT / "qa" / "mayflower_route_qa_summary_20260801.md",
    )
    args = parser.parse_args()

    summary = build_summary(
        bundle_dir=args.bundle_dir or active_bundle_dir(),
        component_audit_path=args.component_audit,
        feedback_audit_path=args.feedback_audit,
        postals=args.postals or DEFAULT_POSTALS,
        approved_corrections_path=args.approved_corrections,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(args.output_md, summary)
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(args.output_json),
                "output_md": str(args.output_md),
                "approved_source_backed_corrections": summary["conclusion"][
                    "approved_source_backed_corrections"
                ],
                "ready_for_owner_review": summary["conclusion"]["ready_for_owner_review"],
                "blocked_without_more_source_evidence": summary["conclusion"][
                    "blocked_without_more_source_evidence"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
