#!/usr/bin/env python3
"""S.H.I.O.K. task runner (cross-platform replacement for make).

Usage: python run.py <task> [options]
Tasks: check | ingest | network | route | score | export | validate | publish | test | shell
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
    "route": "igraph dual-weight batch, spawn-safe multiprocessing (T1.2)",
    "score": "apply pipeline/config/weights.yaml (T1.4)",
    "export": "scores/{area}.json + geom/h3/{cell}.json + manifest (T1.5)",
    "validate": "golden set + OneMap comparison; blocks publish (T1.7)",
    "publish": "vercel deploy --prod --archive=tgz (only deploy path)",
    "test": "pytest (T0.1)",
    "shell": "not needed on native Windows; use your activated venv",
}


def run_task(name: str, extra: list[str]) -> int:
    if name == "publish":
        rc = run_task("validate", [])
        if rc != 0:
            print("publish blocked: validate failed", file=sys.stderr)
            return rc
    if name == "test":
        cmd = [sys.executable, "-m", "pytest"] + extra
        res = subprocess.run(cmd)
        return res.returncode

    print(f"not implemented: {name} — {STUBS[name]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__)
    parser.add_argument("task", choices=sorted(STUBS))
    args, extra = parser.parse_known_args()
    return run_task(args.task, extra)


if __name__ == "__main__":
    raise SystemExit(main())
