from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_NETWORK = PROJECT_ROOT / "processed" / "network_island.parquet"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit QA connector candidates against current source-backed network evidence."
    )
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK)
    parser.add_argument("--search-m", type=float, default=80.0)
    parser.add_argument("--evidence-buffer-m", type=float, default=8.0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--geojson", type=Path, default=None)
    parser.add_argument("--draft-corrections", type=Path, default=None)
    args = parser.parse_args()

    from pipeline.connector_candidates import (
        audit_candidate_file,
        audit_geojson,
        audit_summary,
        draft_correction_geojson,
    )

    audited = audit_candidate_file(
        args.candidates,
        args.network,
        search_m=args.search_m,
        evidence_buffer_m=args.evidence_buffer_m,
    )
    report = audit_summary(audited)
    report["candidate_source"] = str(args.candidates)
    report["network"] = str(args.network)
    report["search_m"] = args.search_m
    report["evidence_buffer_m"] = args.evidence_buffer_m

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if args.geojson:
        args.geojson.parent.mkdir(parents=True, exist_ok=True)
        args.geojson.write_text(
            json.dumps(audit_geojson(audited), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    if args.draft_corrections:
        args.draft_corrections.parent.mkdir(parents=True, exist_ok=True)
        args.draft_corrections.write_text(
            json.dumps(draft_correction_geojson(audited), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "ok": report["ok"],
                "candidate_count": report["candidate_count"],
                "classification_counts": report["classification_counts"],
                "promotion_status_counts": report["promotion_status_counts"],
                "output": str(args.output) if args.output else None,
                "geojson": str(args.geojson) if args.geojson else None,
                "draft_corrections": (
                    str(args.draft_corrections) if args.draft_corrections else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
