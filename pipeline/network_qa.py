from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TypeGuard


PROJECT_ROOT = Path(__file__).resolve().parent.parent
QA_DIR = PROJECT_ROOT / "qa"
VALID_AREAS = {"pilot", "island"}
RESIDUAL_CLASSES = {
    "PRIVATE_ESTATE",
    "CLIP_EDGE",
    "ISOLATED_NON_TRANSIT",
    "REAL_DISCONNECTION",
}
REQUIRED_QA_KEYS = {
    "nodes",
    "edges",
    "mean_edge_length_m",
    "connected_components_count",
    "top_5_component_sizes",
    "residual_components_gt_50_osm_only",
    "residual_components_gt_50_final",
    "real_disconnection_count_osm_only",
    "real_disconnection_count_final",
    "flags",
}
EXPECTED_NPARKS_SHADE_SOURCES = {
    "nparks_heritage_trees",
    "nparks_heritage_road_green_buffers",
    "nparks_nature_ways",
    "nparks_park_connector_loop",
    "nparks_tracks",
}
PRODUCTION_SOURCE_METRIC_KEYS = {
    "covered_edge_length_m_osm_tags",
    "covered_edge_length_m_lta_bridge_underpass_match",
    "covered_edge_length_m_osm_roof_canopy",
    "covered_edge_length_m_inferred_hdb_precinct_footways",
    "covered_edge_length_m_inferred_hdb_point_footways",
    "covered_edge_length_m_inferred_hdb_void_deck",
    "shade_proxy_edge_count",
    "shade_proxy_weighted_length_m",
    "shade_proxy_sources",
}


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _validate_residuals(
    report: dict[str, Any],
    key: str,
    errors: list[str],
    warnings: list[str],
) -> int:
    residuals = report.get(key)
    if not isinstance(residuals, list):
        errors.append(f"{key} must be a list")
        return 0

    for index, residual in enumerate(residuals):
        prefix = f"{key}[{index}]"
        if not isinstance(residual, dict):
            errors.append(f"{prefix} must be an object")
            continue

        for required in ("size", "lat", "lon", "gap_m", "class", "evidence"):
            if required not in residual:
                errors.append(f"{prefix} missing {required}")

        cls = residual.get("class")
        if cls not in RESIDUAL_CLASSES:
            errors.append(f"{prefix} has unsupported class {cls!r}")
        if cls == "REAL_DISCONNECTION":
            errors.append(f"{prefix} is REAL_DISCONNECTION")

        if not _is_number(residual.get("size")) or residual.get("size", 0) <= 50:
            warnings.append(f"{prefix} size is not a >50-node residual")
        for coordinate in ("lat", "lon", "gap_m"):
            if not _is_number(residual.get(coordinate)):
                errors.append(f"{prefix} {coordinate} must be numeric")
        if not residual.get("evidence"):
            errors.append(f"{prefix} evidence is empty")

    return len(residuals)


def _validate_positive_metric(
    report: dict[str, Any],
    key: str,
    errors: list[str],
) -> None:
    value = report.get(key)
    if not _is_number(value):
        errors.append(f"{key} must be numeric")
    elif float(value) <= 0:
        errors.append(f"{key} must be > 0, got {value!r}")


def _validate_production_sources(report: dict[str, Any], errors: list[str]) -> None:
    missing = sorted(PRODUCTION_SOURCE_METRIC_KEYS - set(report))
    for key in missing:
        errors.append(f"missing production source metric: {key}")

    for key in sorted(PRODUCTION_SOURCE_METRIC_KEYS - {"shade_proxy_sources"}):
        if key in report:
            _validate_positive_metric(report, key, errors)

    shade_sources = report.get("shade_proxy_sources")
    if not isinstance(shade_sources, dict):
        errors.append("shade_proxy_sources must be an object")
        return

    for source_key in sorted(EXPECTED_NPARKS_SHADE_SOURCES):
        source = shade_sources.get(source_key)
        if not isinstance(source, dict):
            errors.append(f"shade_proxy_sources missing {source_key}")
            continue
        if source.get("status") != "loaded":
            errors.append(f"shade_proxy_sources.{source_key}.status must be loaded")
        for metric in ("features_raw", "features_in_scope", "proxy_polygons"):
            value = source.get(metric)
            if not _is_number(value) or float(value) <= 0:
                errors.append(
                    f"shade_proxy_sources.{source_key}.{metric} must be > 0, got {value!r}"
                )

    covered_metric_keys = [key for key in report if str(key).startswith("covered_edge_length_m_")]
    bad_tree_as_rain = [
        key
        for key in covered_metric_keys
        if "nparks" in key.lower() or "shade" in key.lower() or "tree" in key.lower()
    ]
    if bad_tree_as_rain:
        errors.append(
            "shade/tree metrics must not be counted as rain shelter: "
            + ", ".join(sorted(bad_tree_as_rain))
        )


def validate_network_qa(
    qa_path: Path,
    debug_path: Path | None = None,
    *,
    require_debug: bool = True,
    require_production_sources: bool = False,
    min_mean_edge_m: float = 10.0,
    max_mean_edge_m: float = 40.0,
) -> tuple[bool, dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not qa_path.is_file():
        return False, {
            "ok": False,
            "qa_path": str(qa_path),
            "debug_path": str(debug_path) if debug_path else None,
            "errors": [f"missing QA file: {qa_path}"],
            "warnings": [],
        }

    with open(qa_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    if not isinstance(report, dict):
        return False, {
            "ok": False,
            "qa_path": str(qa_path),
            "debug_path": str(debug_path) if debug_path else None,
            "errors": ["QA report must be a JSON object"],
            "warnings": [],
        }

    missing = sorted(REQUIRED_QA_KEYS - set(report))
    for key in missing:
        errors.append(f"missing required key: {key}")

    for key in ("real_disconnection_count_osm_only", "real_disconnection_count_final"):
        value = report.get(key)
        if value != 0:
            errors.append(f"{key} must be 0, got {value!r}")

    flags = report.get("flags", [])
    if not isinstance(flags, list):
        errors.append("flags must be a list")
    elif flags:
        errors.append(f"qa flags present: {', '.join(str(flag) for flag in flags)}")

    mean_edge = report.get("mean_edge_length_m")
    if not _is_number(mean_edge):
        errors.append("mean_edge_length_m must be numeric")
    elif not min_mean_edge_m <= float(mean_edge) <= max_mean_edge_m:
        errors.append(
            f"mean_edge_length_m must be between {min_mean_edge_m:g} and "
            f"{max_mean_edge_m:g}, got {mean_edge!r}"
        )

    osm_residual_count = _validate_residuals(
        report, "residual_components_gt_50_osm_only", errors, warnings
    )
    final_residual_count = _validate_residuals(
        report, "residual_components_gt_50_final", errors, warnings
    )

    if require_debug:
        if debug_path is None:
            errors.append("debug_path is required")
        elif not debug_path.is_file():
            errors.append(f"missing debug GeoJSON: {debug_path}")

    if require_production_sources:
        _validate_production_sources(report, errors)

    ok = not errors
    summary: dict[str, Any] = {
        "ok": ok,
        "qa_path": str(qa_path),
        "debug_path": str(debug_path) if debug_path else None,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "nodes": report.get("nodes"),
            "edges": report.get("edges"),
            "mean_edge_length_m": report.get("mean_edge_length_m"),
            "connected_components_count": report.get("connected_components_count"),
            "top_5_component_sizes": report.get("top_5_component_sizes"),
            "real_disconnection_count_osm_only": report.get("real_disconnection_count_osm_only"),
            "real_disconnection_count_final": report.get("real_disconnection_count_final"),
            "flags": report.get("flags"),
            "osm_residual_components_gt_50": osm_residual_count,
            "final_residual_components_gt_50": final_residual_count,
            "shade_proxy_edge_count": report.get("shade_proxy_edge_count"),
            "shade_proxy_weighted_length_m": report.get("shade_proxy_weighted_length_m"),
        },
    }
    return ok, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a network conflation QA report against acceptance gates."
    )
    parser.add_argument("--area", default="pilot", choices=sorted(VALID_AREAS))
    parser.add_argument("--qa", type=Path, help="Override QA JSON path.")
    parser.add_argument("--debug", type=Path, help="Override debug GeoJSON path.")
    parser.add_argument(
        "--no-debug",
        action="store_true",
        help="Do not require the debug GeoJSON artifact.",
    )
    args = parser.parse_args()

    qa_path = args.qa or QA_DIR / f"conflation_qa_{args.area}.json"
    debug_path = args.debug or QA_DIR / f"{args.area}_debug.geojson"
    ok, summary = validate_network_qa(
        qa_path,
        debug_path,
        require_debug=not args.no_debug,
        require_production_sources=args.area == "island",
    )
    print(json.dumps(summary, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
