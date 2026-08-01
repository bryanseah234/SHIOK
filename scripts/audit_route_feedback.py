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
        description="Audit user-drawn route feedback against the current pedestrian network."
    )
    parser.add_argument("feedback", type=Path)
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK)
    parser.add_argument("--search-m", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--geojson", type=Path, default=None)
    parser.add_argument("--candidates-geojson", type=Path, default=None)
    args = parser.parse_args()

    from pipeline.route_feedback import (
        audit_geojson,
        audit_report,
        classify_feedback_segments,
        component_gap_candidate_geojson,
        feedback_segments,
        load_feedback_routes,
        load_network_edges,
    )

    routes = load_feedback_routes(args.feedback)
    segments = feedback_segments(routes)
    network = load_network_edges(args.network)
    audited = classify_feedback_segments(segments, network, search_m=args.search_m)
    report = audit_report(audited)
    report["feedback"] = str(args.feedback)
    report["network"] = str(args.network)
    report["search_m"] = args.search_m

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.geojson:
        args.geojson.parent.mkdir(parents=True, exist_ok=True)
        args.geojson.write_text(
            json.dumps(audit_geojson(audited), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if args.candidates_geojson:
        args.candidates_geojson.parent.mkdir(parents=True, exist_ok=True)
        args.candidates_geojson.write_text(
            json.dumps(component_gap_candidate_geojson(audited), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "ok": report["ok"],
                "route_count": report["route_count"],
                "segment_count": report["segment_count"],
                "classification_counts": report["classification_counts"],
                "output": str(args.output) if args.output else None,
                "geojson": str(args.geojson) if args.geojson else None,
                "candidates_geojson": (
                    str(args.candidates_geojson) if args.candidates_geojson else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
