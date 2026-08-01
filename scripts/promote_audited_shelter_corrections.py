from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = PROJECT_ROOT / "data" / "audited_shelter_corrections.geojson"


def read_geojson(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError(f"not a GeoJSON FeatureCollection: {path}")
    return cast(dict[str, Any], payload)


def write_geojson(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def feature_audit_id(feature: dict[str, Any]) -> str:
    props = feature.get("properties")
    if not isinstance(props, dict):
        return ""
    return str(props.get("audit_id") or props.get("id") or "").strip()


def validate_review_ready(feature: dict[str, Any]) -> None:
    props = feature.get("properties")
    if not isinstance(props, dict):
        raise ValueError("feature has no properties")
    audit_id = feature_audit_id(feature)
    if not audit_id:
        raise ValueError("feature has no audit_id")
    status = str(props.get("status", "")).strip().lower()
    if status != "needs_owner_review":
        raise ValueError(f"{audit_id} status must be needs_owner_review, got {status!r}")
    promotion_status = str(props.get("promotion_status", "")).strip()
    if promotion_status != "review_ready_not_scoring":
        raise ValueError(
            f"{audit_id} promotion_status must be review_ready_not_scoring, "
            f"got {promotion_status!r}"
        )
    if props.get("is_covered") is not True and str(props.get("covered", "")).lower() != "yes":
        raise ValueError(f"{audit_id} must be marked covered")


def promote_feature(
    feature: dict[str, Any],
    *,
    reviewer: str,
    evidence_note: str,
    reviewed_at: str,
) -> dict[str, Any]:
    validate_review_ready(feature)
    promoted = deepcopy(feature)
    props = promoted.setdefault("properties", {})
    props["original_status"] = props.get("status")
    props["status"] = "approved"
    props["reviewer"] = reviewer
    props["reviewed_at"] = reviewed_at
    props["approval_note"] = evidence_note
    props["review_policy"] = (
        "Approved only after human review of source-backed shelter evidence; "
        "network build ingests approved covered LineString/MultiLineString features."
    )
    return promoted


def promote_corrections(
    *,
    draft_path: Path,
    target_path: Path,
    audit_ids: list[str],
    reviewer: str,
    evidence_note: str,
    dry_run: bool,
) -> dict[str, Any]:
    if not audit_ids:
        raise ValueError("at least one --approve audit_id is required")
    if not reviewer.strip():
        raise ValueError("--reviewer is required")
    if not evidence_note.strip():
        raise ValueError("--evidence-note is required")

    draft = read_geojson(draft_path)
    target = (
        read_geojson(target_path)
        if target_path.is_file()
        else {
            "type": "FeatureCollection",
            "name": "audited_shelter_corrections",
            "schema": "shiok-audited-shelter-corrections-v1",
            "usage": (
                "Only source-backed covered pedestrian correction lines with status=approved "
                "and covered/is_covered=true are ingested."
            ),
            "features": [],
        }
    )

    requested = list(
        dict.fromkeys(str(audit_id).strip() for audit_id in audit_ids if audit_id.strip())
    )
    draft_by_id = {feature_audit_id(feature): feature for feature in draft["features"]}
    missing = [audit_id for audit_id in requested if audit_id not in draft_by_id]
    if missing:
        raise ValueError(f"requested audit_id not found in draft: {missing}")

    reviewed_at = datetime.now(UTC).isoformat()
    promoted = [
        promote_feature(
            draft_by_id[audit_id],
            reviewer=reviewer.strip(),
            evidence_note=evidence_note.strip(),
            reviewed_at=reviewed_at,
        )
        for audit_id in requested
    ]

    target_features = {
        feature_audit_id(feature): feature
        for feature in target.get("features", [])
        if feature_audit_id(feature)
    }
    for feature in promoted:
        target_features[feature_audit_id(feature)] = feature
    target["features"] = [target_features[audit_id] for audit_id in sorted(target_features)]

    if not dry_run:
        write_geojson(target_path, target)

    return {
        "ok": True,
        "dry_run": dry_run,
        "draft": str(draft_path),
        "target": str(target_path),
        "approved_count": len(promoted),
        "approved_audit_ids": requested,
        "target_feature_count": len(target["features"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote human-reviewed draft shelter corrections into the approved layer."
    )
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--approve", action="append", dest="audit_ids", default=[])
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--evidence-note", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = promote_corrections(
        draft_path=args.draft,
        target_path=args.target,
        audit_ids=args.audit_ids,
        reviewer=args.reviewer,
        evidence_note=args.evidence_note,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
