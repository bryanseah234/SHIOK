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
REQUIRED_SUBSCORE_STATUS = {"access", "bus", "rain", "heat", "crossing"}
REQUIRED_SCORING_FINGERPRINTS = {
    "pipeline\\scoring.py",
    "pipeline\\scoring_integration.py",
    "pipeline\\routing.py",
    "pipeline\\config\\params.yaml",
    "pipeline\\config\\weights.yaml",
}


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


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def bundle_validation_floor(bundle_dir: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / "manifest.json"
    manifest_payload = read_json(manifest_path) if manifest_path.is_file() else {}
    generated_at = parse_iso_datetime(manifest_payload.get("generated_at"))
    mtime = (
        datetime.fromtimestamp(manifest_path.stat().st_mtime, UTC)
        if manifest_path.is_file()
        else None
    )
    candidates = [item for item in [generated_at, mtime] if item is not None]
    floor = max(candidates) if candidates else None
    return {
        "manifest_path": str(manifest_path),
        "bundle_generated_at": manifest_payload.get("generated_at"),
        "manifest_mtime": file_mtime_iso(manifest_path),
        "fresh_after": floor.isoformat() if floor else None,
    }


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


def bundle_score_provenance_status(bundle_dir: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        return {
            "ok": False,
            "manifest_path": str(manifest_path),
            "source_hash_count": 0,
            "scoring_fingerprint_count": 0,
            "subscore_status_keys": [],
            "missing_scoring_fingerprints": sorted(REQUIRED_SCORING_FINGERPRINTS),
            "missing_subscore_status": sorted(REQUIRED_SUBSCORE_STATUS),
            "warning": "bundle manifest is missing",
        }

    manifest_payload = read_json(manifest_path)
    provenance = manifest_payload.get("provenance")
    if not isinstance(provenance, dict):
        return {
            "ok": False,
            "manifest_path": str(manifest_path),
            "source_hash_count": 0,
            "scoring_fingerprint_count": 0,
            "subscore_status_keys": [],
            "missing_scoring_fingerprints": sorted(REQUIRED_SCORING_FINGERPRINTS),
            "missing_subscore_status": sorted(REQUIRED_SUBSCORE_STATUS),
            "warning": "bundle manifest provenance block is missing",
        }

    source_hashes = provenance.get("source_hashes")
    scoring_fingerprints = provenance.get("scoring_fingerprints")
    subscore_status = provenance.get("subscore_status")
    source_hash_count = len(source_hashes) if isinstance(source_hashes, dict) else 0
    fingerprint_keys = (
        set(scoring_fingerprints) if isinstance(scoring_fingerprints, dict) else set()
    )
    missing_fingerprints = sorted(REQUIRED_SCORING_FINGERPRINTS - fingerprint_keys)
    subscore_keys = set(subscore_status) if isinstance(subscore_status, dict) else set()
    missing_subscores = sorted(REQUIRED_SUBSCORE_STATUS - subscore_keys)
    ok = source_hash_count > 0 and not missing_subscores and not missing_fingerprints
    warning = None
    if not ok:
        warning = (
            "active bundle manifest lacks score source hashes, scoring code/config fingerprints, "
            "or complete subscore status; regenerate/export the bundle with current code before "
            "using it as provenance evidence"
        )

    return {
        "ok": ok,
        "manifest_path": str(manifest_path),
        "source_hash_count": source_hash_count,
        "scoring_fingerprint_count": len(fingerprint_keys),
        "subscore_status_keys": sorted(subscore_keys),
        "missing_scoring_fingerprints": missing_fingerprints,
        "missing_subscore_status": missing_subscores,
        "warning": warning,
    }


def latest_json_report(qa_dir: Path, pattern: str) -> Path | None:
    reports = [path for path in qa_dir.glob(pattern) if path.is_file()]
    if not reports:
        return None
    return max(reports, key=lambda path: (path.stat().st_mtime, path.name))


def latest_json_reports(qa_dir: Path, pattern: str) -> list[Path]:
    return sorted(
        [path for path in qa_dir.glob(pattern) if path.is_file()],
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def is_blocking_onemap_report(report: dict[str, Any]) -> bool:
    sample_size = report.get("sample_size")
    sample_kind = report.get("sample_kind")
    return (
        isinstance(sample_size, int)
        and sample_size >= 2000
        and sample_kind in (None, "blocking_stratified")
    )


def onemap_subset_status(report: dict[str, Any]) -> dict[str, Any]:
    raw_subsets = report.get("subset_summary")
    if not isinstance(raw_subsets, dict):
        return {"subset_summary": {}, "failing_subset_order": []}

    subset_summary = {
        str(name): value for name, value in raw_subsets.items() if isinstance(value, dict)
    }
    failing_subsets: list[dict[str, Any]] = []
    for name, summary in subset_summary.items():
        if summary.get("thresholds_passed") is not False:
            continue
        failing_subsets.append(
            {
                "subset": name,
                "count": summary.get("count"),
                "median_abs_pct_delta": summary.get("median_abs_pct_delta"),
                "p95_abs_pct_delta": summary.get("p95_abs_pct_delta"),
                "median_abs_delta_m": summary.get("median_abs_delta_m"),
                "p95_abs_delta_m": summary.get("p95_abs_delta_m"),
            }
        )
    failing_subsets.sort(
        key=lambda item: (
            -(float(item["p95_abs_pct_delta"]) if item["p95_abs_pct_delta"] is not None else -1),
            str(item["subset"]),
        )
    )
    return {
        "subset_summary": subset_summary,
        "failing_subset_order": failing_subsets,
    }


def onemap_validation_status(
    qa_dir: Path,
    *,
    active_bundle: str | None = None,
    bundle_dir: Path | None = None,
) -> dict[str, Any]:
    report_paths = latest_json_reports(qa_dir, "onemap_validation_cached_report*.json")
    report_path = None
    readable_reports: list[tuple[Path, dict[str, Any]]] = []
    for candidate_path in report_paths:
        try:
            candidate = read_json(candidate_path)
        except (OSError, TypeError, json.JSONDecodeError):
            continue
        if is_blocking_onemap_report(candidate):
            readable_reports.append((candidate_path, candidate))
    latest_any_path = readable_reports[0][0] if readable_reports else None
    if active_bundle:
        for candidate_path, candidate in readable_reports:
            if candidate.get("bundle") == active_bundle:
                report_path = candidate_path
                break
    report_path = report_path or latest_any_path
    if report_path is None:
        return {
            "state": "not_collected",
            "report_path": None,
            "gate_passed": False,
            "bundle_matches_active": None,
            "fresh_for_active_bundle": False,
            "summary": (
                "sample planner, guarded collector, and cache evaluator implemented; "
                "the 2,000-postal OneMap walk comparison has not been collected yet"
            ),
        }

    try:
        report = read_json(report_path)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        return {
            "state": "unreadable",
            "report_path": str(report_path),
            "summary": f"latest cached OneMap validation report is unreadable: {exc}",
        }

    gate_passed = bool(report.get("gate_passed"))
    report_bundle = report.get("bundle")
    bundle_matches_active: bool | None = None
    if active_bundle and isinstance(report_bundle, str):
        bundle_matches_active = report_bundle == active_bundle
    freshness = bundle_validation_floor(bundle_dir) if bundle_dir else {}
    report_generated_at = parse_iso_datetime(report.get("generated_at"))
    fresh_after = parse_iso_datetime(freshness.get("fresh_after"))
    fresh_for_active_bundle = (
        bool(bundle_matches_active)
        and report_generated_at is not None
        and (fresh_after is None or report_generated_at >= fresh_after)
    )

    thresholds = report.get("thresholds", {})
    subset_status = onemap_subset_status(report)
    median = report.get("median_abs_pct_delta")
    p95 = report.get("p95_abs_pct_delta")
    median_max = (
        thresholds.get("median_abs_pct_delta_max") if isinstance(thresholds, dict) else None
    )
    p95_max = thresholds.get("p95_abs_pct_delta_max") if isinstance(thresholds, dict) else None
    same_bundle_fresh_gate_passed = bool(gate_passed and fresh_for_active_bundle)
    result_state = "passed" if gate_passed else "failed"
    state = result_state
    if bundle_matches_active is False:
        state = f"{result_state}_stale_bundle"
    elif bundle_matches_active and not fresh_for_active_bundle:
        state = f"{result_state}_stale_report"

    if bundle_matches_active is False:
        summary = (
            "latest cached 2,000-postal OneMap walk validation is for bundle "
            f"{report_bundle}, not active bundle {active_bundle}; rerun validation before "
            f"using it as active-bundle launch evidence. That cached validation {result_state}: "
            f"median abs delta {median}%"
        )
    elif bundle_matches_active and not fresh_for_active_bundle:
        summary = (
            "latest cached 2,000-postal OneMap walk validation is for the active bundle "
            f"{active_bundle}, but it is stale for the current manifest; rerun validation before "
            f"using it as launch evidence. That cached validation {result_state}: "
            f"median abs delta {median}%"
        )
    else:
        summary = (
            f"latest cached 2,000-postal OneMap walk validation {result_state}: "
            f"median abs delta {median}%"
        )
    if median_max is not None:
        summary += f" (max {median_max}%)"
    summary += f", p95 abs delta {p95}%"
    if p95_max is not None:
        summary += f" (max {p95_max}%)"
    summary += (
        f", missing cache results {report.get('missing_cache_results')}, "
        f"invalid cache results {report.get('invalid_cache_results')}"
    )

    return {
        "state": state,
        "report_path": str(report_path),
        "summary": summary,
        "bundle": report_bundle,
        "active_bundle": active_bundle,
        "bundle_matches_active": bundle_matches_active,
        "fresh_for_active_bundle": fresh_for_active_bundle,
        "same_bundle_fresh_gate_passed": same_bundle_fresh_gate_passed,
        "gate_passed": gate_passed,
        "sample_size": report.get("sample_size"),
        "cached_results": report.get("cached_results"),
        "missing_cache_results": report.get("missing_cache_results"),
        "invalid_cache_results": report.get("invalid_cache_results"),
        "median_abs_pct_delta": median,
        "p95_abs_pct_delta": p95,
        "thresholds": thresholds,
        "subset_summary": subset_status["subset_summary"],
        "failing_subset_order": subset_status["failing_subset_order"],
        "generated_at": report.get("generated_at"),
        "freshness": freshness,
        "latest_any_report_path": str(latest_any_path) if latest_any_path else None,
    }


def readiness_features(
    qa_dir: Path = QA_DIR,
    *,
    active_bundle: str | None = None,
    bundle_dir: Path | None = None,
) -> dict[str, Any]:
    onemap_status = onemap_validation_status(
        qa_dir,
        active_bundle=active_bundle,
        bundle_dir=bundle_dir,
    )
    return {
        "incorporated": {
            "nparks_spatial_shade_proxy_heat_only": True,
            "broader_osm_covered_tags_from_hashed_pbf": True,
            "bus_as_transit_direct_fallback": True,
            "ura_no_dwelling_units_postal_source": True,
            "all_known_source_derived_postals_scored_or_explicit_state": True,
        },
        "not_incorporated": {
            "canonical_140k_postal_universe": (
                "not claimed; URA improves official-current coverage and Overture remains a "
                "candidate gate, but neither is a complete accepted source-of-record"
            ),
            "ura_expanded_scores_live": (
                "postal prep produces 124443 candidate records and 123967 ready-to-score rows; "
                "not active production until full rescore/export/deploy"
            ),
            "overture_addresses_sg_candidate": (
                "optional archive/probe implemented; produced 125876-postal candidate universe "
                "with 1671 Overture-only postcodes; coordinate QA implemented with p95 23.5m "
                "and 41 postcodes over 1km; not active production until outlier review/rescore"
            ),
            "nparks_lai_route_level_canopy": "LAI is calibration-only, not route geometry",
            "building_shadow_time_of_day": "future heat model",
            "live_bus_or_mrt_arrivals": "requires runtime proxy or collected static aggregates",
            "onemap_walk_validation_gate": onemap_status["summary"],
            "bellingcat_openinframap_overpass_as_production_feeds": (
                "QA/discovery only unless raw bounded OSM query output is archived and hashed"
            ),
            "mayflower_560231_560234_shelter_false_negative": (
                "needs source-backed connector/correction review; no postal override"
            ),
        },
        "validation_gates": {
            "onemap_walk_validation": onemap_status,
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
    waive_onemap_validation: bool = False,
    owner_approval_note: str = "",
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
    score_provenance = bundle_score_provenance_status(bundle_dir)
    onemap_status = onemap_validation_status(
        project_root / "qa",
        active_bundle=str(bundle_state.get("bundle") or ""),
        bundle_dir=bundle_dir,
    )

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
    if score_provenance["warning"]:
        warnings.append(str(score_provenance["warning"]))
    onemap_gate_passed = bool(onemap_status.get("same_bundle_fresh_gate_passed"))
    onemap_gate_waived = bool(waive_onemap_validation and not onemap_gate_passed)
    if onemap_gate_waived:
        onemap_status = {
            **onemap_status,
            "waived": True,
            "waiver_reason": owner_approval_note
            or "owner approved release despite failed OneMap validation gate",
        }

    if not onemap_gate_passed:
        warnings.append(str(onemap_status.get("summary")))

    release_gate_checks = {
        "static_artifact_validation": bool(validation.get("ok")),
        "state_counts_match_manifest": state_total_matches_manifest,
        "scoring_fingerprints": bool(score_provenance.get("ok")),
        "onemap_validation_same_bundle_fresh": onemap_gate_passed,
        "onemap_validation_waived": onemap_gate_waived,
        "vercel_root_directory": bool(vercel.get("root_directory_ok")),
        "infrastructure_readiness": not errors,
    }
    owner_approvals = ["production_deploy"]
    blocking_checks = {
        key: value
        for key, value in release_gate_checks.items()
        if key
        not in {
            "onemap_validation_same_bundle_fresh",
            "onemap_validation_waived",
        }
    }
    preapproval_gate_passed = all(blocking_checks.values()) and (
        onemap_gate_passed or onemap_gate_waived
    )
    release_gate_passed = preapproval_gate_passed and not owner_approvals
    if not preapproval_gate_passed:
        release_gate_status = "blocked"
    elif owner_approvals:
        release_gate_status = "waiting_on_owner_approval"
    else:
        release_gate_status = "passed"

    report: dict[str, Any] = {
        "ok": not errors,
        "release_gate_passed": release_gate_passed,
        "release_gate_status": release_gate_status,
        "release_gate_summary": {
            "active_bundle": bundle_state.get("bundle"),
            "manifest_path": freshness.get("bundle_manifest_path"),
            "state_counts": bundle_state.get("state_counts"),
            "static_artifact_validation": {
                "ok": validation.get("ok"),
                "errors": validation.get("errors", []),
                "warnings": validation.get("warnings", []),
            },
            "scoring_fingerprint_status": score_provenance,
            "onemap_validation": onemap_status,
            "vercel_root_directory": vercel.get("root_directory"),
            "checks": release_gate_checks,
            "unresolved_warnings": warnings,
            "required_owner_approvals": owner_approvals,
        },
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
            "score_provenance": score_provenance,
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
        "features": readiness_features(
            project_root / "qa",
            active_bundle=str(bundle_state.get("bundle") or ""),
            bundle_dir=bundle_dir,
        ),
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
    parser.add_argument(
        "--waive-onemap-validation",
        action="store_true",
        help="Record an owner waiver for a failed fresh same-bundle OneMap gate.",
    )
    parser.add_argument("--owner-approval-note", default="")
    args = parser.parse_args()

    ok, report = build_readiness_report(
        bundle_dir=args.bundle_dir,
        mode=args.mode,
        summary_path=args.summary,
        universe_path=args.universe,
        params_path=args.params,
        qa_path=args.qa,
        debug_path=args.debug,
        waive_onemap_validation=args.waive_onemap_validation,
        owner_approval_note=args.owner_approval_note,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
