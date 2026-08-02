from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
QA_DIR = PROJECT_ROOT / "qa"


def _residual_feature(residual: dict[str, Any], *, source: str, index: int) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [float(residual["lon"]), float(residual["lat"])],
        },
        "properties": {
            "class": residual.get("class"),
            "source": source,
            "index": index,
            "size": residual.get("size"),
            "gap_m": residual.get("gap_m"),
            "evidence": residual.get("evidence"),
        },
    }


def build_debug_geojson(report: dict[str, Any]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for source_key in ("residual_components_gt_50_final", "residual_components_gt_50_osm_only"):
        residuals = report.get(source_key, [])
        if not isinstance(residuals, list):
            continue
        for index, residual in enumerate(residuals):
            if not isinstance(residual, dict):
                continue
            if "lat" not in residual or "lon" not in residual:
                continue
            features.append(_residual_feature(residual, source=source_key, index=index))

    return {
        "type": "FeatureCollection",
        "name": "network_qa_residual_debug",
        "shiok_debug_kind": "compact_residual_points_from_conflation_qa",
        "features": features,
    }


def rebuild_debug_geojson(qa_path: Path, output_path: Path) -> dict[str, Any]:
    report = json.loads(qa_path.read_text(encoding="utf-8"))
    payload = build_debug_geojson(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "qa_path": str(qa_path),
        "output": str(output_path),
        "feature_count": len(payload["features"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild compact network debug GeoJSON from an existing conflation QA JSON."
    )
    parser.add_argument("--qa", type=Path, default=QA_DIR / "conflation_qa_island.json")
    parser.add_argument("--output", type=Path, default=QA_DIR / "island_debug.geojson")
    args = parser.parse_args()

    report = rebuild_debug_geojson(args.qa, args.output)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
