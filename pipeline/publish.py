from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from pipeline.export import DEFAULT_VALIDATE_DIR, validate_static_artifacts

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"


def command_name(name: str) -> str:
    return f"{name}.cmd" if os.name == "nt" else name


def run_command(cmd: list[str], cwd: Path) -> dict[str, Any]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return {
        "cmd": cmd,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "ok": result.returncode == 0,
    }


def compact_command_report(
    command_report: dict[str, Any], *, keep_stdout: bool = False
) -> dict[str, Any]:
    summary = dict(command_report)
    stdout = str(summary.pop("stdout", ""))
    stderr = str(summary.pop("stderr", ""))
    summary["stdout_tail"] = stdout.splitlines()[-20:]
    summary["stderr_tail"] = stderr.splitlines()[-20:]
    if keep_stdout:
        summary["stdout"] = stdout
    return summary


def load_vercel_link(web_dir: Path) -> dict[str, Any]:
    for filename in ("project.json", "repo.json"):
        path = web_dir / ".vercel" / filename
        if path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                payload: Any = json.load(f)
            return {"linked": True, "path": str(path), "payload": payload}
    return {"linked": False, "path": None, "payload": None}


def deploy_command(web_dir: Path) -> list[str]:
    return [
        command_name("vercel"),
        "deploy",
        str(web_dir),
        "--prod",
        "--archive=tgz",
        "--yes",
        "--no-wait",
    ]


def summarize_audit(command_report: dict[str, Any]) -> dict[str, Any]:
    summary = dict(command_report)
    try:
        payload = json.loads(command_report.get("stdout") or "{}")
        summary["vulnerabilities"] = payload.get("metadata", {}).get("vulnerabilities", {})
        summary["stdout"] = ""
    except json.JSONDecodeError:
        summary["vulnerabilities"] = None
    return summary


def publish_preflight(
    input_dir: Path = DEFAULT_VALIDATE_DIR,
    web_dir: Path = WEB_DIR,
    *,
    run_external_checks: bool = True,
) -> tuple[bool, dict[str, Any]]:
    validation_ok, validation_report = validate_static_artifacts(input_dir=input_dir)
    report: dict[str, Any] = {
        "ok": False,
        "mode": "preflight",
        "input_dir": str(input_dir),
        "web_dir": str(web_dir),
        "validation": validation_report,
        "vercel": load_vercel_link(web_dir),
        "checks": {},
        "deploy_command": deploy_command(web_dir),
        "deploy_executed": False,
    }

    if not validation_ok:
        report["errors"] = ["publish blocked: static artifact validation failed"]
        return False, report

    if not run_external_checks:
        linked = bool(report["vercel"].get("linked"))
        report["ok"] = linked
        report["errors"] = [] if linked else ["publish blocked: Vercel project is not linked"]
        return bool(report["ok"]), report

    audit = summarize_audit(run_command([command_name("npm"), "audit", "--json"], cwd=web_dir))
    build = compact_command_report(run_command([command_name("npm"), "run", "build"], cwd=web_dir))
    whoami = compact_command_report(
        run_command([command_name("vercel"), "whoami"], cwd=web_dir),
        keep_stdout=True,
    )

    report["checks"] = {
        "npm_audit": audit,
        "npm_build": build,
        "vercel_whoami": whoami,
    }

    errors: list[str] = []
    if not report["vercel"].get("linked"):
        errors.append("publish blocked: Vercel project is not linked")
    if not audit["ok"]:
        errors.append("publish blocked: npm audit failed")
    else:
        total = audit.get("vulnerabilities", {}).get("total")
        if total not in (0, None):
            errors.append(f"publish blocked: npm audit reports {total} vulnerabilities")
    if not build["ok"]:
        errors.append("publish blocked: npm build failed")
    if not whoami["ok"]:
        errors.append("publish blocked: Vercel CLI is not authenticated")

    report["errors"] = errors
    report["ok"] = not errors
    return bool(report["ok"]), report


def publish_production(
    input_dir: Path, web_dir: Path, confirm: bool
) -> tuple[bool, dict[str, Any]]:
    ok, report = publish_preflight(input_dir=input_dir, web_dir=web_dir)
    report["mode"] = "production"
    if not ok:
        return False, report
    if not confirm:
        report["ok"] = False
        report["errors"] = ["production deploy requires --confirm-production"]
        return False, report

    result = run_command(deploy_command(web_dir), cwd=web_dir)
    report["deploy_executed"] = True
    report["deploy_result"] = result
    report["ok"] = result["ok"]
    report["errors"] = [] if result["ok"] else ["vercel production deploy failed"]
    return bool(report["ok"]), report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and publish the static web site.")
    parser.add_argument("--input", type=Path, default=DEFAULT_VALIDATE_DIR)
    parser.add_argument("--web-dir", type=Path, default=WEB_DIR)
    parser.add_argument("--deploy", action="store_true", help="Run the production deploy.")
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Required with --deploy to actually create a production deployment.",
    )
    parser.add_argument(
        "--skip-external-checks",
        action="store_true",
        help="Skip npm/Vercel command checks. Intended only for unit tests.",
    )
    args = parser.parse_args()

    if args.deploy:
        ok, report = publish_production(
            input_dir=args.input,
            web_dir=args.web_dir,
            confirm=bool(args.confirm_production),
        )
    else:
        ok, report = publish_preflight(
            input_dir=args.input,
            web_dir=args.web_dir,
            run_external_checks=not args.skip_external_checks,
        )

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
