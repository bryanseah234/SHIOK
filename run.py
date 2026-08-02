#!/usr/bin/env python3
"""S.H.I.O.K. task runner (cross-platform replacement for make).

Usage: python run.py <task> [options]
Tasks: batch-plan | bus-arrivals | bus-connector-diagnostics | check | compare-targeted | ingest | network | network-preflight | network-qa | onemap-validation | onemap-outlier-replay | onemap-outlier-triage | overture-addresses | readiness | route | score | score-batch | postal-universe | geocode-universe | export | export-transit | validate | publish | test | shell
`publish` ALWAYS runs `validate` first — this gate is hard-coded and must never be removed.
Stubs below are replaced task-by-task per docs/BUILD_PLAN.md.
"""

import argparse
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

STUBS = {
    "check": "fetch listings, hash, diff vs manifest (T0.3)",
    "ingest": "download changed sources to raw/ (T0.3)",
    "network": "build conflated graph + QA report (T1.1)",
    "network-preflight": "verify network build inputs without building graph",
    "network-qa": "validate conflation QA report acceptance gates",
    "onemap-validation": "plan/evaluate OneMap walk-routing launch validation gate",
    "onemap-outlier-replay": "replay OneMap validation outliers through current local scoring",
    "onemap-outlier-triage": "build QA queues from profiled OneMap outlier replays",
    "overture-addresses": "probe Overture Addresses SG postal-universe candidate",
    "readiness": "fast production-readiness report without scoring or deploying",
    "route": "igraph dual-weight batch, spawn-safe multiprocessing (T1.2)",
    "score": "apply pipeline/config/weights.yaml (T1.4)",
    "score-batch": "resumable postal scoring batch runner",
    "bus-arrivals": "collect local LTA bus-arrival snapshots for future reliability scoring",
    "bus-connector-diagnostics": "diagnose priority OneMap missing-bus connector cases",
    "compare-targeted": "compare a targeted score report against the active bundle",
    "batch-plan": "dry-run full postal geocode/scoring batch plan (checkpoint C)",
    "postal-universe": "build deterministic postal-code universe candidates",
    "geocode-universe": "bounded OneMap geocode fill for source-derived postal gaps",
    "export": "scores/{area}.json + geom/h3/{cell}.json + manifest (T1.5)",
    "export-transit": "refresh transit POIs without rescoring",
    "validate": "golden set + OneMap comparison; blocks publish (T1.7)",
    "publish": "vercel deploy --prod --archive=tgz (only deploy path)",
    "test": "pytest (T0.1)",
    "shell": "not needed on native Windows; use your activated venv",
}


def run_task(name: str, extra: list[str]) -> int:
    def run_module(module: str, module_args: list[str] | None = None) -> int:
        cmd = [sys.executable, "-m", module] + (module_args or []) + extra
        return subprocess.run(cmd, check=False).returncode

    if name == "batch-plan":
        return run_module("pipeline.batch_plan")
    if name == "publish":
        return run_module("pipeline.publish")
    if name == "test":
        return run_module("pytest")
    if name in ("check", "ingest"):
        return run_module("pipeline.fetch", [name])
    if name == "network":
        return run_module("pipeline.network")
    if name == "network-preflight":
        return run_module("pipeline.network_preflight")
    if name == "network-qa":
        return run_module("pipeline.network_qa")
    if name == "onemap-validation":
        return run_module("pipeline.onemap_validation")
    if name == "onemap-outlier-replay":
        return run_module("scripts.replay_onemap_outliers")
    if name == "onemap-outlier-triage":
        return run_module("scripts.triage_onemap_outliers")
    if name == "overture-addresses":
        return run_module("pipeline.overture_addresses")
    if name == "readiness":
        return run_module("scripts.production_readiness")
    if name == "score":
        return run_module("pipeline.scoring_integration")
    if name == "score-batch":
        return run_module("pipeline.score_batch")
    if name == "bus-arrivals":
        return run_module("pipeline.bus_arrivals")
    if name == "bus-connector-diagnostics":
        return run_module("scripts.diagnose_bus_connectors")
    if name == "compare-targeted":
        return run_module("scripts.compare_targeted_scores")
    if name == "postal-universe":
        return run_module("pipeline.postal_universe")
    if name == "geocode-universe":
        return run_module("pipeline.geocode_universe")
    if name == "export":
        return run_module("pipeline.export", ["export"])
    if name == "export-transit":
        return run_module("pipeline.export", ["export-transit"])
    if name == "validate":
        return run_module("pipeline.export", ["validate"])

    print(f"not implemented: {name} — {STUBS[name]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__)
    parser.add_argument("task", choices=sorted(STUBS))
    args, extra = parser.parse_known_args()
    return run_task(args.task, extra)


if __name__ == "__main__":
    raise SystemExit(main())
