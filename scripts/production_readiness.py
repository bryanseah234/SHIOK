from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.batch_plan import PARAMS_PATH, build_batch_plan
from pipeline.export import validate_static_artifacts
from pipeline.network_qa import validate_network_qa
from scripts.audit_current_bundle import active_bundle_dir, build_report, summarize_state_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "web"
QA_DIR = PROJECT_ROOT / "qa"
DEFAULT_NETWORK = PROJECT_ROOT / "processed" / "network_island.parquet"
DEFAULT_UNIVERSE = (
    PROJECT_ROOT / "processed" / "postal_universe_candidate_full_registered_geocoded.parquet"
)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object: {path}")
    return payload


def load_vercel_project(path: Path) -> dict[str, Any]:
    project_path = path / ".vercel" / "project.json"
    if not project_path.is_file():
        return {"linked": False, "path": str(project_path), "payload": None}
    return {
        "linked": True,
        "path": str(project_path),
        "payload": read_json(project_path),
    }


def vercel_readiness(project_root: Path, web_dir: Path) -> dict[str, Any]:
    root_link = load_vercel_project(project_root)
    web_link = load_vercel_project(web_dir)
    payload = root_link.get("payload") if root_link.get("linked") else web_link.get("payload")
    settings = payload.get("settings", {}) if isinstance(payload, dict) else {}
    root_directory = settings.get("rootDirectory") if isinstance(settings, dict) else None
    project_name = payload.get("projectName") if isinstance(payload, dict) else None
    project_id = payload.get("projectId") if isinstance(payload, dict) else None

    warnings: list[str] = []
    if root_link.get("linked") and web_link.get("linked"):
        root_payload = root_link.get("payload") or {}
        web_payload = web_link.get("payload") or {}
        if root_payload.get("projectId") != web_payload.get("projectId"):
            warnings.append("root and web Vercel project IDs differ")
        elif root_payload.get("projectName") != web_payload.get("projectName"):
            warnings.append("root and web Vercel project names differ but project ID matches")

    return {
        "linked": bool(root_link.get("linked") or web_link.get("linked")),
        "project_name": project_name,
        "project_id": project_id,
        "root_directory": root_directory,
        "root_directory_ok": root_directory == "web",
        "git_data_strategy": (
            "web build downloads configured bundle from production when local data is absent"
        ),
        "root_link": root_link,
        "web_link": web_link,
        "warnings": warnings,
    }


def file_mtime_iso(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


def bundle_network_freshness(bundle_dir: Path, network_path: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / "manifest.json"
    manifest_payload = read_json(manifest_path) if manifest_path.is_file() else {}
    bundle_mtime = manifest_path.stat().st_mtime if manifest_path.is_file() else None
    network_mtime = network_path.stat().st_mtime if network_path.is_file() else None
    stale_seconds: float | None = None
    if bundle_mtime is not None and network_mtime is not None:
        stale_seconds = max(0.0, network_mtime - bundle_mtime)

    stale = stale_seconds is not None and stale_seconds > 60.0
    warning = None
    if stale:
        warning = (
            "active bundle predates current network build; run targeted/full rescore/export "
            "before claiming latest network corrections are live"
        )

    return {
        "active_bundle_reflects_current_network": not stale,
        "bundle_manifest_path": str(manifest_path),
        "bundle_manifest_mtime": file_mtime_iso(manifest_path),
        "bundle_generated_at": manifest_payload.get("generated_at"),
        "network_path": str(network_path),
        "network_mtime": file_mtime_iso(network_path),
        "stale_seconds": round(stale_seconds, 3) if stale_seconds is not None else None,
        "warning": warning,
    }


def readiness_features() -> dict[str, Any]:
    return {
        "incorporated": {
            "nparks_spatial_shade_proxy_heat_only": True,
            "broader_osm_covered_tags_from_hashed_pbf": True,
            "bus_as_transit_direct_fallback": True,
            "all_known_source_derived_postals_scored_or_explicit_state": True,
        },
        "not_incorporated": {
            "canonical_140k_postal_universe": (
                "not claimed; Overture Addresses is a candidate gate, not accepted source-of-record"
            ),
            "overture_addresses_sg_candidate": (
                "optional archive/probe implemented; produced 125876-postal candidate universe "
                "with 1671 Overture-only postcodes; coordinate QA implemented with p95 23.5m "
                "and 41 postcodes over 1km; not active production until outlier review/rescore"
            ),
            "nparks_lai_route_level_canopy": "LAI is calibration-only, not route geometry",
            "building_shadow_time_of_day": "future heat model",
            "live_bus_or_mrt_arrivals": "requires runtime proxy or collected static aggregates",
            "bellingcat_openinframap_overpass_as_production_feeds": (
                "QA/discovery only unless raw bounded OSM query output is archived and hashed"
            ),
            "mayflower_560231_560234_shelter_false_negative": (
                "needs source-backed connector/correction review; no postal override"
            ),
        },
    }


def build_readiness_report(
    *,
    project_root: Path = PROJECT_ROOT,
    web_dir: Path = WEB_DIR,
    bundle_dir: Path | None = None,
    mode: str = "candidate_full_registered",
    summary_path: Path | None = None,
    universe_path: Path | None = None,
    params_path: Path = PARAMS_PATH,
    qa_path: Path | None = None,
    debug_path: Path | None = None,
    network_path: Path = DEFAULT_NETWORK,
    postal_universe_path: Path = DEFAULT_UNIVERSE,
) -> tuple[bool, dict[str, Any]]:
    bundle_dir = bundle_dir or active_bundle_dir()
    qa_path = qa_path or project_root / "qa" / "conflation_qa_island.json"
    debug_path = debug_path or project_root / "qa" / "island_debug.geojson"

    validation_ok, validation = validate_static_artifacts(input_dir=bundle_dir)
    bundle_state_full = build_report(
        bundle_dir=bundle_dir,
        replay_limit=0,
        network_path=network_path,
        postal_universe_path=postal_universe_path,
    )
    bundle_state = summarize_state_report(bundle_state_full)
    state_total = sum(int(value) for value in bundle_state["state_counts"].values())
    state_total_matches_manifest = state_total == int(bundle_state["manifest_record_count"])

    island_ok, island_qa = validate_network_qa(
        qa_path,
        debug_path,
        require_production_sources=True,
    )
    batch_ok, batch_plan = build_batch_plan(
        mode=mode,
        summary_path=summary_path,
        universe_path=universe_path,
        params_path=params_path,
        qa_path=qa_path,
        debug_path=debug_path,
    )
    vercel = vercel_readiness(project_root, web_dir)
    freshness = bundle_network_freshness(bundle_dir, network_path)

    errors: list[str] = []
    warnings: list[str] = []
    if not validation_ok:
        errors.append("static data validation failed")
    if not state_total_matches_manifest:
        errors.append("bundle state counts do not match manifest record_count")
    if not island_ok:
        errors.append("island network QA failed")
    if not batch_ok:
        errors.append("batch plan failed")
    if not vercel["linked"]:
        errors.append("Vercel project is not linked")
    if not vercel["root_directory_ok"]:
        errors.append("Vercel root directory is not web")
    if freshness["warning"]:
        warnings.append(str(freshness["warning"]))

    report: dict[str, Any] = {
        "ok": not errors,
        "generated_at": datetime.now(UTC).isoformat(),
        "bundle": {
            **bundle_state,
            "path": str(bundle_dir),
            "state_total_matches_manifest": state_total_matches_manifest,
            "static_validation": {
                "ok": validation.get("ok"),
                "file_count": validation.get("file_count"),
                "indexed_postals": validation.get("indexed_postals"),
                "geometry_postals": validation.get("geometry_postals"),
                "geometry_postals_with_route_segments": validation.get(
                    "geometry_postals_with_route_segments"
                ),
                "transit_features": validation.get("transit_features"),
                "score_prefixes": validation.get("score_prefixes"),
                "errors": validation.get("errors", []),
                "warnings": validation.get("warnings", []),
            },
            "freshness": freshness,
        },
        "network": {
            "ok": island_qa.get("ok"),
            "qa_path": island_qa.get("qa_path"),
            "debug_path": island_qa.get("debug_path"),
            "metrics": island_qa.get("metrics", {}),
            "errors": island_qa.get("errors", []),
            "warnings": island_qa.get("warnings", []),
        },
        "batch_plan": {
            "ok": batch_plan.get("ok"),
            "postal_universe": batch_plan.get("postal_universe", {}),
            "bounded_geocoding": batch_plan.get("bounded_geocoding", {}),
            "scoring_batch": batch_plan.get("scoring_batch", {}),
            "checkpoint_gates": batch_plan.get("checkpoint_gates", {}),
            "warnings": batch_plan.get("warnings", []),
            "errors": batch_plan.get("errors", []),
        },
        "vercel": vercel,
        "features": readiness_features(),
        "errors": errors,
        "warnings": warnings,
    }
    return not errors, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fast production-readiness report without scoring or deploying."
    )
    parser.add_argument("--bundle-dir", type=Path, default=None)
    parser.add_argument("--mode", default="candidate_full_registered")
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--params", type=Path, default=PARAMS_PATH)
    parser.add_argument("--qa", type=Path, default=None)
    parser.add_argument("--debug", type=Path, default=None)
    args = parser.parse_args()

    ok, report = build_readiness_report(
        bundle_dir=args.bundle_dir,
        mode=args.mode,
        summary_path=args.summary,
        universe_path=args.universe,
        params_path=args.params,
        qa_path=args.qa,
        debug_path=args.debug,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
