# ruff: noqa: E402, RUF100

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.scoring_integration import NETWORK_PATH, score_postals

DEFAULT_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)


def compact_candidate_audit(record: dict[str, Any]) -> dict[str, Any]:
    raw_provenance = record.get("provenance")
    provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
    return {
        "postal": record.get("postal"),
        "state": record.get("state"),
        "total": record.get("total"),
        "best_node": record.get("best_node"),
        "paths": record.get("paths"),
        "route_options": record.get("route_options"),
        "transit_node_set": provenance.get("transit_node_set"),
        "bus_connectivity": provenance.get("bus_connectivity"),
        "direct_bus_fallback": provenance.get("direct_bus_fallback"),
        "bus_stop_access_connector": provenance.get("bus_stop_access_connector"),
        "untrusted_bus_routes": provenance.get("untrusted_bus_routes"),
        "candidate_selection": provenance.get("candidate_selection"),
        "candidate_debug": provenance.get("candidate_debug"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit ranked MRT/LRT and bus candidate scores for selected postals."
    )
    parser.add_argument("--postal", dest="postals", action="append", required=True)
    parser.add_argument("--network", type=Path, default=NETWORK_PATH)
    parser.add_argument("--postal-universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = score_postals(
        postal_codes=args.postals,
        include_geometry=False,
        network_path=args.network,
        postal_universe_path=args.postal_universe,
        include_candidate_debug=True,
    )
    audit = {
        "ok": len(records) == len({str(postal).zfill(6) for postal in args.postals}),
        "requested_count": len(args.postals),
        "record_count": len(records),
        "network": str(args.network),
        "postal_universe": str(args.postal_universe),
        "records": [compact_candidate_audit(record) for record in records],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": audit["ok"],
                "record_count": audit["record_count"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if audit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
